"""RV01-RV08 isolated technical acceptance; no production or physical-print claim."""
import copy
import io
from unittest.mock import patch
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from quality.tests.helpers import QualityTestMixin
from quality.models import QualityShipmentBatch, QualityShipmentLine
from quality.services import delivered_quantities_by_order
from orders.models import ProductSpecification
from . import tests as original_tests
from .models import CardEvent, InternalCard, TemplatePublication, TraceAudit
from . import services as s


@override_settings(FLOW_CARDS_WRITE_ENABLED=True)
class FlowReviewTests(QualityTestMixin, TestCase):
    plan = original_tests.FlowCardTests.plan
    generated = original_tests.FlowCardTests.generated
    event = original_tests.FlowCardTests.event
    draft_shipment = original_tests.FlowCardTests.draft_shipment

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.product = ProductSpecification.objects.create(specification="3x1.5", material="ISOLATED")
        cls.order.product_specification = cls.product
        cls.order.specification, cls.order.material, cls.order.source_system = "3x1.5", "ISOLATED", "翔邦"
        cls.order.save()

    def standard(self, **extra):
        return s.confirm_standard({"request_key": s.uuid.uuid4().hex, "product_id": self.product.pk, "source": "隔离确认，非客户批准值",
            "appearance_ids": list(s.AppearanceIssue.objects.values_list("pk", flat=True)), **extra}, self.user)

    def bake_body(self, cards):
        return {"request_key": s.uuid.uuid4().hex, "card_ids": [str(c.pk) for c in cards], "kind": "BAKE", "employee_id": self.inspector.pk,
            "business_at": timezone.now().isoformat(), "scope": "实际部分第1-30模", "evidence": "隔离执行记录",
            "values": {"temperature_c": 150, "minutes": 120,
                "card_trays": [{"card_id": str(c.pk), "trays": [1, 3, 5], "scope": "各卡本次部分"} for c in cards]}}

    def test_RV01_preview_no_release_then_explicit_publish_once_and_next_renderer(self):
        before = (InternalCard.objects.count(), TemplatePublication.objects.count())
        response = self.client.get("/api/flow-cards/templates/preview/")
        self.assertEqual(response.status_code, 200, response.content[:100])
        self.assertTrue(response.content.startswith(b"%PDF"))
        self.assertEqual(before, (InternalCard.objects.count(), TemplatePublication.objects.count()))
        body = {"renderer": s.RENDERER, "print_confirmed": True, "confirmation": "隔离模拟人工确认，不代表真实实印"}
        bad = self.client.post("/api/flow-cards/templates/publish/", {**body, "print_confirmed": False}, format="json")
        self.assertEqual(bad.status_code, 400)
        for _ in range(2):
            r = self.client.post("/api/flow-cards/templates/publish/", body, format="json")
            self.assertEqual(r.status_code, 200, r.content)
            self.assertEqual(r.json()["version"], 1)
        self.assertEqual(TemplatePublication.objects.count(), 1)
        old_card = self.generated(planned_molds=100).cards.get()
        with patch.object(s, "RENDERER", "review-isolated-v3"), patch.object(s, "TEMPLATE", {**s.TEMPLATE, "renderer": "review-isolated-v3"}):
            result = self.client.post("/api/flow-cards/templates/publish/", {**body, "renderer": s.RENDERER}, format="json")
            self.assertEqual(result.json()["version"], 2)
        old_card.group.refresh_from_db()
        self.assertEqual(old_card.group.snapshot["template"]["version"], 1)

    def test_RV03_dimensions_and_unknown_scale_preserved_in_frozen_requirements(self):
        st = self.standard(dimension_count=1, hardness={"MOLDING_HARDNESS": {"object": "COUPON", "instrument": "手持，型号未确认"}},
            criteria=[{"name": "内径", "lower": "1", "upper": "2", "unit": "mm", "method": "隔离量具", "stage": "DIMENSION"}])
        snap = self.generated(standard_id=st.pk, planned_molds=100).snapshot["standard"]["snapshot"]
        self.assertEqual(snap["dimension_count"], 1)
        self.assertEqual(snap["hardness"]["MOLDING_HARDNESS"]["scale"], "")
        self.assertEqual(snap["hardness"]["BAKED_HARDNESS"]["object"], "UNKNOWN")
        self.assertEqual(self.generated(paper_width_mm=195, paper_height_mm=135).snapshot["template"]["width_mm"], 195)

    def test_RV03_celsius_display_fallback_does_not_change_snapshot(self):
        from .pdf_v2 import display_text
        self.assertEqual(display_text("150~160℃ × 120min"), "150~160°C × 120min")
        self.assertEqual(display_text("℃ ×"), "°C ×")
        st = self.standard(criteria=[{"name": "上模温度", "lower": "150", "upper": "160", "unit": "℃", "method": "隔离方法", "stage": "UPPER_TEMP"}])
        card = self.generated(standard_id=st.pk, planned_molds=100).cards.get()
        from .pdf import render_cards
        self.assertTrue(render_cards([(card, 0)]).startswith(b"%PDF"))
        card.group.refresh_from_db()
        self.assertEqual(card.group.snapshot["standard"]["snapshot"]["criteria"][0]["unit"], "℃")

    def test_RV05_older_than_twenty_groups_paginated_searchable_and_scanable(self):
        first = self.generated(planned_molds=1, plan_basis="最早明确范围")
        for _ in range(21): s.create_group(self.plan(planned_molds=1), self.user)
        response = self.client.get("/api/flow-cards/groups/", {"order_id": self.order.pk, "page": 2})
        self.assertEqual(response.json()["count"], 22)
        self.assertIn(str(first.pk), [r["id"] for r in response.json()["results"]])
        number = first.cards.get().card_no
        self.assertEqual(self.client.get("/api/flow-cards/groups/", {"q": number}).json()["results"][0]["id"], str(first.pk))
        self.assertEqual(self.client.get("/api/flow-cards/cards/scan/", {"code": number}).status_code, 200)

    def test_RV05_twenty_print_eighteen_success_two_unknown_resume_original(self):
        cards = list(self.generated(planned_molds=2000).cards.all())
        body = {"card_ids": list(map(lambda c: str(c.pk), cards)), "request_key": "review-print-20"}
        job = s.create_print(body, self.user)
        outcomes = {str(c.pk): "SUCCESS" if i < 18 else "UNKNOWN" for i, c in enumerate(cards)}
        for _ in range(2): s.confirm_print(job, {"outcomes": outcomes}, self.user)
        self.assertEqual(job.items.filter(status="SUCCESS").count(), 18)
        self.assertEqual(job.items.filter(active=True).count(), 2)
        s.confirm_print(job, {"outcomes": {str(c.pk): "SUCCESS" for c in cards[18:]}}, self.user)
        self.assertEqual(job.items.filter(active=True).count(), 0)
        self.assertEqual(set(InternalCard.objects.values_list("reprint_count", flat=True)), {0})
        self.assertEqual(s.create_print(body, self.user).pk, job.pk)

    def test_RV05_lost_response_retries_same_group_event_and_original_request(self):
        body = self.plan(planned_molds=100)
        group = s.generate_group(s.create_group(body, self.user), self.user)
        self.assertEqual(s.create_group(body, self.user).pk, group.pk)
        self.assertEqual(s.generate_group(group, self.user).cards.count(), 1)
        bake = self.bake_body(list(group.cards.all()))
        self.assertEqual(s.record_event(bake, self.user).pk, s.record_event(bake, self.user).pk)
        self.assertEqual(CardEvent.objects.filter(kind="BAKE").count(), 1)

    def test_RV05_unscanned_whole_batch_return_keeps_source_and_net(self):
        card = self.generated(planned_molds=100).cards.get()
        bid = self.draft_shipment()
        self.client.post(f"/api/quality/shipment-batches/{bid}/confirm/", {"internal_sources": [{"card_id": str(card.pk), "request_key": "rv-whole", "evidence": "隔离核对来源"}]}, format="json")
        returned = self.client.post("/api/quality/rework-cases/", {"origin": "CUSTOMER_RETURN", "shipment_batch_id": bid, "shipment_unit_no": 1, "reason": "隔离无扫码整批退回", "inspector_ids": [self.inspector.pk]}, format="json")
        self.assertEqual(returned.status_code, 201, returned.content)
        self.assertEqual(delivered_quantities_by_order([self.order.pk])[self.order.pk], 0)
        trace = self.client.get(f"/api/flow-cards/cards/{card.pk}/trace/").json()["results"][0]
        self.assertEqual(trace["related_returns"][0]["id"], returned.json()["id"])
        bound = self.client.post(f"/api/quality/rework-cases/{returned.json()['id']}/bind-card/", {"card_no": "RV-UNSCANNED-CARD", "order_id": self.order.pk}, format="json")
        self.assertEqual(bound.status_code, 200, bound.content)
        waiting = self.client.patch(f"/api/quality/rework-cases/{returned.json()['id']}/", {"status": "WAITING_REWORK"}, format="json")
        self.assertEqual(waiting.status_code, 200, waiting.content)
        reship = self.client.post(f"/api/quality/rework-cases/{returned.json()['id']}/reship/", {"inspector_ids": [self.inspector.pk]}, format="json")
        self.assertEqual(reship.status_code, 201, reship.content)
        self.assertEqual(delivered_quantities_by_order([self.order.pk])[self.order.pk], 100)

    def test_RV06_bake_multiple_cards_noncontinuous_trays_repeat_and_snapshot(self):
        cards = list(self.generated(planned_molds=200).cards.all())
        body = self.bake_body(cards)
        first = s.record_event(body, self.user)
        self.assertEqual(first.cards.count(), 2)
        self.assertEqual(first.values["card_trays"][0]["trays"], [1,3,5])
        again = copy.deepcopy(body); again["request_key"] = "bake-next"; again["scope"] = "本卡剩余部分"
        s.record_event(again, self.user)
        self.assertEqual(CardEvent.objects.filter(kind="BAKE").count(), 2)
        self.assertEqual(delivered_quantities_by_order([self.order.pk])[self.order.pk], 0)

    def test_RV06_shared_coupon_allows_different_trays_but_not_unselected_tray(self):
        cards = list(self.generated(planned_molds=200).cards.all())
        body = self.bake_body(cards)
        body['values']['card_trays'][1]['trays'] = [7]
        body['values']['coupon_samples'] = [{'sample_ref': 'SHARED', 'material_ref': '同次当批料',
            'card_ids': [str(c.pk) for c in cards], 'trays': [1, 7], 'accompanied': True}]
        saved = s.record_event(body, self.user)
        self.assertEqual(saved.cards.count(), 2)
        for invalid in [[9], [True], ['1']]:
            bad = copy.deepcopy(body); bad['request_key'] = s.uuid.uuid4().hex
            bad['values']['coupon_samples'][0]['trays'] = invalid
            with self.assertRaises(ValidationError):
                s.record_event(bad, self.user)

    def test_RV06_coupon_baked_read_requires_actual_accompanied_sample_and_scope(self):
        config = {"object": "COUPON", "scale": "隔离量表（非实际仪器确认）"}
        st = self.standard(hardness={"MOLDING_HARDNESS": config, "BAKED_HARDNESS": config})
        cards = list(self.generated(standard_id=st.pk, planned_molds=200).cards.all())
        body = self.bake_body(cards)
        body["values"]["coupon_samples"] = [{"sample_ref":"S1", "material_ref":"原始料批标识", "card_ids":[str(cards[0].pk)], "trays":[1,5], "accompanied": True}]
        bake = s.record_event(body, self.user)
        event = self.event(cards[0], kind="BAKED_HARDNESS", employee_id=self.inspector.pk, business_at=timezone.now().isoformat(),
            values={"state":"MEASURED", "actual":0, "measurement_object":"COUPON", "sample_ref":"S1", "material_ref":"原始料批标识", "bake_event_id":str(bake.pk)})
        measured = s.record_event(event, self.user, entry_card=cards[0].pk)
        self.assertEqual(measured.values["actual"], "0")
        self.assertEqual(measured.values["judgment"], "NOT_EVALUATED")
        bad = {**event, "request_key":"wrong-coverage", "card_ids":[str(cards[1].pk)]}
        with self.assertRaisesMessage(ValidationError,"未登记随"):
            s.record_event(bad, self.user, entry_card=cards[1].pk)
        bad = copy.deepcopy(event); bad["request_key"]="no-bake"; bad["values"]["bake_event_id"]=None
        with self.assertRaises(ValidationError): s.record_event(bad,self.user)

    def test_RV07_replace_preserves_both_original_bytes_and_audit_no_completion(self):
        import tempfile
        from PIL import Image
        from django.core.files.uploadedfile import SimpleUploadedFile
        from .models import Attachment
        card=self.generated(planned_molds=100).cards.get()
        with tempfile.TemporaryDirectory() as root, override_settings(FLOW_CARDS_PRIVATE_ROOT=root):
            ids=[]
            for color in ["white","black"]:
                raw=io.BytesIO(); Image.new("RGB",(8,8),color).save(raw,"PNG")
                data={"card_id":str(card.pk),"file":SimpleUploadedFile("paper.png",raw.getvalue(),content_type="image/png")}
                if ids: data["replaces_id"]=ids[0]
                response=self.client.post("/api/flow-cards/attachments/",data,format="multipart")
                self.assertEqual(response.status_code,201,response.content); ids.append(response.json()["id"])
            self.assertEqual(str(Attachment.objects.get(pk=ids[1]).replaces_id),ids[0])
            for aid in ids:
                response = self.client.get(f"/api/flow-cards/attachments/{aid}/content/")
                self.assertEqual(response.status_code, 200)
                self.assertTrue(b"".join(response.streaming_content).startswith(b"\x89PNG"))
                response.close()
            self.assertEqual(self.client.get(f"/api/flow-cards/cards/{card.pk}/").json()["whole_mold_status"],"SYSTEM_UNREGISTERED")
            self.assertEqual(TraceAudit.objects.filter(object_type="Attachment").count(),2)

    def test_RV03_RV06_explicit_objects_not_size_heuristics_and_old_pdf_immutable(self):
        from .pdf import render_cards
        for spec, obj in [("3x1.5", "COUPON"), ("100x10", "COUPON"), ("regular", "PRODUCT")]:
            product = ProductSpecification.objects.create(specification=spec, material="ISOLATED")
            st = s.confirm_standard({"request_key":s.uuid.uuid4().hex,"product_id":product.pk,"source":"隔离明确选择，不按尺寸猜",
                "appearance_ids":list(s.AppearanceIssue.objects.values_list("pk",flat=True)),"hardness":{"MOLDING_HARDNESS":{"object":obj}}},self.user)
            self.assertEqual(st.snapshot["hardness"]["MOLDING_HARDNESS"]["object"],obj)
            self.assertEqual(st.snapshot["hardness"]["MOLDING_HARDNESS"]["scale"],"")
        st = self.standard(hardness={"MOLDING_HARDNESS":{"object":"COUPON"}})
        card = self.generated(standard_id=st.pk,planned_molds=100).cards.get()
        original = render_cards([(card,0)])
        self.standard(hardness={"MOLDING_HARDNESS":{"object":"PRODUCT","scale":"另确认量表"}})
        card.refresh_from_db()
        self.assertEqual(original,render_cards([(card,0)]))
        old = copy.deepcopy(card.group.snapshot)
        old["template"]={"renderer":"flowcard-draft-20261118-v1","version":1}
        card.group.snapshot=old
        self.assertTrue(render_cards([(card,0)]).startswith(b"%PDF"))

    def test_RV06_malformed_sample_scope_is_validation_error_not_500(self):
        cards=list(self.generated(planned_molds=100).cards.all())
        for scope in [[{}], [None], [123], []]:
            body=self.bake_body(cards)
            body["values"]["coupon_samples"]=[{"sample_ref":"S1","material_ref":"M1","card_ids":scope,"trays":[1],"accompanied":True}]
            with self.assertRaises(ValidationError): s.record_event(body,self.user)
