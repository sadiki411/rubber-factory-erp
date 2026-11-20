"""Entirely synthetic. None of these tests touches the configured production DB."""
import uuid
from copy import deepcopy

from django.contrib.auth.models import Group, Permission
from django.db import IntegrityError, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from quality.tests.helpers import QualityTestMixin
from orders.models import ProductSpecification
from production.models import ProductionRun, ProductionRunOrder, ProductionDailyLog
from orders.models import ProductInspectionCriterion
from quality.models import QualityOrder, QualityShipmentBatch
from . import preparation as p, services as s
from .models import CardGroup, InternalCard, OrderCardPreparation


@override_settings(FLOW_CARDS_WRITE_ENABLED=True, FLOW_CARDS_PREPARATION_ENABLED=True)
class PreparationTests(QualityTestMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.product = ProductSpecification.objects.create(specification="SYNTH-215", material="N7200")
        cls.order.product_specification = cls.product
        cls.order.specification = cls.product.specification
        cls.order.material = cls.product.material
        cls.order.source_system = "翔邦"
        cls.order.save()

    def setUp(self):
        super().setUp()
        self.user.user_permissions.add(Permission.objects.get(codename="maintain_product_standard"))

    def standard(self, **changes):
        body = {"request_key": uuid.uuid4().hex, "product_id": self.product.pk, "customer_key": "翔邦", "source": "合成资料20261119，非正式产品标准",
                "whole_mold": "NOT_REQUIRED", "dimension_count": 1,
                "appearance_ids": list(s.AppearanceIssue.objects.values_list("pk", flat=True)),
                "criteria": [{"name": "线径", "upper": "3.6", "lower": "", "unit": "mm", "limit_mode": "UPPER", "numeric": True, "stage": "DIMENSION", "method": "合成量具"}],
                "route": {"confirmed": True, "molding": True, "patrol": True, "deburr": True, "inspection": True, "bake": False},
                "packaging": {"molds_per_card": 100, "patrol_every_molds": 50}, **changes}
        row = s.confirm_standard(body, self.user)
        return p.activate_standard(row, self.user) if changes.get("activate", True) else row

    def create_run(self, order=None, **changes):
        order = order or self.order
        return ProductionRun.objects.create(order=order, product_specification=self.product, order_no=order.order_no,
            specification=self.product.specification, material=self.product.material, order_quantity=3208, cavities=16,
            planned_mold_count=201, is_ledger_only=True, created_by=self.user, **changes)

    def body(self, run=None, **changes):
        return {"product_id": self.product.pk, "order_id": self.order.pk, "run_id": run.pk if run else None,
                "request_key": uuid.uuid4().hex, **changes}

    def test_A_two_entries_standard_zero_reentry_three_cards_no_quantity_post(self):
        self.standard()
        run = self.create_run()
        before = (ProductionRun.objects.count(), ProductionDailyLog.objects.count(), QualityShipmentBatch.objects.count())
        order_context = p.context({"order_id": self.order.pk}, self.user)
        run_context = p.context({"run_id": run.pk}, self.user)
        self.assertEqual(order_context["run_id"], run.pk)
        for key in ("order_ids", "suggested_molds", "auto_standard_id", "available_ranges"):
            self.assertEqual(order_context[key], run_context[key])
        self.assertEqual(run_context["suggested_molds"], 201)
        group = p.save_draft(self.body(run), self.user)
        s.generate_group(group, self.user)
        self.assertEqual(list(group.cards.values_list("planned_molds", flat=True)), [100, 100, 1])
        self.assertEqual((group.range_start, group.range_end), (1, 201))
        group.refresh_from_db()
        self.assertEqual([t["label"] for t in group.snapshot["todos"]], ["成型实测", "巡检", "毛边", "品检"])
        self.assertEqual(before, (ProductionRun.objects.count(), ProductionDailyLog.objects.count(), QualityShipmentBatch.objects.count()))
        self.assertEqual(p.context({"order_id": self.order.pk}, self.user)["preferred_group"]["id"], str(group.pk))

    def test_order_explicit_360_without_run_and_default_100_100_100_60(self):
        self.standard()
        before = (ProductionRun.objects.count(), ProductionDailyLog.objects.count(), QualityShipmentBatch.objects.count())
        body = self.body(order_plan=True, planned_molds=360)
        group = p.save_draft(body, self.user)
        self.assertIsNone(group.run_id)
        self.assertEqual(group.snapshot["card_molds"], [100, 100, 100, 60])
        self.assertEqual(group.snapshot["plan_source"], "ORDER_EXPLICIT")
        self.assertIsNone(group.snapshot["effective_cavities"])
        self.assertEqual(p.save_draft(body, self.user).pk, group.pk)
        s.generate_group(group, self.user)
        s.generate_group(group, self.user)
        self.assertEqual(list(group.cards.order_by("sequence").values_list("planned_molds", flat=True)), [100, 100, 100, 60])
        self.assertEqual(before, (ProductionRun.objects.count(), ProductionDailyLog.objects.count(), QualityShipmentBatch.objects.count()))
        ctx = p.context({"order_id": self.order.pk, "order_plan": "1"}, self.user)
        self.assertEqual(ctx["preferred_group"]["id"], str(group.pk))
        with self.assertRaises(p.PreparationConflict):
            p.save_draft(self.body(order_plan=True, planned_molds=360), self.user)
        # Later production sees the original order cards, not another auto-issued set.
        run = self.create_run()
        self.assertEqual(p.context({"run_id": run.pk}, self.user)["preferred_group"]["id"], str(group.pk))
        with self.assertRaises(p.PreparationConflict):
            p.save_draft(self.body(run), self.user)

    def test_order_plan_does_not_guess_existing_run_and_tail_edit_survives_reopen(self):
        self.standard()
        self.create_run()
        self.assertIsNone(p.context({"order_id": self.order.pk, "order_plan": "1"}, self.user)["run_id"])
        group = p.save_draft(self.body(order_plan=True, planned_molds=360), self.user)
        changed = p.save_draft(self.body(order_plan=True, group_id=str(group.pk), expected_revision=1,
            planned_molds=360, card_molds=[100, 100, 90, 70]), self.user)
        self.assertEqual(changed.pk, group.pk)
        self.assertEqual(changed.snapshot["card_molds"], [100, 100, 90, 70])
        s.generate_group(changed, self.user)
        self.assertEqual(list(changed.cards.order_by("sequence").values_list("planned_molds", flat=True)), [100, 100, 90, 70])
        with self.assertRaisesMessage(s.ValidationError, "旧卡"):
            p.save_draft(self.body(order_plan=True, group_id=str(group.pk), expected_revision=2, planned_molds=360), self.user)

    def test_card_distribution_rejects_bad_values_totals_and_excess_cards(self):
        self.standard()
        for custom in ([100,100,100,59], [100,100,160,0], [100,100,161,-1], [100,100,99.5,60.5],
                       [True,359], [], "100/100/100/60", [1]*501):
            with self.subTest(custom=custom):
                with self.assertRaises(s.ValidationError):
                    p.save_draft(self.body(order_plan=True, planned_molds=360, card_molds=custom), self.user)
        self.assertFalse(CardGroup.objects.exists())

    def test_order_print_resumes_same_pending_job_without_new_numbers(self):
        self.standard()
        group = s.generate_group(p.save_draft(self.body(order_plan=True, planned_molds=360), self.user), self.user)
        ids = list(map(str, group.cards.order_by("sequence").values_list("pk", flat=True)))
        first = s.create_print({"request_key": uuid.uuid4().hex, "card_ids": ids, "resume_active": True}, self.user)
        retry = s.create_print({"request_key": uuid.uuid4().hex, "card_ids": ids, "resume_active": True}, self.user)
        self.assertEqual(first.pk, retry.pk)
        self.assertEqual(InternalCard.objects.count(), 4)
        self.assertTrue(all(c.printed_at is None for c in group.cards.all()))
        with self.assertRaises(s.ValidationError):
            s.create_print({"request_key": uuid.uuid4().hex, "card_ids": ids[:2], "resume_active": True}, self.user)

    def test_order_preview_returns_custom_tail_without_writing(self):
        self.standard()
        response = self.client.post("/api/flow-cards/groups/preview/", self.body(order_plan=True, planned_molds=360, card_molds=[100,100,90,70]), format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["tail_molds"], 70)
        self.assertEqual(response.json()["count"], 4)
        self.assertFalse(CardGroup.objects.exists())

    def test_B_completed_runs_never_suggest_old_plan_or_create_new_segment(self):
        run = self.create_run(status="COMPLETED")
        data = p.context({"order_id": self.order.pk}, self.user)
        self.assertIsNone(data["run_id"])
        self.assertIsNone(data["suggested_molds"])
        self.assertTrue(data["runs"][0]["historical"])
        with self.assertRaisesMessage(s.ValidationError, "历史"):
            p.save_draft(self.body(run), self.user)
        self.assertEqual(ProductionRun.objects.count(), 1)

    def test_B_three_orders_single_context_append_only_uncovered(self):
        self.standard()
        run = self.create_run()
        orders = [self.order]
        for n in range(2):
            order = QualityOrder.objects.create(order_no=f"SYNTH-COMMON-{n}", specification=self.product.specification, material=self.product.material,
                product_specification=self.product, source_system="翔邦", order_quantity=100, created_by=self.user)
            ProductionRunOrder.objects.create(run=run, order=order, planned_quantity=100)
            orders.append(order)
        first = p.save_draft(self.body(run, planned_molds=100), self.user)
        for order in orders:
            self.assertEqual(p.context({"order_id": order.pk}, self.user)["preferred_group"]["id"], str(first.pk))
        s.generate_group(first, self.user)
        self.assertEqual(OrderCardPreparation.objects.get(order=self.order).state, "PENDING")
        with self.assertRaises(p.PreparationConflict):
            p.save_draft(self.body(run, range_start=1, planned_molds=100, append_confirmed=True), self.user)
        second = p.save_draft(self.body(run), self.user)
        self.assertEqual((second.range_start, second.range_end), (101, 201))
        self.assertEqual(second.orders.count(), 3)
        s.generate_group(second, self.user)
        self.assertEqual(InternalCard.objects.count(), 3)
        self.assertEqual(OrderCardPreparation.objects.get(order=self.order).state, "GENERATED")

    def test_B_multitask_requires_choice_and_cross_customer_rejected(self):
        a, b = self.create_run(), self.create_run(segment_no=2)
        self.assertIsNone(p.context({"order_id": self.order.pk}, self.user)["run_id"])
        other = QualityOrder.objects.create(order_no="SYNTH-OTHER-CUSTOMER", specification=self.product.specification, material=self.product.material,
            product_specification=self.product, source_system="艾斯霖", order_quantity=100, created_by=self.user)
        ProductionRunOrder.objects.create(run=a, order=other, planned_quantity=100)
        with self.assertRaisesMessage(s.ValidationError, "跨客户"):
            p.save_draft(self.body(a), self.user)
        self.assertEqual(b.status, "PLANNED")

    def test_C_missing_first_order_same_draft_after_later_task_and_conflict(self):
        p.sync_order_suggestion(self.order, self.user)
        self.order.production_required = True
        self.order.save()
        p.sync_order_suggestion(self.order, self.user)
        p.sync_order_suggestion(self.order, self.user)
        self.assertEqual(OrderCardPreparation.objects.count(), 1)
        draft = p.save_draft(self.body(), self.user)
        self.assertIsNone(draft.planned_molds)
        self.assertEqual(p.context({"order_id": self.order.pk}, self.user)["preferred_group"]["id"], str(draft.pk))
        run = self.create_run()
        self.assertEqual(p.context({"run_id": run.pk}, self.user)["preferred_group"]["id"], str(draft.pk))
        body = self.body(run, group_id=str(draft.pk), expected_revision=1)
        updated = p.save_draft(body, self.user)
        self.assertEqual(updated.pk, draft.pk)
        self.assertEqual(updated.revision, 2)
        self.assertEqual(p.save_draft(body, self.user).revision, 2)
        with self.assertRaises(p.PreparationConflict):
            p.save_draft({**body, "request_key": uuid.uuid4().hex}, self.user)
        self.assertEqual(CardGroup.objects.count(), 1)

    def test_C_missing_standard_later_completed_adopts_known_whole_mold_on_same_draft(self):
        draft = p.save_draft(self.body(), self.user)
        self.assertEqual(draft.whole_mold, "UNKNOWN")
        self.standard()
        run = self.create_run()
        updated = p.save_draft(self.body(run, group_id=str(draft.pk), expected_revision=1, whole_mold="UNKNOWN"), self.user)
        self.assertEqual(updated.pk, draft.pk)
        self.assertEqual(updated.whole_mold, "NOT_REQUIRED")
        self.assertEqual(s.generate_group(updated, self.user).cards.count(), 3)

    def test_D_reference_edits_rejected_explicit_override_frozen_and_not_global(self):
        original = ProductInspectionCriterion.objects.create(product_specification=self.product, inspection_item="线径", upper_limit="3.6", unit="mm", raw_data={"limit_mode": "UPPER"})
        entry = {"criterion_id": original.pk, "stage": "DIMENSION", "method": "合成量具", "limit_mode": "UPPER", "upper": "3.5", "unit": "mm"}
        with self.assertRaisesMessage(s.ValidationError, "静默改值"):
            self.standard(criteria=[entry])
        specific = self.standard(applicable_order_id=self.order.pk, criteria=[{**entry, "override": True}])
        original.refresh_from_db()
        self.assertEqual(original.upper_limit, "3.6")
        self.assertEqual(specific.snapshot["criteria"][0]["upper"], "3.5")
        run = self.create_run()
        draft = p.save_draft(self.body(run, standard_id=specific.pk), self.user)
        self.assertEqual(draft.snapshot["standard"]["snapshot"]["criteria"][0]["upper"], "3.5")
        draft.refresh_from_db()
        self.assertEqual(draft.snapshot["standard"]["snapshot"]["criteria"][0]["lower"], "")
        s.generate_group(draft, self.user)
        other = deepcopy(self.order)
        other.pk = None
        other.order_no = "SYNTH-NEXT"
        other.save()
        self.assertNotIn(specific.pk, [v.pk for v in p.applicable_standards(self.product, None, [other])])

    def test_D_conflicts_cannot_be_waived_with_text(self):
        one = ProductInspectionCriterion.objects.create(product_specification=self.product, inspection_item="硬度", lower_limit="60", upper_limit="70", unit="Shore A", raw_data={"limit_mode": "RANGE"})
        ProductInspectionCriterion.objects.create(product_specification=self.product, order=self.order, inspection_item="硬度", lower_limit="50", upper_limit="60", unit="Shore D")
        standard = self.standard(criteria=[{"criterion_id": one.pk, "stage": "MOLDING_HARDNESS", "limit_mode": "RANGE"}])
        run = self.create_run()
        draft = p.save_draft(self.body(run, standard_id=standard.pk, requirements_review="任意说明"), self.user)
        with self.assertRaisesMessage(s.ValidationError, "逐项"):
            s.generate_group(draft, self.user)
        context = p.context({"run_id": run.pk}, self.user)
        self.assertEqual({c["unit"] for c in context["conflicts"][0]["sources"]}, {"Shore A", "Shore D"})

    def test_E_new_standard_does_not_rewrite_frozen_card_or_time_units(self):
        standard = self.standard(process_parameters={"primary_curing_time": {"state": "CONFIRMED", "lower": "50", "upper": "60", "unit": "min"},
            "upper_set_temperature": {"state": "CONFIRMED", "lower": "175", "upper": "185", "unit": "℃"},
            "upper_contact_temperature": {"state": "CONFIRMED", "lower": "165", "upper": "175", "unit": "℃"}})
        run = self.create_run()
        group = p.save_draft(self.body(run), self.user)
        s.generate_group(group, self.user)
        group.refresh_from_db()
        frozen = deepcopy(group.snapshot)
        second = self.standard()
        standard.refresh_from_db()
        self.assertEqual(standard.status, "SUPERSEDED")
        self.assertEqual(second.status, "ACTIVE")
        group.refresh_from_db()
        self.assertEqual(group.snapshot, frozen)
        self.assertEqual(group.snapshot["process_parameters"]["primary_curing_time"]["unit"], "min")
        first_card = group.cards.first()
        s.generate_group(group, self.user)
        self.assertEqual(group.cards.first().card_no, first_card.card_no)

    def test_F_readonly_preview_no_write_and_operator_no_master_write(self):
        self.standard()
        run = self.create_run()
        self.user.groups.add(Group.objects.create(name="GPT只读测试"))
        before = (CardGroup.objects.count(), InternalCard.objects.count(), OrderCardPreparation.objects.count())
        response = self.client.post("/api/flow-cards/groups/preview/", self.body(run), format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["count"], 3)
        self.assertEqual(before, (CardGroup.objects.count(), InternalCard.objects.count(), OrderCardPreparation.objects.count()))
        self.assertEqual(self.client.post("/api/flow-cards/groups/", self.body(run), format="json").status_code, 403)
        self.user.groups.clear()
        self.user.user_permissions.clear()
        self.user = type(self.user).objects.get(pk=self.user.pk)
        self.client.force_authenticate(self.user)
        self.assertFalse(p.can_maintain(self.user))
        self.assertEqual(self.client.post("/api/flow-cards/standards/", {}, format="json").status_code, 403)

    def test_get_refresh_does_not_backfill_old_orders_or_drafts(self):
        for _ in range(2):
            response = self.client.get("/api/flow-cards/context/", {"order_id": self.order.pk})
            self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual((CardGroup.objects.count(), OrderCardPreparation.objects.count()), (0, 0))

    def test_product_profile_can_read_customer_versions_without_granting_job_applicability(self):
        first = self.standard()
        second = self.standard(customer_key="艾斯霖")
        data = p.context({"product_id": self.product.pk}, self.user)
        self.assertEqual({s["id"] for s in data["standards"]}, {first.pk, second.pk})
        self.assertEqual(len(data["criteria"]), 2)
        self.assertIsNone(data["auto_standard_id"])
        self.assertNotIn(second.pk, [s.pk for s in p.applicable_standards(self.product, None, [self.order])])

    def test_B_database_guard_rejects_overlapping_interval_even_outside_api(self):
        run = self.create_run()
        first = p.save_draft(self.body(run, planned_molds=100), self.user)
        with self.assertRaises(IntegrityError), transaction.atomic():
            CardGroup.objects.create(product=self.product, run=run, context_key="direct-bypass", range_start=50, range_end=150,
                planned_molds=101, request_key=uuid.uuid4().hex, request_digest="guard", plan_basis="合成越界验证", created_by=self.user)
        self.assertEqual(CardGroup.objects.count(), 1)
        self.assertEqual(first.range_end, 100)

    def test_C_arrangement_formula_requires_all_evidence_and_never_uses_total_holes(self):
        self.product.total_cavities = "99"
        self.product.save()
        plan = p.build_plan(self.body(arranged_quantity=3208))
        self.assertIsNone(plan["planned"])
        plan = p.build_plan(self.body(arranged_quantity=3208, confirmed_effective_output=16,
            output_unit="件/模", output_basis="本次人工核对实物有效产出", effective_output_confirmed=True))
        self.assertEqual(plan["planned"], 201)
        self.assertIsNone(plan["range_start"])
        self.assertEqual(plan["snapshot"]["effective_cavities"], 16)
        self.assertEqual(ProductionRun.objects.count(), 0)

    def test_C_second_device_cannot_generate_stale_revision(self):
        self.standard()
        run = self.create_run()
        draft = p.save_draft(self.body(run), self.user)
        stale_revision = draft.revision
        updated = p.save_draft(self.body(run, group_id=str(draft.pk), expected_revision=stale_revision, molds_per_card=50), self.user)
        response = self.client.post(f"/api/flow-cards/groups/{draft.pk}/generate/", {"expected_revision": stale_revision}, format="json")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(InternalCard.objects.count(), 0)
        response = self.client.post(f"/api/flow-cards/groups/{draft.pk}/generate/", {"expected_revision": updated.revision}, format="json")
        self.assertEqual(response.status_code, 200, response.content)

    def test_C_reimport_preserves_prepared_suggestion_and_completed_production_is_not_pending(self):
        self.order.production_required = True
        self.order.save()
        run = self.create_run()
        draft = p.save_draft(self.body(run), self.user)
        p.sync_order_suggestion(self.order, self.user)
        self.assertEqual(OrderCardPreparation.objects.get(order=self.order).state, "PREPARING")
        self.assertEqual(OrderCardPreparation.objects.get(order=self.order).draft_id, draft.pk)
        self.order.status = "COMPLETED"
        self.order.save()
        p.sync_order_suggestion(self.order, self.user)
        self.assertEqual(OrderCardPreparation.objects.get(order=self.order).state, "NOT_PENDING")

    def test_E_this_preparation_process_override_never_changes_master(self):
        standard = self.standard(process_parameters={"primary_curing_time":{"state":"CONFIRMED","lower":"50","upper":"60","unit":"min"}})
        run = self.create_run()
        override = {"primary_curing_time":{"state":"CONFIRMED","lower":"55","upper":"60","unit":"min","source":"仅本段合成例外"}}
        draft = p.save_draft(self.body(run, process_overrides=override), self.user)
        self.assertEqual(draft.snapshot["process_parameters"]["primary_curing_time"]["lower"], "55")
        standard.refresh_from_db()
        self.assertEqual(standard.snapshot["process_parameters"]["primary_curing_time"]["lower"], "50")

    def test_F_feature_off_keeps_existing_card_readable_without_new_suggestions(self):
        self.standard()
        run = self.create_run()
        draft = p.save_draft(self.body(run), self.user)
        card = s.generate_group(draft, self.user).cards.first()
        with override_settings(FLOW_CARDS_PREPARATION_ENABLED=False, FLOW_CARDS_WRITE_ENABLED=False):
            self.assertEqual(self.client.get(f"/api/flow-cards/cards/{card.pk}/").status_code, 200)
            self.assertEqual(self.client.post("/api/flow-cards/groups/", self.body(run), format="json").status_code, 403)

    def test_D_reference_cannot_change_inclusive_or_mode_or_borrow_other_customer(self):
        original = ProductInspectionCriterion.objects.create(product_specification=self.product, inspection_item="线径", upper_limit="3.6", unit="mm", raw_data={"limit_mode": "UPPER", "upper_inclusive": False})
        entry = {"criterion_id": original.pk, "stage": "DIMENSION", "method": "量具", "limit_mode": "UPPER"}
        for changes in ({"upper_inclusive": True}, {"limit_mode": "PENDING"}):
            with self.assertRaisesMessage(s.ValidationError, "静默改值"):
                self.standard(criteria=[{**entry, **changes}])
        standard = self.standard(criteria=[entry])
        self.assertFalse(standard.snapshot["criteria"][0]["upper_inclusive"])
        original.customer = "艾斯霖"
        original.save()
        with self.assertRaisesMessage(s.ValidationError, "其他客户"):
            self.standard(criteria=[entry])

    def test_D_order_exception_cannot_escape_its_order_or_be_silently_merged(self):
        original = ProductInspectionCriterion.objects.create(product_specification=self.product, order=self.order, inspection_item="线径", upper_limit="3.5", unit="mm", raw_data={"limit_mode": "UPPER"})
        entry = {"criterion_id": original.pk, "stage": "DIMENSION", "method": "量具", "limit_mode": "UPPER"}
        with self.assertRaisesMessage(s.ValidationError, "订单特规"):
            self.standard(criteria=[entry])
        specific = self.standard(applicable_order_id=self.order.pk, criteria=[entry])
        base = self.standard()
        run = self.create_run()
        other = QualityOrder.objects.create(order_no="SYNTH-INCOMPATIBLE", specification=self.product.specification, material=self.product.material,
            product_specification=self.product, source_system="翔邦", order_quantity=100, created_by=self.user)
        ProductionRunOrder.objects.create(run=run, order=other, planned_quantity=100)
        with self.assertRaisesMessage(s.ValidationError, "独立生效的订单特规"):
            p.save_draft(self.body(run, standard_id=base.pk), self.user)
        self.assertEqual(specific.status, "ACTIVE")

    def test_F_operator_cannot_publish_template_or_edit_shared_issue_dictionary(self):
        self.user.user_permissions.clear()
        self.client.force_authenticate(type(self.user).objects.get(pk=self.user.pk))
        for path in ("/api/flow-cards/templates/publish/", "/api/flow-cards/appearance/"):
            self.assertEqual(self.client.post(path, {}, format="json").status_code, 403)

    def test_F_sources_paginated_and_original_file_missing_is_not_empty(self):
        for n in range(55):
            ProductInspectionCriterion.objects.create(product_specification=self.product, inspection_item=f"合成来源{n}")
        response = self.client.get("/api/flow-cards/preparation-sources/", {"order_id": self.order.pk, "page_size": 50})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 55)
        self.assertEqual(len(response.json()["results"]), 50)
        import tempfile
        from orders.models import BusinessImportBatch
        from django.core.files.base import ContentFile
        with tempfile.TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory):
            batch = BusinessImportBatch.objects.create(source_type="PRODUCT_SPECIFICATIONS", parser="synthetic-only", original_name="合成原件.txt", sha256="0" * 64, created_by=self.user)
            path = f"/api/flow-cards/source-files/{batch.pk}/"
            self.assertEqual(self.client.get(path).status_code, 503)
            batch.original_file.save("synthetic.txt", ContentFile(b"synthetic source, no official data"))
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertIn("attachment", response["Content-Disposition"])
            self.assertEqual(response["Content-Security-Policy"], "sandbox")
            response.close()
            self.client.force_authenticate(user=None)
            self.assertIn(self.client.get(path).status_code, (401, 403))

    def test_F_todos_only_reference_actual_events_and_do_not_claim_whole_completion(self):
        self.standard()
        group = p.save_draft(self.body(self.create_run()), self.user)
        card = s.generate_group(group, self.user).cards.first()
        todos = p.package_progress(card)
        self.assertTrue(all(t["state"] == "SYSTEM_UNREGISTERED" and not t["fact_ids"] for t in todos))
        self.assertTrue(all("不等于现场未做" in t["notice"] for t in todos))

    def test_F_cannot_hide_required_measurement_by_disabling_its_route(self):
        self.standard(route={"confirmed": True, "molding": False, "inspection": True})
        group = p.save_draft(self.body(self.create_run()), self.user)
        with self.assertRaisesMessage(s.ValidationError, "适用工序未开启"):
            s.generate_group(group, self.user)
        self.assertEqual(InternalCard.objects.count(), 0)

    def test_C_bulk_suggestions_are_idempotent_without_per_order_aggregation(self):
        from unittest.mock import patch
        self.order.production_required = True
        self.order.save()
        other = QualityOrder.objects.create(order_no="SYNTH-IMPORT-SECOND", product_specification=self.product, specification=self.product.specification,
            material=self.product.material, source_system="翔邦", order_quantity=100, production_required=True, created_by=self.user)
        with patch("orders.services.production_totals_by_order", return_value={}) as production, patch("quality.services.delivered_quantities_by_order", return_value={}) as delivery:
            p.sync_order_suggestions([self.order, other, self.order], self.user)
            self.assertEqual(production.call_count, 1)
            self.assertEqual(delivery.call_count, 1)
        p.sync_order_suggestions([self.order, other], self.user)
        self.assertEqual(OrderCardPreparation.objects.count(), 2)

    def test_E_pdf_route_and_old_rendering_are_separate(self):
        from .pdf import render_cards
        self.standard()
        group = p.save_draft(self.body(self.create_run()), self.user)
        card = s.generate_group(group, self.user).cards.first()
        new_pdf = render_cards([(card, 0)])
        self.assertTrue(new_pdf.startswith(b"%PDF"))
        card.group.snapshot["route"]["bake"] = True
        self.assertNotEqual(render_cards([(card, 0)]), new_pdf)
        group.refresh_from_db()
        group.snapshot["template"]["renderer"] = s.RENDERER
        group.save(update_fields=["snapshot"])
        card = group.cards.first()
        old = render_cards([(card, 0)])
        self.standard()
        with override_settings(FLOW_CARDS_PREPARATION_ENABLED=False):
            self.assertEqual(render_cards([(card, 0)]), old)


