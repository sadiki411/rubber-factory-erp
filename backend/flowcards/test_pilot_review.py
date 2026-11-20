"""2026-11-19 P0 regression, synthetic only; no live data or inferred tolerances."""
import copy
import uuid
from datetime import datetime, timedelta
from unittest.mock import patch

from django.test import TestCase, TransactionTestCase, override_settings
from rest_framework.exceptions import ValidationError
from reportlab.pdfgen.canvas import Canvas

from quality.tests.helpers import QualityTestMixin
from orders.models import ProductSpecification
from . import services as s
from .models import CardEvent
from . import test_limits_review as helpers
from . import tests as original_tests
from .measurements import applicable_criteria


@override_settings(FLOW_CARDS_WRITE_ENABLED=True)
class FlowPilotReviewTests(QualityTestMixin, TestCase):
    plan = helpers.FlowLimitsReviewTests.plan
    generated = helpers.FlowLimitsReviewTests.generated
    standard = helpers.FlowLimitsReviewTests.standard
    criterion = helpers.FlowLimitsReviewTests.criterion
    reading = helpers.FlowLimitsReviewTests.reading
    card = helpers.FlowLimitsReviewTests.card
    bake_body = helpers.FlowLimitsReviewTests.bake_body

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.product = ProductSpecification.objects.create(specification="PILOT100x3", material="ISOLATED")
        cls.order.product_specification = cls.product
        cls.order.specification, cls.order.material, cls.order.source_system = cls.product.specification, cls.product.material, "翔邦"
        cls.order.save()

    def text_of_card(self, card):
        texts = []
        class ObserveCanvas(Canvas):
            def drawString(self, x, y, text, *args, **kwargs):
                texts.append(str(text))
                return super().drawString(x, y, text, *args, **kwargs)
        from .pdf import render_cards
        with patch("flowcards.pdf_v3.canvas.Canvas", ObserveCanvas):
            raw = render_cards([(card, 0)])
        self.assertTrue(raw.startswith(b"%PDF"))
        return "\n".join(texts)

    def test_P01_applicable_dimensions_keep_order_not_count_from_another_list(self):
        none = self.criterion(name="内径不测", limit_mode="NONE", upper="")
        line = self.criterion(name="线径必测")
        for rows in ([none, line], [line, none], [line, none, self.criterion(name="高度必测")]):
            st = self.standard(dimension_count=len(applicable_criteria(rows)), criteria=rows)
            card = self.generated(standard_id=st.pk, planned_molds=100).cards.get()
            text = self.text_of_card(card)
            self.assertNotIn("内径不测", text)
            self.assertIn("线径必测", text)
            self.assertIn("≤ 3.05 mm", text)
            if len(rows) == 3:
                self.assertIn("高度必测", text)
                self.assertLess(text.index("线径必测"), text.index("高度必测"))
            detail = self.client.get(f"/api/flow-cards/cards/{card.pk}/").json()
            self.assertEqual([r["criterion"]["name"] for r in detail["measurement_status"]], [r["name"] for r in applicable_criteria(rows)])
            self.assertEqual(len(st.snapshot["criteria"]), len(rows))

    def test_P01_more_than_two_dimensions_continue_exactly_once_and_all_none(self):
        rows = [self.criterion(name=name) for name in ("线径D1", "内径D2", "高度D3", "槽宽D4")]
        rows.insert(1, self.criterion(name="不测项目X", limit_mode="NONE", upper=""))
        st = self.standard(dimension_count=4, criteria=rows)
        card = self.generated(standard_id=st.pk, planned_molds=100).cards.get()
        text = self.text_of_card(card)
        for name in ("线径D1", "内径D2", "高度D3", "槽宽D4"):
            self.assertEqual(text.count(name), 1)
        self.assertNotIn("不测项目X", text)
        st = self.standard(dimension_count=0, criteria=[self.criterion(name="全部不测", limit_mode="NONE")])
        text = self.text_of_card(self.generated(standard_id=st.pk, planned_molds=100).cards.get())
        self.assertNotIn("全部不测", text)
        self.assertNotIn("关键尺寸", text)

    def retest(self, card, parent, time, **extra):
        body = self.reading(card, "3.04", replaces_id=str(parent.pk), business_at=f"2026-11-19T{time}+08:00")
        body["values"].update({"same_scope_confirmed": True, "review_mode": "RETEST", **extra})
        return body

    def alert_count(self, card):
        return len(self.client.get(f"/api/flow-cards/cards/{card.pk}/").json()["measurement_alerts"])

    def test_P02_earlier_and_same_minute_only_historical_then_real_later_retest(self):
        card = self.card()
        first = s.record_event(self.reading(card, business_at="2026-11-19T11:00:00+08:00"), self.user)
        for time in ("10:00:00", "11:00:45"):
            body = self.retest(card, first, time)
            historical = s.record_event(body, self.user)
            self.assertIsNone(historical.replaces_id)
            self.assertEqual(historical.values["historical_for"], str(first.pk))
            self.assertEqual(historical.values["review_effect"], "HISTORICAL_ONLY")
            self.assertEqual(self.alert_count(card), 1)
        body = self.retest(card, first, "11:20:00")
        final = s.record_event(body, self.user)
        self.assertEqual(final.replaces_id, first.pk)
        self.assertEqual(self.alert_count(card), 0)
        self.assertEqual(s.record_event(body, self.user).pk, final.pk)
        self.assertEqual(CardEvent.objects.count(), 4)
        first.refresh_from_db()
        self.assertEqual(first.values["actual"], "3.06")
        with self.assertRaises(ValidationError):
            s.record_event(self.retest(card, first, "11:21:00"), self.user)

    def test_P02_correction_needs_explicit_mode_reason_scope_and_retains_original(self):
        card = self.card()
        first = s.record_event(self.reading(card, business_at="2026-11-19T11:00:00+08:00"), self.user)
        bad = self.retest(card, first, "10:00:00", review_mode="CORRECTION")
        with self.assertRaises(ValidationError):
            s.record_event(bad, self.user)
        wrong_scope = self.retest(card, first, "11:20:00"); wrong_scope["scope"] = "另一盘"
        with self.assertRaises(ValidationError):
            s.record_event(wrong_scope, self.user)
        missing_confirmation = self.retest(card, first, "11:20:00", same_scope_confirmed=False)
        with self.assertRaises(ValidationError):
            s.record_event(missing_confirmation, self.user)
        corrected = self.retest(card, first, "10:00:00", review_mode="CORRECTION", correction_reason="原读数与时间抄错，核对纸卡更正")
        new = s.record_event(corrected, self.user)
        self.assertEqual(new.values["review_effect"], "CORRECTION")
        self.assertEqual(self.alert_count(card), 0)
        first.refresh_from_db()
        self.assertEqual((first.values["actual"], first.business_at.hour), ("3.06", 3))

    def test_P02_unknown_method_cannot_close_and_legacy_early_link_stays_open(self):
        card = self.card()
        first = s.record_event(self.reading(card, business_at="2026-11-19T11:00:00+08:00"), self.user)
        second = s.record_event(self.retest(card, first, "11:20:00", method="未知测法"), self.user)
        self.assertEqual(self.alert_count(card), 1)
        final = s.record_event(self.retest(card, second, "11:30:00"), self.user)
        self.assertEqual(self.alert_count(card), 0)
        # Synthetic old-format relation; do not rewrite stored legacy data.
        CardEvent.objects.filter(pk=final.pk).update(business_at=datetime.fromisoformat("2026-11-19T10:00:00+08:00"))
        self.assertEqual(self.alert_count(card), 1)

    def test_P02_corrected_time_then_later_retest_does_not_reopen_superseded_alert(self):
        card = self.card()
        first = s.record_event(self.reading(card, business_at="2026-11-19T11:00:00+08:00"), self.user)
        body = self.retest(card, first, "10:00:00", review_mode="CORRECTION", correction_reason="纸卡原测量时间抄错")
        body["values"]["actual"] = "3.06"
        correction = s.record_event(body, self.user)
        self.assertEqual(self.alert_count(card), 1)
        s.record_event(self.retest(card, correction, "10:20:00"), self.user)
        self.assertEqual(self.alert_count(card), 0)
        first.refresh_from_db()
        self.assertEqual(first.values["actual"], "3.06")

    def bake_fixture(self, shared=False):
        config = {"object": "COUPON", "scale": "示例量表"}
        st = self.standard(hardness={"BAKED_HARDNESS": config}, criteria=[self.criterion(name="烤后硬度", stage="BAKED_HARDNESS", lower="60", upper="75", limit_mode="RANGE", unit="示例量表")])
        cards = list(self.generated(standard_id=st.pk, planned_molds=200).cards.all())
        body = self.bake_body(cards)
        sample = {"sample_ref": "S1", "material_ref": "M1", "trays": [1], "card_ids": [str(c.pk) for c in cards] if shared else [str(cards[0].pk)], "accompanied": True}
        body["values"]["coupon_samples"] = [sample]
        bake = s.record_event(body, self.user)
        reading = self.reading(cards[0], "70", kind="BAKED_HARDNESS", card_ids=sample["card_ids"])
        reading["values"].update(measurement_object="COUPON", sample_ref="S1", material_ref="M1", bake_event_id=str(bake.pk))
        return cards, bake, reading

    def test_P03_entry_card_cannot_write_another_packages_coupon(self):
        cards, bake, reading = self.bake_fixture()
        wrong = self.client.post(f"/api/flow-cards/cards/{cards[1].pk}/record/", reading, format="json")
        self.assertEqual(wrong.status_code, 400, wrong.content)
        self.assertIn("当前入口卡", wrong.json()["entry_card"])
        self.assertEqual(self.client.post("/api/flow-cards/events/", reading, format="json").status_code, 400)
        options = self.client.get(f"/api/flow-cards/cards/{cards[1].pk}/bake-samples/").json()
        self.assertEqual(options["results"][0]["values"]["coupon_samples"], [])
        saved = self.client.post(f"/api/flow-cards/cards/{cards[0].pk}/record/", reading, format="json")
        self.assertEqual(saved.status_code, 201, saved.content)
        self.assertEqual(saved.json()["values"]["entry_card_id"], str(cards[0].pk))
        self.assertEqual(self.client.post(f"/api/flow-cards/cards/{cards[1].pk}/record/", reading, format="json").status_code, 400)

    def test_P03_shared_coupon_keeps_full_set_and_no_generic_latest_baked_normal(self):
        cards, bake, reading = self.bake_fixture(shared=True)
        partial = copy.deepcopy(reading); partial["card_ids"] = partial["card_ids"][:1]
        self.assertEqual(self.client.post(f"/api/flow-cards/cards/{cards[0].pk}/record/", partial, format="json").status_code, 400)
        event = s.record_event(reading, self.user, entry_card=cards[0].pk)
        self.assertEqual(event.cards.count(), 2)
        second_body = self.bake_body([cards[0]])
        second_body["values"]["card_trays"][0].update(trays=[7], scope="第二次仅盘7")
        # Keep this second bake later even after the original calendar date passes.
        second_body["business_at"] = (bake.business_at + timedelta(hours=1)).isoformat()
        s.record_event(second_body, self.user)
        detail = self.client.get(f"/api/flow-cards/cards/{cards[0].pk}/").json()
        self.assertFalse(any(m["criterion"]["stage"] == "BAKED_HARDNESS" for m in detail["measurement_status"]))
        self.assertEqual(detail["bake_measurement_status"][0]["checks"][0]["actual"], "70")
        self.assertEqual(detail["bake_measurement_status"][1]["checks"], [])
        self.assertEqual(detail["bake_measurement_status"][1]["trays"], [7])
        self.assertEqual(detail["bake_measurement_status"][1]["scope"], "第二次仅盘7")
        self.assertIn("未测", detail["bake_measurement_status"][1]["notice"])
        self.assertEqual(detail["baked_product_measurements"], [])
        for card in cards:
            events = self.client.get(f"/api/flow-cards/cards/{card.pk}/events/").json()["results"]
            self.assertEqual([e["id"] for e in events if e["kind"] == "BAKED_HARDNESS"], [str(event.pk)])

    def test_P03_product_measurement_keeps_own_provenance_separate_from_coupon(self):
        cards, bake, reading = self.bake_fixture()
        product = copy.deepcopy(reading)
        product["request_key"] = uuid.uuid4().hex
        product["values"].update(measurement_object="PRODUCT", object_change_reason="隔离成品另测")
        for key in ("bake_event_id", "sample_ref", "material_ref"):
            product["values"].pop(key)
        saved = s.record_event(product, self.user, entry_card=cards[0].pk)
        detail = self.client.get(f"/api/flow-cards/cards/{cards[0].pk}/").json()
        self.assertEqual(detail["bake_measurement_status"][0]["checks"][0]["message"], "本次胶片未测")
        self.assertEqual(detail["baked_product_measurements"][0]["id"], str(saved.pk))
        self.assertEqual(detail["baked_product_measurements"][0]["values"]["measurement_object"], "PRODUCT")

    def test_P01_frozen_v3_default_bytes_unchanged_when_v4_enabled(self):
        from .pdf_v3 import render_cards_v3
        from .pdf_v4 import render_cards_v4
        from .pdf import render_cards
        card = self.card()
        before = render_cards_v3([(card, 0)])
        render_cards_v4([(card, 0)])
        self.assertEqual(before, render_cards_v3([(card, 0)]))
        card.group.snapshot["template"]["renderer"] = "flowcard-review-20261118-v3"
        self.assertEqual(before, render_cards([(card, 0)]))


