from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from quality.models import (
    ProcessCard,
    QualityReworkCase,
    QualityShipmentBatch,
)

from .helpers import QualityTestMixin


class WeightedWorkflowApiTests(QualityTestMixin, TestCase):
    def card(self, card_no, quantity=1000, unit_weight_g="2.50000"):
        return ProcessCard.objects.create(
            card_no=card_no,
            order=self.order,
            quantity=quantity,
            unit_weight_g=unit_weight_g,
            created_by=self.user,
        )

    def batch(self, key, *lines):
        return self.client.post(
            "/api/quality/shipment-batches/",
            {
                "shipment_date": timezone.localdate().isoformat(),
                "client_key": key,
                "inspector_ids": [self.inspector.pk],
                "lines": list(lines),
            },
            format="json",
        )

    def confirm(self, batch_id):
        return self.client.post(
            f"/api/quality/shipment-batches/{batch_id}/confirm/", {}, format="json"
        )

    def test_multiple_cards_are_created_atomically_as_draft_then_confirmed(self):
        first, second = self.card("PC-MULTI-1"), self.card("PC-MULTI-2")
        response = self.batch(
            "multi-1",
            {"process_card_id": first.pk, "actual_weight_kg": "2.500", "quantity": 1000},
            {"process_card_id": second.pk, "actual_weight_kg": "2.500", "quantity": 1000},
        )
        self.assertEqual(response.status_code, 201, response.content)
        batch_id = response.json()["id"]
        self.assertEqual(response.json()["status"], "DRAFT")
        self.assertEqual(response.json()["line_count"], 2)
        self.assertEqual(first.shipped_net_weight_kg, Decimal("0"))
        self.assertEqual(self.confirm(batch_id).status_code, 200)
        first.refresh_from_db()
        self.assertEqual(first.shipped_net_weight_kg, Decimal("2.500"))

    def test_weight_cap_draft_void_and_idempotency(self):
        card = self.card("PC-CAP")
        first = self.batch("cap-1", {"process_card_id": card.pk, "net_weight_kg": "2.750", "piece_quantity": 1000})
        self.assertEqual(first.status_code, 201, first.content)
        self.assertEqual(self.confirm(first.json()["id"]).status_code, 200)
        extra = self.batch("cap-2", {"process_card_id": card.pk, "net_weight_kg": "0.001"})
        self.assertEqual(extra.status_code, 201, extra.content)
        self.assertEqual(self.confirm(extra.json()["id"]).status_code, 400)
        draft = self.batch("draft-1", {"process_card_id": card.pk, "net_weight_kg": "0.100"})
        self.assertEqual(draft.status_code, 201)
        self.assertEqual(card.shipped_net_weight_kg, Decimal("2.750"))
        void = self.client.post(f"/api/quality/shipment-batches/{draft.json()['id']}/void/", {}, format="json")
        self.assertEqual(void.status_code, 200)
        card.refresh_from_db()
        self.assertEqual(card.shipped_net_weight_kg, Decimal("2.750"))
        same = self.batch("cap-1", {"process_card_id": card.pk, "net_weight_kg": "2.750"})
        self.assertEqual(same.status_code, 201, same.content)
        self.assertEqual(same.json()["id"], first.json()["id"])
        self.assertEqual(QualityShipmentBatch.objects.filter(client_key="cap-1").count(), 1)

    def test_piece_cap_and_confirmed_batches_are_immutable(self):
        card = self.card("PC-PIECES", quantity=10, unit_weight_g="2")
        first = self.batch("pieces-1", {"process_card_id": card.pk, "net_weight_kg": "0.016", "piece_quantity": 8})
        self.assertEqual(self.confirm(first.json()["id"]).status_code, 200)
        second = self.batch("pieces-2", {"process_card_id": card.pk, "net_weight_kg": "0.004", "piece_quantity": 3})
        self.assertEqual(self.confirm(second.json()["id"]).status_code, 400)
        self.assertEqual(self.client.patch(f"/api/quality/shipment-batches/{first.json()['id']}/", {"notes": "x"}, format="json").status_code, 400)
        self.assertEqual(self.client.delete(f"/api/quality/shipment-batches/{first.json()['id']}/").status_code, 405)
        line_id = first.json()["lines"][0]["id"]
        self.assertEqual(
            self.client.patch(
                f"/api/quality/shipment-lines/{line_id}/",
                {"notes": "attempted edit"},
                format="json",
            ).status_code,
            400,
        )
        self.assertEqual(self.client.delete(f"/api/quality/shipment-lines/{line_id}/").status_code, 405)

    def test_missing_unit_weight_cannot_ship(self):
        card = self.card("PC-NO-WEIGHT", unit_weight_g=None)
        response = self.batch("no-weight", {"process_card_id": card.pk, "net_weight_kg": "1.000"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("process_card", response.json())

    def test_rework_link_rules_auto_rounds_and_cumulative_bounds(self):
        card = self.card("PC-REWORK", quantity=10, unit_weight_g="2")
        internal = self.client.post(
            "/api/quality/rework-cases/",
            {"origin": "INTERNAL", "process_card_id": card.pk, "affected_quantity": 4},
            format="json",
        )
        self.assertEqual(internal.status_code, 201, internal.content)
        self.assertTrue(internal.json()["case_no"].startswith("R"))
        for _ in range(4):
            attempt = self.client.post(
                "/api/quality/rework-attempts/",
                {"case_id": internal.json()["id"], "input_quantity": 1, "reworked_quantity": 1},
                format="json",
            )
            self.assertEqual(attempt.status_code, 201, attempt.content)
        too_many = self.client.post(
            "/api/quality/rework-attempts/",
            {"case_id": internal.json()["id"], "input_quantity": 1, "reworked_quantity": 1},
            format="json",
        )
        self.assertEqual(too_many.status_code, 400)
        draft = self.batch("return-draft", {"process_card_id": card.pk, "net_weight_kg": "0.020", "piece_quantity": 10})
        invalid_return = self.client.post(
            "/api/quality/rework-cases/",
            {"origin": "CUSTOMER_RETURN", "shipment_line_id": draft.json()["lines"][0]["id"], "affected_quantity": 1, "affected_weight_kg": "0.002"},
            format="json",
        )
        self.assertEqual(invalid_return.status_code, 400)
        self.assertEqual(self.confirm(draft.json()["id"]).status_code, 200)
        valid_return = self.client.post(
            "/api/quality/rework-cases/",
            {"origin": "CUSTOMER_RETURN", "shipment_line_id": draft.json()["lines"][0]["id"], "affected_quantity": 1, "affected_weight_kg": "0.002"},
            format="json",
        )
        self.assertEqual(valid_return.status_code, 201, valid_return.content)

    def test_customer_return_reopens_delivery_weight_allowance(self):
        card = self.card("PC-RETURN")
        sent = self.batch("return-1", {"process_card_id": card.pk, "net_weight_kg": "2.750", "piece_quantity": 1000})
        self.assertEqual(self.confirm(sent.json()["id"]).status_code, 200)
        returned = self.client.post(
            "/api/quality/rework-cases/",
            {"origin": "CUSTOMER_RETURN", "shipment_line_id": sent.json()["lines"][0]["id"], "affected_quantity": 40, "affected_weight_kg": "0.100"},
            format="json",
        )
        self.assertEqual(returned.status_code, 201, returned.content)
        card.refresh_from_db()
        self.assertEqual(card.delivered_net_weight_kg, Decimal("2.650"))
        resend = self.batch("return-2", {"process_card_id": card.pk, "net_weight_kg": "0.100", "piece_quantity": 40})
        self.assertEqual(self.confirm(resend.json()["id"]).status_code, 200, resend.content)

    def test_missing_date_must_be_filled_before_confirmation_and_status_is_action_only(self):
        card = self.card("PC-DATE")
        draft = self.client.post(
            "/api/quality/shipment-batches/",
            {
                "client_key": "date-1",
                "shipment_date": None,
                "inspector_ids": [self.inspector.pk],
                "lines": [{"process_card_id": card.pk, "net_weight_kg": "2.500", "piece_quantity": 1000}],
            },
            format="json",
        )
        self.assertEqual(draft.status_code, 201, draft.content)
        batch_id = draft.json()["id"]
        self.assertEqual(self.confirm(batch_id).status_code, 400)
        self.assertEqual(
            self.client.patch(
                f"/api/quality/shipment-batches/{batch_id}/",
                {"status": "CONFIRMED"},
                format="json",
            ).status_code,
            400,
        )
        historical = timezone.localdate() - timedelta(days=1)
        patched = self.client.patch(
            f"/api/quality/shipment-batches/{batch_id}/",
            {"shipment_date": historical.isoformat(), "backfill_reason": "补录纸质出货单"},
            format="json",
        )
        self.assertEqual(patched.status_code, 200, patched.content)
        self.assertEqual(self.confirm(batch_id).status_code, 200)

    def test_process_card_summary_exposes_net_piece_count_and_rework_count(self):
        card = self.card("PC-SUMMARY")
        batch = self.batch("summary-1", {"process_card_id": card.pk, "net_weight_kg": "2.500", "piece_quantity": 1000})
        self.assertEqual(self.confirm(batch.json()["id"]).status_code, 200)
        returned = self.client.post(
            "/api/quality/rework-cases/",
            {"origin": "CUSTOMER_RETURN", "shipment_line_id": batch.json()["lines"][0]["id"], "affected_quantity": 40},
            format="json",
        )
        self.assertEqual(returned.status_code, 201, returned.content)
        payload = self.client.get(f"/api/quality/process-cards/{card.pk}/").json()
        self.assertEqual(payload["shipped_quantity"], 1000)
        self.assertEqual(payload["returned_piece_quantity"], 40)
        self.assertEqual(payload["delivered_piece_quantity"], 960)
        self.assertEqual(payload["rework_count"], 1)

    def test_customer_return_cannot_mix_process_cards(self):
        first, second = self.card("PC-MISMATCH-1"), self.card("PC-MISMATCH-2")
        batch = self.batch("mismatch-1", {"process_card_id": first.pk, "net_weight_kg": "2.500", "piece_quantity": 1000})
        self.assertEqual(self.confirm(batch.json()["id"]).status_code, 200)
        response = self.client.post(
            "/api/quality/rework-cases/",
            {
                "origin": "CUSTOMER_RETURN",
                "process_card_id": second.pk,
                "shipment_line_id": batch.json()["lines"][0]["id"],
                "affected_quantity": 1,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.content)