@override_settings(FLOW_CARDS_WRITE_ENABLED=True, FLOW_CARDS_PREPARATION_ENABLED=True)
class PreparationConcurrencyTests(TransactionTestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        from .models import AppearanceIssue
        self.user = get_user_model().objects.create_user(username="synthetic-preparation-race")
        self.user.user_permissions.add(Permission.objects.get(codename="maintain_product_standard"))
        for code, name in (("BURR", "毛刺"), ("DIRT", "杂质"), ("AIR", "包风"), ("SHORT", "缺料")):
            AppearanceIssue.objects.get_or_create(code=code, defaults={"name": name})
        self.product = ProductSpecification.objects.create(specification="SYNTH-RACE", material="SYNTH")
        self.order = QualityOrder.objects.create(order_no="SYNTH-RACE", product_specification=self.product, specification="SYNTH-RACE", material="SYNTH", source_system="翔邦", order_quantity=3208, created_by=self.user)
        self.standard = PreparationTests.standard(self)
        self.run = PreparationTests.create_run(self)
        self.body = PreparationTests.body(self, self.run)

    def parallel(self, path, bodies):
        from .tests import FlowCardConcurrencyTests
        return FlowCardConcurrencyTests.parallel(self, path, bodies)

    def test_same_run_different_keys_cannot_issue_second_physical_set(self):
        from rest_framework.test import APIClient
        bodies = [{**self.body, "request_key": uuid.uuid4().hex} for _ in range(2)]
        self.parallel("/api/flow-cards/groups/", bodies)
        client = APIClient(); client.force_authenticate(self.user)
        if not CardGroup.objects.exists():
            self.assertEqual(client.post("/api/flow-cards/groups/", bodies[0], format="json").status_code, 201)
        self.assertEqual(CardGroup.objects.count(), 1)
        group = CardGroup.objects.get()
        self.parallel(f"/api/flow-cards/groups/{group.pk}/generate/", [{"expected_revision": 1}] * 2)
        retry = client.post(f"/api/flow-cards/groups/{group.pk}/generate/", {"expected_revision": 1}, format="json")
        self.assertEqual(retry.status_code, 200, retry.content)
        self.assertEqual(InternalCard.objects.count(), 3)

    def test_two_editors_keep_first_save_and_reject_stale_revision(self):
        from rest_framework.test import APIClient
        group = p.save_draft(self.body, self.user)
        bodies = [{**self.body, "request_key": uuid.uuid4().hex, "group_id": str(group.pk), "expected_revision": 1, "preparation_notes": text} for text in ("设备A", "设备B")]
        statuses = self.parallel("/api/flow-cards/groups/", bodies)
        if all(status == 503 for status in statuses):
            client = APIClient(); client.force_authenticate(self.user)
            self.assertEqual(client.post("/api/flow-cards/groups/", bodies[0], format="json").status_code, 201)
        group.refresh_from_db()
        self.assertEqual(group.revision, 2)
        self.assertIn(group.snapshot["preparation_notes"], ("设备A", "设备B"))
        self.assertEqual(CardGroup.objects.count(), 1)
