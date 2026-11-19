import io
import uuid
from decimal import Decimal

from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from django.utils import timezone
from django.test.utils import CaptureQueriesContext
from django.db import connection
from rest_framework.exceptions import ValidationError

from orders.models import ProductSpecification, ProductInspectionCriterion
from quality.models import QualityOrder, QualityShipmentBatch, QualityShipmentLine, ProcessCard
from quality.services import delivered_quantities_by_order
from quality.tests.helpers import QualityTestMixin
from . import services as s
from .models import CardGroup, InternalCard, CardEvent, PrintJob, AppearanceIssue, ShipmentSource, StandardRevision


@override_settings(FLOW_CARDS_WRITE_ENABLED=True)
class FlowCardTests(QualityTestMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.product = ProductSpecification.objects.create(specification="215", material="N7200")
        cls.order.product_specification = cls.product
        cls.order.specification = cls.product.specification
        cls.order.material = cls.product.material
        cls.order.source_system = "翔邦"
        cls.order.save()

    def plan(self, **changes):
        return {"request_key": uuid.uuid4().hex, "product_id": self.product.pk,
                "order_ids": [self.order.pk], "planned_molds": 2050, "molds_per_card": 100,
                "whole_mold": "REQUIRED", "whole_mold_basis": "现场确认", "plan_basis": "本次计划0至2050模", "append_confirmed": True, **changes}

    def generated(self, **changes):
        return s.generate_group(s.create_group(self.plan(**changes), self.user), self.user)

    def event(self, card, **changes):
        return {"request_key": uuid.uuid4().hex, "kind": "WHOLE_MOLD", "card_ids": [str(card.pk)],
                "values": {"status": "PARTIAL", "fixed_positions": None},
                "scope": "本包前20模", "evidence": "原始纸卡", **changes}

    def test_A02_draft_without_molds_never_creates_production(self):
        from production.models import ProductionRun, ProductionDailyLog
        counts = (ProductionRun.objects.count(), ProductionDailyLog.objects.count())
        group = s.create_group(self.plan(planned_molds=None), self.user)
        self.assertEqual(group.status, "DRAFT")
        self.assertEqual(group.cards.count(), 0)
        with self.assertRaises(ValidationError):
            s.generate_group(group, self.user)
        self.assertEqual(counts, (ProductionRun.objects.count(), ProductionDailyLog.objects.count()))

    def test_A02_draft_can_be_completed_once_but_generated_snapshot_cannot_change(self):
        draft = s.create_group(self.plan(planned_molds=None), self.user)
        body = self.plan(request_key="REVISE-PREPARED", planned_molds=80)
        changed = s.revise_draft(draft, body, self.user)
        self.assertEqual(changed.pk, draft.pk)
        self.assertEqual(s.revise_draft(draft, body, self.user).planned_molds, 80)
        card = s.generate_group(changed, self.user).cards.get()
        self.assertEqual(card.planned_molds, 80)
        with self.assertRaisesMessage(ValidationError, "快照不能修改"):
            s.revise_draft(changed, {**body, "request_key": "ANOTHER-REVISION", "planned_molds": 100}, self.user)

    def test_A03_A04_split_tail_unique_and_idempotent(self):
        for molds, count, tail in [(2000, 20, 100), (2050, 21, 50)]:
            body = self.plan(planned_molds=molds)
            group = s.create_group(body, self.user)
            self.assertEqual(s.create_group(body, self.user).pk, group.pk)
            s.generate_group(group, self.user)
            s.generate_group(group, self.user)
            cards = list(group.cards.all())
            self.assertEqual(len(cards), count)
            self.assertEqual(sum(c.planned_molds for c in cards), molds)
            self.assertEqual(cards[-1].planned_molds, tail)
            self.assertEqual(len({c.card_no for c in cards}), count)
            self.assertEqual(cards[2].denominator, count)
        self.assertEqual(InternalCard.objects.count(), 41)

    def test_A03_reused_request_different_body_rejected(self):
        body = self.plan()
        s.create_group(body, self.user)
        with self.assertRaises(ValidationError):
            s.create_group({**body, "planned_molds": 2000}, self.user)

    def test_A04_existing_group_requires_explicit_append_not_reprint(self):
        original = self.generated()
        with self.assertRaisesMessage(ValidationError, "已有卡组"):
            s.create_group(self.plan(append_confirmed=False), self.user)
        added = self.generated(planned_molds=50, plan_basis="明确追加2051至2100模")
        self.assertEqual(added.cards.count(), 1)
        self.assertEqual(original.cards.first().denominator, 21)

    def test_A01_A06_conflicting_order_requirements_need_review(self):
        original = ProductInspectionCriterion.objects.create(product_specification=self.product, inspection_item="硬度", lower_limit="65", upper_limit="75", unit="Shore A")
        ProductInspectionCriterion.objects.create(product_specification=self.product, order=self.order, inspection_item="硬度", lower_limit="68", upper_limit="72", unit="Shore A")
        standard = s.confirm_standard({"request_key": "CONFLICT-STANDARD", "product_id": self.product.pk, "source": "已核对原产品要求", "appearance_ids": list(AppearanceIssue.objects.values_list("pk", flat=True)),
            "criteria": [{"criterion_id": original.pk, "stage": "MOLDING_HARDNESS", "method": "硬度计", "numeric": True}]}, self.user)
        group = s.create_group(self.plan(standard_id=standard.pk), self.user)
        self.assertEqual(len(group.snapshot["requirement_warnings"]), 2)
        with self.assertRaisesMessage(ValidationError, "存在差异"):
            s.generate_group(group, self.user)
        reviewed = self.generated(standard_id=standard.pk, requirements_review="本单按已核对客户范围另行确认；保留选择依据")
        self.assertTrue(reviewed.snapshot["requirements_review"])

    def test_A08_appearance_library_group_choice_and_old_snapshot(self):
        group = self.generated()
        old = group.snapshot["appearance"][0]
        response = self.client.post("/api/flow-cards/appearance/", {"id": old["id"], "name": "改名后的问题"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        created = self.client.post("/api/flow-cards/appearance/", {"name": "新问题"}, format="json")
        ids = [created.json()["id"], *[x["id"] for x in group.snapshot["appearance"]][1:]]
        later = self.generated(appearance_ids=ids)
        self.assertEqual(later.snapshot["appearance"][0]["name"], "新问题")
        duplicate = self.client.post("/api/flow-cards/appearance/", {"name": "新问题"}, format="json")
        self.assertEqual(duplicate.status_code, 400)
        group.refresh_from_db()
        self.assertEqual(group.snapshot["appearance"][0], old)

    def test_A01_A28_bake_conditions_and_more_dimensions_have_continuation(self):
        from .pdf import render_cards
        standard = s.confirm_standard({"request_key": "BAKE-STANDARD", "product_id": self.product.pk, "source": "隔离确认", "appearance_ids": list(AppearanceIssue.objects.values_list("pk", flat=True)),
            "bake": {"state": "APPLICABLE", "temperature_lower": 180, "temperature_upper": 190, "minutes_lower": 120, "minutes_upper": 150},
            "criteria": [{"name": f"尺寸{i}", "lower": "1", "upper": "2", "unit": "mm", "method": "已确认量具", "stage": "DIMENSION", "numeric": True} for i in range(3)]}, self.user)
        card = self.generated(standard_id=standard.pk, planned_molds=100).cards.first()
        raw = render_cards([(card, 0)])
        self.assertEqual(raw.count(b"/Type /Page\n"), 2)
        self.assertEqual(card.group.snapshot["standard"]["snapshot"]["bake"]["minutes_lower"], 120)

    def test_A09_bad_payloads_are_validation_errors_not_server_errors(self):
        for endpoint, body in [("groups/", []), ("groups/", self.plan(order_ids=["bad"])), ("print-jobs/", {"request_key": "BAD-UUID", "card_ids": ["bad"]}), ("events/", {"request_key": "BAD-UUID2", "kind": "ANOMALY", "card_ids": ["bad"]})]:
            with self.subTest(endpoint=endpoint):
                result = self.client.post("/api/flow-cards/" + endpoint, body, format="json")
                self.assertEqual(result.status_code, 400, result.content)

    def test_A05_merged_orders_are_source_set_no_quantity(self):
        order = QualityOrder.objects.create(order_no="SECOND", specification="215", material="N7200", product_specification=self.product,
            source_system="翔邦", order_quantity=99, created_by=self.user)
        group = self.generated(order_ids=[self.order.pk, order.pk])
        self.assertEqual(set(group.orders.values_list("id", flat=True)), {self.order.pk, order.pk})
        self.assertEqual(delivered_quantities_by_order([self.order.pk, order.pk]), {self.order.pk: 0, order.pk: 0})

    def test_A06_same_text_different_product_rejected(self):
        other = ProductSpecification.objects.create(specification="215", material="N7200", product_name="另一产品")
        with self.assertRaises(ValidationError):
            s.create_group(self.plan(product_id=other.pk), self.user)

    def test_A06_cross_customer_rejected_not_same_standard(self):
        other = QualityOrder.objects.create(order_no="OTHER", specification="215", material="N7200", product_specification=self.product,
            source_system="艾斯霖", order_quantity=9, created_by=self.user)
        with self.assertRaises(ValidationError):
            s.create_group(self.plan(order_ids=[self.order.pk, other.pk]), self.user)

    def test_A08_standard_reuses_original_criteria_and_freezes(self):
        criterion = ProductInspectionCriterion.objects.create(product_specification=self.product, inspection_item="上模探头",
            lower_limit="170", upper_limit="180", unit="℃")
        standard = s.confirm_standard({"request_key": "STANDARD-1", "product_id": self.product.pk, "source": "现场确认原要求", "whole_mold": "REQUIRED",
            "appearance_ids": list(AppearanceIssue.objects.values_list("pk", flat=True)),
            "criteria": [{"criterion_id": criterion.pk, "stage": "UPPER_TEMP", "method": "接触探头", "numeric": True}]}, self.user)
        group = self.generated(standard_id=standard.pk)
        criterion.upper_limit = "190"
        criterion.save()
        AppearanceIssue.objects.update(name="修改后的名称")
        group.refresh_from_db()
        saved = group.snapshot["standard"]["snapshot"]
        self.assertEqual(saved["criteria"][0]["upper"], "180")
        self.assertNotEqual(saved["appearance"][0]["name"], "修改后的名称")
        self.assertEqual(ProductInspectionCriterion.objects.count(), 1)

    def test_A09_A10_real_zero_unknown_separate_no_guessed_limits(self):
        card = self.generated().cards.first()
        for state, actual in [("MEASURED", 0), ("NOT_MEASURED", None), ("NOT_APPLICABLE", None), ("UNKNOWN", None)]:
            event = s.record_event(self.event(card, kind="UPPER_TEMP", business_at=timezone.now().isoformat(), employee_id=self.inspector.pk,
                values={"state": state, "actual": actual}), self.user)
            self.assertEqual(event.values["state"], state)
            if state == "MEASURED":
                self.assertEqual(event.values["actual"], "0")
                self.assertEqual(event.values["judgment"], "NOT_EVALUATED")
        with self.assertRaises(ValidationError):
            s.record_event(self.event(card, kind="UPPER_TEMP", values={"state": "MEASURED", "actual": None}), self.user)

    def test_A09a_source_is_plan_until_explicit_confirmation(self):
        card = self.generated().cards.first()
        response = self.client.get(f"/api/flow-cards/cards/{card.pk}/")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["source_status"], "PLAN_ONLY")
        s.record_event(self.event(card, kind="SOURCE_CONFIRM", values={"confirmed": True}, employee_id=self.inspector.pk, business_at=timezone.now().isoformat()), self.user)
        self.assertEqual(self.client.get(f"/api/flow-cards/cards/{card.pk}/").json()["source_status"], "PHYSICAL_CONFIRMED")

    def test_A11_A12_A13_print_unknown_partial_retry_reprint(self):
        group = self.generated(planned_molds=200)
        cards = list(group.cards.all())
        body = {"request_key": "PRINT-1", "card_ids": [str(c.pk) for c in cards]}
        job = s.create_print(body, self.user)
        self.assertEqual(s.create_print(body, self.user).pk, job.pk)
        for c in cards:
            c.refresh_from_db(); self.assertIsNone(c.printed_at)
        with self.assertRaises(ValidationError):
            s.create_print({**body, "request_key": "RACING"}, self.user)
        outcomes = {str(cards[0].pk): "SUCCESS", str(cards[1].pk): "UNKNOWN"}
        s.confirm_print(job, {"outcomes": outcomes}, self.user)
        s.confirm_print(job, {"outcomes": outcomes}, self.user)
        self.assertTrue(job.items.get(card=cards[1]).active)
        s.confirm_print(job, {"outcomes": {str(cards[1].pk): "SUCCESS"}}, self.user)
        for count in [1, 2]:
            job2 = s.create_print({"request_key": f"REPRINT-{count}", "card_ids": [str(cards[0].pk)]}, self.user)
            self.assertEqual(job2.items.get().proposed_reprint, count)
            s.confirm_print(job2, {"outcomes": {str(cards[0].pk): "SUCCESS"}}, self.user)
        cards[0].refresh_from_db(); cards[1].refresh_from_db()
        self.assertEqual(cards[0].reprint_count, 2)
        self.assertEqual(cards[1].reprint_count, 0)

    def test_A14_internal_scan_never_customer_unknown_creation(self):
        card = self.generated().cards.first()
        result = self.client.get("/api/flow-cards/cards/scan/", {"code": f"ERPFC:{card.pk}"})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["type"], "INTERNAL")
        result = self.client.get("/api/quality/process-cards/scan/", {"code": f"ERPFC:{card.pk}"})
        self.assertEqual(result.status_code, 400)
        self.assertNotIn("binding_required", result.json())
        self.assertEqual(ProcessCard.objects.count(), 0)

    def test_A25_write_guard_on_every_endpoint(self):
        self.user.groups.add(Group.objects.get_or_create(name="GPT只读测试")[0])
        endpoints = ["groups/", "groups/preview/", "standards/", "appearance/", "events/", "print-jobs/", "templates/publish/", "attachments/", "diagrams/", "shipments/1/sources/"]
        for endpoint in endpoints:
            with self.subTest(endpoint=endpoint):
                response = self.client.post("/api/flow-cards/"+endpoint, {}, format="json")
                self.assertEqual(response.status_code, 403, response.content)

    @override_settings(FLOW_CARDS_WRITE_ENABLED=False)
    def test_A27_pilot_off_keeps_reads_rejects_writes(self):
        response = self.client.post("/api/flow-cards/groups/", self.plan(), format="json")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.get("/api/flow-cards/groups/").status_code, 200)

    def test_A29_unknown_whole_requires_confirmation(self):
        group = s.create_group(self.plan(whole_mold="UNKNOWN"), self.user)
        with self.assertRaises(ValidationError):
            s.generate_group(group, self.user)
        self.assertEqual(group.cards.count(), 0)

    def test_A32_fixed_positions_values(self):
        card = self.generated(confirmed_total_cavities=400, cavities_basis="实物数孔").cards.first()
        for value in [0, None, 4]:
            event = s.record_event(self.event(card, values={"status": "PARTIAL", "fixed_positions": value}), self.user)
            self.assertEqual(event.values["fixed_positions"], value)
        for value in [-1, 0.5, 401, True]:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                s.record_event(self.event(card, values={"status": "PARTIAL", "fixed_positions": value}), self.user)

    def test_A33_A34_record_never_changes_cavities_or_quantity(self):
        from production.models import ProductionRun, ProductionDailyLog
        run = ProductionRun.objects.create(order=self.order, product_specification=self.product, order_no=self.order.order_no,
            specification="215", material="N7200", cavities=396, planned_mold_count=2050, order_quantity=1000,
            status="COMPLETED", is_ledger_only=True, created_by=self.user)
        log = ProductionDailyLog.objects.create(run=run, produced_mold_count=100, cavities_snapshot=396)
        group = self.generated(run_id=run.pk, confirmed_total_cavities=400, cavities_basis="实物400；有效396已含4定点")
        card = group.cards.first()
        before = dict(group.snapshot)
        for fixed, scope in [(4, "本包前半，4点已含"), (6, "本包后半新增2，位置待核对")]:
            s.record_event(self.event(card, scope=scope, values={"status": "PARTIAL", "fixed_positions": fixed}), self.user)
        group.refresh_from_db()
        self.assertEqual(group.snapshot, before)
        self.assertEqual(group.planned_molds, 2050)
        self.assertEqual(QualityShipmentBatch.objects.count(), 0)
        self.assertEqual(card.events.count(), 2)
        run.refresh_from_db(); log.refresh_from_db()
        self.assertEqual(run.cavities, 396)
        self.assertEqual(log.cavities_snapshot, 396)
        self.assertEqual(group.snapshot["effective_cavities"], 396)
        self.assertEqual(log.produced_mold_count, 100)

    def test_A20_shared_measurement_one_record_not_one_per_card(self):
        cards = list(self.generated(planned_molds=200).cards.all())
        data = self.event(cards[0], kind="UPPER_TEMP", card_ids=[str(c.pk) for c in cards], employee_id=self.inspector.pk,
            business_at=timezone.now().isoformat(), values={"state": "MEASURED", "actual": "175"}, scope="明确同一次覆盖这两包")
        event = s.record_event(data, self.user)
        self.assertEqual(CardEvent.objects.count(), 1)
        self.assertEqual(event.cards.count(), 2)

    def test_A21_A23_photo_original_is_private_and_not_completion(self):
        import tempfile
        from pathlib import Path
        from PIL import Image
        from django.core.files.uploadedfile import SimpleUploadedFile
        card = self.generated().cards.first()
        image = io.BytesIO(); Image.new("RGB", (10, 10), "white").save(image, format="PNG")
        with tempfile.TemporaryDirectory() as directory, override_settings(FLOW_CARDS_PRIVATE_ROOT=Path(directory)):
            result = self.client.post("/api/flow-cards/attachments/", {"card_id": str(card.pk), "file": SimpleUploadedFile("original.png", image.getvalue(), content_type="image/png")}, format="multipart")
            self.assertEqual(result.status_code, 201, result.content)
            identity = result.json()["id"]
            original = self.client.get(f"/api/flow-cards/attachments/{identity}/content/")
            self.assertEqual(original.status_code, 200)
            self.assertEqual(b"".join(original.streaming_content), image.getvalue())
            self.client.force_authenticate(None)
            self.assertIn(self.client.get(f"/api/flow-cards/attachments/{identity}/content/").status_code, [401, 403])
            self.client.force_authenticate(self.user)
        self.assertEqual(self.client.get(f"/api/flow-cards/cards/{card.pk}/").json()["whole_mold_status"], "SYSTEM_UNREGISTERED")
        self.assertEqual(card.events.count(), 0)

    def test_A37_diagram_actual_mold_identity_and_version(self):
        from molds.models import MoldAsset, MoldModel
        from .models import Attachment, PositionDiagram
        model = MoldModel.objects.create(code="TEST-DIAGRAM")
        mold1 = MoldAsset.objects.create(asset_code="M1", mold_model=model, status="OUTSOURCED")
        mold2 = MoldAsset.objects.create(asset_code="M2", mold_model=model, status="OUTSOURCED")
        attachment = Attachment.objects.create(created_by=self.user, storage_name="unread-test-original", original_name="test.png", sha256="a"*64, content_type="image/png")
        diagram = PositionDiagram.objects.create(product=self.product, mold=mold1, version=1, attachment=attachment, direction="箭头朝前", scope="本实物", positions="4个位置", created_by=self.user)
        group = self.generated(mold_id=mold1.pk, diagram_id=diagram.pk)
        self.assertEqual(group.diagram_id, diagram.pk)
        with self.assertRaises(ValidationError):
            self.generated(mold_id=mold2.pk, diagram_id=diagram.pk)

    def test_A35_A36_completion_requires_real_person_time_whole_package(self):
        card = self.generated().cards.first()
        with self.assertRaises(ValidationError):
            s.record_event(self.event(card, values={"status": "COMPLETED", "fixed_positions": 4}), self.user)
        s.record_event(self.event(card, kind="ANOMALY", values={"note": "新定点需核对"}), self.user)
        event = s.record_event(self.event(card, employee_id=self.inspector.pk, business_at=timezone.now().isoformat(), scope="整包",
            values={"status": "COMPLETED", "coverage": "WHOLE", "fixed_positions": 4, "whole_package_confirmed": True}), self.user)
        self.assertEqual(event.employee_name, self.inspector.name)
        response = self.client.get(f"/api/flow-cards/cards/{card.pk}/").json()
        self.assertEqual(response["whole_mold_status"], "COMPLETED")
        self.assertEqual(response["unresolved_anomalies"], 1)
        self.assertEqual(event.created_by_id, self.user.pk)

    def test_A35_resolution_requires_actual_confirm_and_whole_scope_is_explicit(self):
        card = self.generated().cards.first()
        anomaly = s.record_event(self.event(card, kind="ANOMALY", values={"note": "待处理"}), self.user)
        with self.assertRaises(ValidationError):
            s.record_event(self.event(card, kind="RESOLUTION", values={"anomaly_id": str(anomaly.pk), "result": "仅备注"}), self.user)
        with self.assertRaises(ValidationError):
            s.record_event(self.event(card, employee_id=self.inspector.pk, business_at=timezone.now().isoformat(), values={"status": "COMPLETED", "coverage": "PARTIAL", "whole_package_confirmed": True}), self.user)
        s.record_event(self.event(card, kind="RESOLUTION", employee_id=self.inspector.pk, business_at=timezone.now().isoformat(), values={"anomaly_id": str(anomaly.pk), "result": "实际复核处理完成", "resolved": True}), self.user)
        self.assertEqual(self.client.get(f"/api/flow-cards/cards/{card.pk}/").json()["unresolved_anomalies"], 0)

    def test_A38_retry_and_correction_preserve_records(self):
        card = self.generated().cards.first()
        body = self.event(card)
        original = s.record_event(body, self.user)
        self.assertEqual(s.record_event(body, self.user).pk, original.pk)
        s.record_event(self.event(card, replaces_id=str(original.pk), values={"status": "PARTIAL", "fixed_positions": 0}), self.user)
        self.assertEqual(card.events.count(), 2)
        original.refresh_from_db()
        self.assertIsNone(original.values["fixed_positions"])

    def test_A28_A30_pdf_snapshot_size_one_page_per_card(self):
        from .pdf import render_cards
        for requirement in ["REQUIRED", "NOT_REQUIRED"]:
            group = self.generated(planned_molds=100, whole_mold=requirement)
            raw = render_cards([(group.cards.first(), 0)])
            self.assertTrue(raw.startswith(b"%PDF"))
            self.assertIn(b"/Type /Page", raw)
            # Count actual page nodes, not the /Pages tree node.
            self.assertEqual(raw.count(b"/Type /Page\n"), 1)

    def test_A11_A31_pdf_download_is_frozen_and_get_is_read_only(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from .models import TraceAudit
        card = self.generated(planned_molds=100).cards.first()
        with tempfile.TemporaryDirectory() as directory, override_settings(FLOW_CARDS_PRIVATE_ROOT=Path(directory)):
            body = {"request_key": "FREEZE-PDF", "card_ids": [str(card.pk)]}
            response = self.client.post("/api/flow-cards/print-jobs/", body, format="json")
            self.assertEqual(response.status_code, 201, response.content)
            url = response.json()["pdf_url"]
            first = self.client.get(url)
            original = b"".join(first.streaming_content)
            audit_count = TraceAudit.objects.count()
            self.product.product_name = "修改后产品"
            self.product.save()
            with patch("flowcards.pdf.render_cards", side_effect=AssertionError("GET must not re-render")):
                self.assertEqual(b"".join(self.client.get(url).streaming_content), original)
            card.refresh_from_db()
            self.assertIsNone(card.printed_at)
            self.assertEqual(card.reprint_count, 0)
            self.assertEqual(TraceAudit.objects.count(), audit_count)
            retry = self.client.post("/api/flow-cards/print-jobs/", body, format="json")
            self.assertEqual(retry.status_code, 201)
            self.assertEqual(retry.json()["id"], response.json()["id"])

    def test_A26_scan_constant_queries_shared_data_not_history(self):
        group = self.generated(planned_molds=10000)
        card = group.cards.first()
        for _ in range(30):
            s.record_event(self.event(card), self.user)
        with CaptureQueriesContext(connection) as queries:
            result = self.client.get("/api/flow-cards/cards/scan/", {"code": f"ERPFC:{card.pk}"})
        self.assertEqual(result.status_code, 200)
        self.assertLessEqual(len(queries), 4)
        self.assertNotIn("events", result.json())
        self.assertLess(len(result.content), 1500)

    def draft_shipment(self):
        response = self.client.post("/api/quality/shipment-batches/", {"order_id": self.order.pk, "unit_weight_g": "10", "single_batch_net_weight_kg": "1",
            "process_card_shipment_quantity": 100, "product_batch_count": 1, "lines": [{"order_id": self.order.pk}]}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()["id"]

    def test_A15_A16_A17_sources_stable_after_line_rebuild(self):
        card = self.generated().cards.first()
        batch_id = self.draft_shipment()
        source = {"card_id": str(card.pk), "scope": "SET_UNKNOWN", "evidence": "人工核对来源集合", "request_key": "SOURCE-1"}
        confirmed = self.client.post(f"/api/quality/shipment-batches/{batch_id}/confirm/", {"internal_sources": [source]}, format="json")
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        original_line = QualityShipmentLine.objects.get(batch_id=batch_id).pk
        before = delivered_quantities_by_order([self.order.pk])
        self.client.post(f"/api/quality/shipment-batches/{batch_id}/confirm/", {"internal_sources": [source]}, format="json")
        self.assertEqual(ShipmentSource.objects.count(), 1)
        amended = self.client.post(f"/api/quality/shipment-batches/{batch_id}/amend/", {"amend_reason": "实际重量更正", "single_batch_net_weight_kg": "0.900"}, format="json")
        self.assertEqual(amended.status_code, 200, amended.content)
        self.assertNotEqual(QualityShipmentLine.objects.get(batch_id=batch_id).pk, original_line)
        association = ShipmentSource.objects.get()
        self.assertEqual(association.batch_id, batch_id)
        self.assertEqual(association.card_id, card.pk)
        self.assertEqual(association.state, "NEEDS_REVIEW")
        self.assertEqual(before[self.order.pk], 100)
        self.assertEqual(delivered_quantities_by_order([self.order.pk])[self.order.pk], 90)

    def test_A15_A16_many_sources_cross_order_and_reuse_do_not_multiply_delivery(self):
        self.order.order_quantity = 50
        self.order.save()
        other = QualityOrder.objects.create(order_no="ISOLATED-SECOND", item_no="2", specification="215", material="N7200", product_specification=self.product,
            source_system="翔邦", order_quantity=80, created_by=self.user)
        cards = list(self.generated(order_ids=[self.order.pk, other.pk], planned_molds=200).cards.all())
        batch_id = self.draft_shipment()
        sources = [{"card_id": str(card.pk), "scope": "SET_UNKNOWN", "request_key": f"MANY-{index}", "evidence": "明确本次涉及集合，不知道逐包份额"} for index, card in enumerate(cards)]
        response = self.client.post(f"/api/quality/shipment-batches/{batch_id}/confirm/", {"internal_sources": sources}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        actual = delivered_quantities_by_order([self.order.pk, other.pk])
        self.assertEqual(actual, {self.order.pk: 50, other.pk: 50})
        self.assertEqual(ShipmentSource.objects.filter(batch_id=batch_id).count(), 2)
        next_batch_id = self.draft_shipment()
        response = self.client.post(f"/api/quality/shipment-batches/{next_batch_id}/confirm/", {"internal_sources": [{**sources[0], "request_key": "REUSE-FIRST-CARD", "scope": "PARTIAL", "evidence": "本次再次使用原包部分，实际份额未计件"}]}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(sum(delivered_quantities_by_order([self.order.pk, other.pk]).values()), 200)
        self.assertEqual(ShipmentSource.objects.filter(card=cards[0]).count(), 2)

    def test_A17_source_review_keeps_original_evidence_and_no_quantity_effect(self):
        card = self.generated().cards.first()
        batch_id = self.draft_shipment()
        self.client.post(f"/api/quality/shipment-batches/{batch_id}/confirm/", {"internal_sources": [{"card_id": str(card.pk), "scope": "SET_UNKNOWN", "evidence": "原关联", "request_key": "REVIEW-ORIGINAL"}]}, format="json")
        original = ShipmentSource.objects.get()
        snapshot = dict(original.snapshot)
        before = delivered_quantities_by_order([self.order.pk])
        body = {"request_key": "REVIEW-NEW", "reason": "已按当前出货与实物重新核对", "action": "CONFIRM_SET"}
        result = self.client.post(f"/api/flow-cards/shipment-sources/{original.pk}/review/", body, format="json")
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual(self.client.post(f"/api/flow-cards/shipment-sources/{original.pk}/review/", body, format="json").status_code, 200)
        original.refresh_from_db()
        self.assertEqual(original.state, "SUPERSEDED")
        self.assertEqual(original.snapshot, snapshot)
        replacement = ShipmentSource.objects.get(pk=result.json()["replacement_id"])
        self.assertEqual(replacement.snapshot["replaces_source_id"], original.pk)
        self.assertEqual(ShipmentSource.objects.count(), 2)
        revoke = self.client.post(f"/api/flow-cards/shipment-sources/{replacement.pk}/review/", {"request_key": "REVOKE-NEW", "reason": "发现错误关联", "action": "REVOKE"}, format="json")
        self.assertEqual(revoke.status_code, 200, revoke.content)
        self.assertEqual(delivered_quantities_by_order([self.order.pk]), before)

    def test_A15_incomplete_customer_is_pending_not_a_fabricated_conflict(self):
        self.order.source_system = ""
        self.order.save()
        group = self.generated()
        self.assertIsNone(group.snapshot["orders"][0]["customer"])
        # Current shipment order has a now-confirmed customer; old source
        # snapshot stays unknown, so do not silently promote it to CONFIRMED.
        self.order.source_system = "翔邦"
        self.order.save()
        batch_id = self.draft_shipment()
        result = self.client.post(f"/api/quality/shipment-batches/{batch_id}/confirm/", {"internal_sources": [{"card_id": str(group.cards.first().pk), "request_key": "PENDING-CUSTOMER", "evidence": "先保留待核对集合"}]}, format="json")
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual(ShipmentSource.objects.get().state, "NEEDS_REVIEW")

    def test_A22_manual_stock_out_and_customer_shipment_are_separate_no_double_deduction(self):
        from inventory.models import InventoryLocation, InventoryContainer, InventoryTransaction
        location = InventoryLocation.objects.create(code="ISOLATED-L01-P01", rack_code="ISOLATED", rack_type="LARGE", level_no=1, position_no=1, allows_basket=True, allows_bag=True)
        received = self.client.post("/api/inventory/receipts/", {"product": {"product_code": "FLOW-215", "specification": "215", "material": "N7200"}, "location_id": location.pk,
            "container_type": "BASKET", "quality_status": "WAITING", "quantity": 200, "batch_no": "ISOLATED-STOCK"}, format="json")
        self.assertEqual(received.status_code, 201, received.content)
        cid = received.json()["id"]
        outbound = self.client.post("/api/inventory/outbounds/", {"lines": [{"container_id": cid, "quantity": 100}]}, format="json")
        self.assertEqual(outbound.status_code, 201, outbound.content)
        self.assertEqual(delivered_quantities_by_order([self.order.pk])[self.order.pk], 0)
        tx_before = InventoryTransaction.objects.count()
        card = self.generated().cards.first()
        batch_id = self.draft_shipment()
        result = self.client.post(f"/api/quality/shipment-batches/{batch_id}/confirm/", {"internal_sources": [{"card_id": str(card.pk), "request_key": "STOCK-SOURCE", "evidence": "原手工库存出库后按客户卡出货"}]}, format="json")
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual(InventoryContainer.objects.get(pk=cid).quantity, 100)
        self.assertEqual(InventoryTransaction.objects.count(), tx_before)
        self.assertEqual(delivered_quantities_by_order([self.order.pk])[self.order.pk], 100)
        self.assertEqual(InventoryContainer.objects.get(pk=cid).batch.quality_status, "WAITING")

    def test_A27_bad_source_rolls_back_entire_shipment_confirm(self):
        other = ProductSpecification.objects.create(specification="OTHER", material="NBR")
        other_order = QualityOrder.objects.create(order_no="OTHER", specification="OTHER", material="NBR", product_specification=other,
            source_system="翔邦", order_quantity=9, created_by=self.user)
        card = self.generated(product_id=other.pk, order_ids=[other_order.pk]).cards.first()
        batch_id = self.draft_shipment()
        response = self.client.post(f"/api/quality/shipment-batches/{batch_id}/confirm/", {"internal_sources": [{"card_id": str(card.pk), "request_key": "BAD", "evidence": "错误来源"}]}, format="json")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(QualityShipmentBatch.objects.get(pk=batch_id).status, "DRAFT")
        self.assertEqual(ShipmentSource.objects.count(), 0)
        self.assertEqual(delivered_quantities_by_order([self.order.pk]), {self.order.pk: 0})

    def test_A18_two_return_rounds_keep_original_sources_and_no_fake_rework(self):
        from quality.models import QualityReworkAttempt
        card = self.generated().cards.first()
        batch_id = self.draft_shipment()
        source = {"card_id": str(card.pk), "request_key": "SOURCE-ROUNDS", "evidence": "来源集合人工核对"}
        confirmed = self.client.post(f"/api/quality/shipment-batches/{batch_id}/confirm/", {
            "process_card_bindings": [{"shipment_unit_no": 1, "card_no": "DEMO-CUSTOMER-CARD-001", "order_id": self.order.pk}],
            "internal_sources": [source]}, format="json")
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        for expected_round in [1, 2]:
            returned = self.client.post("/api/quality/rework-cases/scan-return/", {"card_no": "DEMO-CUSTOMER-CARD-001", "reason": "隔离退回", "inspector_ids": [self.inspector.pk]}, format="json")
            self.assertEqual(returned.status_code, 201, returned.content)
            self.assertEqual(returned.json()["return_round"], expected_round)
            case_id = returned.json()["id"]
            reship = self.client.post(f"/api/quality/rework-cases/{case_id}/reship/", {"inspector_ids": [self.inspector.pk]}, format="json")
            self.assertEqual(reship.status_code, 201, reship.content)
        trace = self.client.get(f"/api/flow-cards/cards/{card.pk}/trace/")
        self.assertEqual(trace.status_code, 200, trace.content)
        self.assertEqual(len(trace.json()["results"][0]["related_returns"]), 2)
        self.assertEqual(ShipmentSource.objects.get().batch_id, batch_id)
        self.assertEqual(QualityReworkAttempt.objects.count(), 0)
        self.assertEqual(delivered_quantities_by_order([self.order.pk])[self.order.pk], 100)

    def test_A18_long_return_history_is_paginated_not_truncated(self):
        from quality.models import QualityReworkCase
        card = self.generated().cards.first()
        batch = QualityShipmentBatch.objects.create(status="CONFIRMED", created_by=self.user)
        source = ShipmentSource.objects.create(card=card, batch=batch, request_key="LONG-TRACE", evidence="隔离关联", created_by=self.user)
        for i in range(105):
            QualityReworkCase.objects.create(case_no=f"ISOLATED-CASE-{i}", shipment_batch=batch, created_by=self.user)
        result = self.client.get(f"/api/flow-cards/cards/{card.pk}/trace/").json()["results"][0]
        self.assertEqual(result["related_return_count"], 105)
        self.assertEqual(len(result["related_returns"]), 20)
        final = self.client.get(f"/api/flow-cards/cards/{card.pk}/trace-returns/", {"source_id": source.pk, "page": 6}).json()
        self.assertEqual(final["count"], 105)
        self.assertEqual(len(final["results"]), 5)


from django.test import TransactionTestCase


@override_settings(FLOW_CARDS_WRITE_ENABLED=True)
class FlowCardConcurrencyTests(TransactionTestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(override_settings(FLOW_CARDS_PRIVATE_ROOT=Path(directory)))
        from django.contrib.auth import get_user_model
        self.user = get_user_model().objects.create_user(username="flow-race")
        product = ProductSpecification.objects.create(specification="RACE", material="N7200")
        order = QualityOrder.objects.create(order_no="FLOW-RACE", specification="RACE", material="N7200", product_specification=product, source_system="翔邦", order_quantity=100, created_by=self.user)
        # TransactionTestCase flushes seed data after the first method.
        for code, name in [("BURR", "毛刺"), ("DIRT", "杂质"), ("AIR", "包风"), ("SHORT", "缺料")]:
            AppearanceIssue.objects.get_or_create(code=code, defaults={"name": name})
        self.body = {"request_key": "CONCURRENT-SAME", "product_id": product.pk, "order_ids": [order.pk], "planned_molds": 2000,
            "whole_mold": "REQUIRED", "whole_mold_basis": "隔离确认", "plan_basis": "隔离范围"}

    def parallel(self, path, bodies):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from django.db import close_old_connections, connections
        from rest_framework.test import APIClient
        barrier = Barrier(2)
        def work(body):
            close_old_connections()
            try:
                client = APIClient(); client.force_authenticate(self.user)
                barrier.wait(timeout=5)
                result = client.post(path, body, format="json")
                return result.status_code
            finally:
                connections["default"].close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(work, bodies))
        self.assertTrue(all(code in [200, 201, 400, 409, 503] for code in statuses), statuses)
        print(f"FLOW_CONCURRENCY path={path} statuses={statuses}")
        return statuses

    def test_A12_A27_concurrent_same_request_and_generate(self):
        from rest_framework.test import APIClient
        self.parallel("/api/flow-cards/groups/", [self.body, self.body])
        client = APIClient(); client.force_authenticate(self.user)
        retry = client.post("/api/flow-cards/groups/", self.body, format="json")
        self.assertEqual(retry.status_code, 201, retry.content)
        self.assertEqual(CardGroup.objects.count(), 1)
        identity = retry.json()["id"]
        self.parallel(f"/api/flow-cards/groups/{identity}/generate/", [{}, {}])
        result = client.post(f"/api/flow-cards/groups/{identity}/generate/", {}, format="json")
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual(InternalCard.objects.count(), 20)

    def test_A12_same_card_print_contention_no_orphan_job(self):
        group = s.generate_group(s.create_group(self.body, self.user), self.user)
        card = group.cards.first()
        bodies = [{"card_ids": [str(card.pk)], "request_key": f"CONCURRENT-PRINT-{i}"} for i in [1, 2]]
        statuses = self.parallel("/api/flow-cards/print-jobs/", bodies)
        # SQLite may reject BOTH simultaneous read-to-write upgrades. A 503
        # promises a safe same-request retry, not that either write committed.
        from rest_framework.test import APIClient
        client = APIClient(); client.force_authenticate(self.user)
        if not PrintJob.objects.exists():
            self.assertEqual(statuses, [503, 503])
            retry = client.post("/api/flow-cards/print-jobs/", bodies[0], format="json")
            self.assertEqual(retry.status_code, 201, retry.content)
        self.assertEqual(PrintJob.objects.count(), 1)
        self.assertEqual(PrintJob.objects.get().items.count(), 1)
        winner = PrintJob.objects.get()
        replay = client.post("/api/flow-cards/print-jobs/", next(b for b in bodies if b['request_key'] == winner.request_key), format="json")
        self.assertEqual(replay.json()['id'], str(winner.pk))