@override_settings(FLOW_CARDS_WRITE_ENABLED=True)
class FlowPilotConcurrencyTests(TransactionTestCase):
    setUp = original_tests.FlowCardConcurrencyTests.setUp
    parallel = original_tests.FlowCardConcurrencyTests.parallel

    def test_P02_two_devices_retest_same_card_no_fork_and_lost_response_retry(self):
        from rest_framework.test import APIClient
        from quality.models import QualityEmployee
        from .models import AppearanceIssue
        employee = QualityEmployee.objects.create(employee_no="PILOT-RACE", name="隔离双设备", role="BOTH")
        st = s.confirm_standard({"request_key": uuid.uuid4().hex, "product_id": self.body["product_id"],
            "source": "隔离并发标准", "appearance_ids": list(AppearanceIssue.objects.values_list("pk", flat=True))[:4],
            "dimension_count": 1, "whole_mold": "NOT_REQUIRED", "criteria": [{"name": "线径", "stage": "DIMENSION", "upper": "3.05", "limit_mode": "UPPER", "unit": "mm", "method": "隔离测法"}]}, self.user)
        group = s.generate_group(s.create_group({**self.body, "planned_molds": 100, "standard_id": st.pk}, self.user), self.user)
        card = group.cards.get()
        criterion = group.snapshot["standard"]["snapshot"]["criteria"][0]
        body = {"request_key": uuid.uuid4().hex, "card_ids": [str(card.pk)], "kind": "DIMENSION", "employee_id": employee.pk,
            "business_at": "2026-11-19T11:00:00+08:00", "scope": "同包同盘", "values": {"state": "MEASURED", "actual": "3.06", "unit": "mm", "method": "隔离测法", "criterion_id": criterion["id"]}}
        parent = s.record_event(body, self.user)
        attempts = [{**body, "request_key": f"PILOT-RETEST-{i}", "business_at": "2026-11-19T11:20:00+08:00", "replaces_id": str(parent.pk),
            "values": {**body["values"], "actual": "3.04", "same_scope_confirmed": True, "review_mode": "RETEST"}} for i in (1, 2)]
        path = f"/api/flow-cards/cards/{card.pk}/record/"
        statuses = self.parallel(path, attempts)
        client = APIClient(); client.force_authenticate(self.user)
        if not CardEvent.objects.filter(replaces=parent).exists():
            self.assertEqual(statuses, [503, 503])
            self.assertEqual(client.post(path, attempts[0], format="json").status_code, 201)
        self.assertEqual(CardEvent.objects.filter(replaces=parent).count(), 1)
        winner = CardEvent.objects.get(replaces=parent)
        retry = client.post(path, next(b for b in attempts if b["request_key"] == winner.request_key), format="json")
        self.assertEqual(retry.status_code, 201)
        self.assertEqual(retry.json()["id"], str(winner.pk))
        loser = next(b for b in attempts if b["request_key"] != winner.request_key)
        self.assertEqual(client.post(path, loser, format="json").status_code, 400)
        self.assertEqual(CardEvent.objects.count(), 2)
