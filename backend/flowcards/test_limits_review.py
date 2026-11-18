"""Second-round synthetic acceptance. No production DB or customer tolerances."""
import copy
import uuid
from unittest.mock import patch
from decimal import Decimal
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from quality.tests.helpers import QualityTestMixin
from orders.models import ProductSpecification
from . import test_review as review_tests
from . import tests as card_tests
from .models import CardEvent, AppearanceIssue
from . import services as s
from .limits import evaluate, limit_text


@override_settings(FLOW_CARDS_WRITE_ENABLED=True)
class FlowLimitsReviewTests(QualityTestMixin, TestCase):
    plan = card_tests.FlowCardTests.plan
    generated = card_tests.FlowCardTests.generated
    standard = review_tests.FlowReviewTests.standard
    bake_body = review_tests.FlowReviewTests.bake_body

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.product = ProductSpecification.objects.create(specification="隔离100x3", material="ISOLATED")
        cls.order.product_specification = cls.product
        cls.order.specification, cls.order.material, cls.order.source_system = cls.product.specification, cls.product.material, "翔邦"
        cls.order.save()

    def criterion(self, **extra):
        return {"name": "线径", "stage": "DIMENSION", "lower": "", "upper": "3.05", "unit": "mm", "method": "隔离批准示例方法",
                "limit_mode": "UPPER", "upper_inclusive": True, **extra}

    def card(self, **extra):
        st = self.standard(dimension_count=1, criteria=[self.criterion(**extra)])
        return self.generated(standard_id=st.pk, planned_molds=100, requirements_review="隔离测试逐次明确使用本次要求，不更改旧版").cards.get()

    def reading(self, card, actual="3.06", **extra):
        c = card.group.snapshot["standard"]["snapshot"]["criteria"][0]
        return {"request_key": uuid.uuid4().hex, "kind": "DIMENSION", "card_ids": [str(card.pk)], "employee_id": self.inspector.pk,
            "business_at": timezone.now().isoformat(), "scope": "本次抽测", "evidence": "隔离读数，非客户参数", "values": {
                "state": "MEASURED", "actual": actual, "criterion_id": c["id"], "unit": c["unit"], "method": c["method"]}, **extra}

    def test_upper_boundary_snapshot_source_and_product_persistence(self):
        card = self.card()
        c = card.group.snapshot["standard"]["snapshot"]["criteria"][0]
        self.assertEqual(c["lower"], "")
        self.assertEqual(c["limit_mode"], "UPPER")
        self.assertTrue(c["source"])
        self.assertEqual(s.ProductInspectionCriterion.objects.get(pk=c["id"]).raw_data["limit_mode"], "UPPER")
        context = self.client.get("/api/flow-cards/context/", {"product_id": self.product.pk}).json()
        option = next(row for row in context["criteria"] if row["id"] == c["id"])
        self.assertEqual(option["limit_mode"], "UPPER")
        self.assertEqual(option["method"], c["method"])
        self.assertTrue({"order_id", "source_sheet", "source_row"} <= option.keys())
        for actual, judgment, text in [("3.04", "WITHIN", "未超上限"), ("3.05", "WITHIN", "达到上限，未超"), ("3.06", "OUTSIDE", "超上限 0.01 mm")]:
            event = s.record_event(self.reading(card, actual), self.user)
            self.assertEqual(event.values["judgment"], judgment)
            self.assertEqual(event.values["measurement_message"], text)
        result = self.client.get(f"/api/flow-cards/cards/{card.pk}/").json()
        self.assertEqual(len(result["measurement_alerts"]), 1)
        self.assertEqual(result["measurement_alerts"][0]["values"]["difference"], "0.01")

    def test_range_keeps_lower_and_strict_upper_preserved(self):
        for settings, actual, judgment, text in [
            ({"lower": "2.95", "limit_mode": "RANGE"}, "2.94", "OUTSIDE", "低于下限 0.01 mm"),
            ({"upper_inclusive": False}, "3.05", "OUTSIDE", "达到不包含的上限")]:
            e = s.record_event(self.reading(self.card(**settings), actual), self.user)
            self.assertEqual((e.values["judgment"], e.values["measurement_message"]), (judgment, text))
        with self.assertRaises(ValidationError):
            self.card(lower="2.95")
        with self.assertRaises(ValidationError):
            self.card(limit_mode="RANGE")

    def test_pending_missing_actual_unknown_unit_and_true_zero_distinct(self):
        card = self.card(limit_mode="PENDING", upper="")
        e = s.record_event(self.reading(card), self.user)
        self.assertEqual(e.values["judgment"], "NOT_EVALUATED")
        self.assertIn("待确认", e.values["measurement_message"])
        for value in [None, ""]:
            with self.assertRaises(ValidationError):
                s.record_event(self.reading(card, value), self.user)
        for key in ("unit", "method"):
            unknown = self.card(**{key: ""})
            e = s.record_event(self.reading(unknown), self.user)
            self.assertEqual(e.values["judgment"], "NOT_EVALUATED")
        zero = s.record_event(self.reading(self.card(), "0"), self.user)
        self.assertEqual(zero.values["actual"], "0")
        self.assertEqual(zero.values["judgment"], "WITHIN")

    def test_unmeasured_rejects_number_and_never_accepts_client_judgment(self):
        card = self.card()
        for state in ("UNKNOWN", "NOT_MEASURED", "NOT_APPLICABLE"):
            bad = self.reading(card)
            bad["values"]["state"] = state
            with self.assertRaises(ValidationError):
                s.record_event(bad, self.user)
        body = self.reading(card, None)
        body["values"].update(state="NOT_MEASURED", judgment="WITHIN", difference="0", standard_snapshot={})
        e = s.record_event(body, self.user)
        self.assertNotIn("judgment", e.values)
        detail = self.client.get(f"/api/flow-cards/cards/{card.pk}/").json()
        self.assertEqual(detail["measurement_status"][0]["message"], "未测")

    def test_retry_retest_keeps_original_and_processing_person(self):
        card = self.card()
        body = self.reading(card)
        first = s.record_event(body, self.user)
        self.assertEqual(s.record_event(body, self.user).pk, first.pk)
        self.assertEqual(CardEvent.objects.count(), 1)
        not_judged = self.reading(card, "3.04", replaces_id=str(first.pk))
        not_judged["values"]["method"] = "方法待确认"
        second = s.record_event(not_judged, self.user)
        alerts = self.client.get(f"/api/flow-cards/cards/{card.pk}/").json()["measurement_alerts"]
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["review_target"]["id"], str(second.pk))
        final = s.record_event(self.reading(card, "3.04", replaces_id=str(second.pk)), self.user)
        self.assertEqual(len(self.client.get(f"/api/flow-cards/cards/{card.pk}/").json()["measurement_alerts"]), 0)
        first.refresh_from_db()
        self.assertEqual(first.values["actual"], "3.06")
        self.assertEqual(final.employee_id, self.inspector.pk)
        with self.assertRaises(ValidationError):
            s.record_event(self.reading(card, "3.04", replaces_id=str(first.pk)), self.user)

    def test_legacy_pdf_frozen_and_paper_exact_lookup(self):
        from .pdf import render_cards
        with patch.object(s, "TEMPLATE", {**s.TEMPLATE, "renderer": "flowcard-review-20261118-v2"}):
            old = self.card(limit_mode="RANGE", lower="2.95")
        original = render_cards([(old, 0)])
        self.card()
        old.group.refresh_from_db()
        self.assertEqual(original, render_cards([(old, 0)]))
        card = self.card()
        first, tail = card.card_no.rsplit("-", 1)
        for code in (f"ERPFC:{card.pk}", f"{first}-\n{tail}", card.card_no.lower()):
            response = self.client.get("/api/flow-cards/cards/scan/", {"code": code})
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual(response.json()["id"], str(card.pk))
        self.assertEqual(self.client.get("/api/flow-cards/cards/scan/", {"code": card.card_no + "X"}).status_code, 404)
        self.assertEqual(self.client.get("/api/flow-cards/cards/scan/", {"code": tail}).status_code, 400)

    def test_coupon_shortcut_source_covers_two_cards_and_second_bake_remains_unmeasured(self):
        config = {"object": "COUPON", "scale": "示例量表"}
        st = self.standard(hardness={"BAKED_HARDNESS": config}, criteria=[self.criterion(name="烤后硬度", stage="BAKED_HARDNESS", lower="60", upper="70", limit_mode="RANGE", unit="示例量表")])
        cards = list(self.generated(standard_id=st.pk, planned_molds=200).cards.all())
        bake = self.bake_body(cards)
        sample = {"sample_ref": "本厂胶片A", "material_ref": "当批料A", "trays": [1], "card_ids": [str(c.pk) for c in cards], "accompanied": True}
        bake["values"]["coupon_samples"] = [sample]
        first = s.record_event(bake, self.user)
        body = self.reading(cards[0], "65", kind="BAKED_HARDNESS", card_ids=sample["card_ids"])
        body["values"].update(measurement_object="COUPON", sample_ref=sample["sample_ref"], material_ref=sample["material_ref"], bake_event_id=str(first.pk))
        reading = s.record_event(body, self.user)
        self.assertEqual(reading.cards.count(), 2)
        second_bake = self.bake_body([cards[0]])
        second = s.record_event(second_bake, self.user)
        status = self.client.get(f"/api/flow-cards/cards/{cards[0].pk}/").json()["bake_measurement_status"]
        self.assertEqual(len(status), 2)
        self.assertEqual(status[0]["checks"][0]["actual"], "65")
        self.assertEqual(status[1]["checks"], [])
        self.assertIn("未测", status[1]["notice"])
        options = self.client.get(f"/api/flow-cards/cards/{cards[0].pk}/bake-samples/").json()
        self.assertEqual(options["count"], 2)
        self.assertEqual(options["results"][0]["values"]["coupon_samples"], [])
        bad = copy.deepcopy(body); bad["request_key"] = uuid.uuid4().hex; bad["values"]["bake_event_id"] = str(second.pk)
        with self.assertRaises(ValidationError):
            s.record_event(bad, self.user)
        for card in cards:
            events = self.client.get(f"/api/flow-cards/cards/{card.pk}/events/").json()["results"]
            self.assertEqual([e["id"] for e in events if e["kind"] == "BAKED_HARDNESS"], [str(reading.pk)])

    def test_none_does_not_consume_dimension_count_or_invent_line_diameter(self):
        st = self.standard(dimension_count=0, criteria=[self.criterion(limit_mode="NONE")])
        self.assertEqual(st.snapshot["dimension_count"], 0)
        self.assertEqual(limit_text(st.snapshot["criteria"][0]), "不要求测")
        self.assertEqual(evaluate({"numeric": True, "lower": "1", "upper": "2", "unit": "mm", "method": "已确认"}, Decimal("1"), True)["judgment"], "WITHIN")

    def test_compact_identity_preserves_short_code_and_fits_one_line(self):
        from .pdf_v3 import compact_identity
        from .pdf import font_name, wrap
        from reportlab.lib.units import mm
        p = {"customer_product_no": "SHORT-01", "material": "DEMO", "specification": "隔离中文长规格" * 50}
        text = compact_identity(p, font_name(), 176*mm)
        self.assertIn("SHORT-01", text)
        self.assertIn("DEMO", text)
        self.assertEqual(len(wrap(text, 176*mm, font_name(), 9)), 1)
