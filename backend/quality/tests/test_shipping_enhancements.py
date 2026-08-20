from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from quality.models import (
    ProcessCard,
    ProductUnitWeight,
    QualityEmployee,
    QualityOrder,
    QualityReworkCase,
    QualityShipment,
    QualityShipmentBatch,
    QualityShipmentLine,
)

from .helpers import QualityTestMixin, response_results


class ShippingEnhancementApiTests(QualityTestMixin, TestCase):
    def card(self, number="PC-ENH", quantity=1000, unit="2.5"):
        return ProcessCard.objects.create(
            card_no=number,
            order=self.order,
            quantity=quantity,
            unit_weight_g=unit,
            created_by=self.user,
        )

    def test_multiple_inspectors_mirror_legacy_inspector(self):
        second = QualityEmployee.objects.create(
            employee_no="QC-ENH-2", name="乙品检", role=QualityEmployee.Role.BOTH
        )
        card = self.card()
        response = self.client.post(
            "/api/quality/shipment-batches/",
            {
                "shipment_date": timezone.localdate().isoformat(),
                "inspector_ids": [second.pk, self.inspector.pk],
                "lines": [{"process_card_id": card.pk, "net_weight_kg": "2.500"}],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        batch = QualityShipmentBatch.objects.get(pk=response.json()["id"])
        self.assertEqual(batch.inspector_id, second.pk)
        self.assertEqual(set(batch.inspectors.values_list("pk", flat=True)), {second.pk, self.inspector.pk})
        self.assertEqual(response.json()["inspector_ids"], [second.pk, self.inspector.pk])

    def test_server_calculates_piece_quantity_and_preserves_snapshots_on_confirm(self):
        card = self.card(number="PC-ENH-CALC", quantity=1000, unit="2")
        response = self.client.post(
            "/api/quality/shipment-batches/",
            {
                "shipment_date": timezone.localdate().isoformat(),
                "inspector_ids": [self.inspector.pk],
                "lines": [{
                    "process_card_id": card.pk,
                    "net_weight_kg": "1.000",
                    "unit_weight_g": "2",
                    "specification_snapshot": self.order.specification,
                    "material_snapshot": self.order.material,
                }],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        line = QualityShipmentLine.objects.get(batch_id=response.json()["id"])
        self.assertEqual(line.piece_quantity, 500)
        self.assertEqual(self.client.post(f"/api/quality/shipment-batches/{response.json()['id']}/confirm/", {}, format="json").status_code, 200)
        line.refresh_from_db()
        self.assertEqual(line.unit_weight_g_snapshot, Decimal("2.00000"))
        self.assertEqual(line.specification_snapshot, self.order.specification)
        self.assertTrue(ProductUnitWeight.objects.filter(product_specification=line.product_specification, unit_weight_g=Decimal("2.00000")).exists())

    def test_direct_order_line_and_candidate_endpoint(self):
        response = self.client.get(
            "/api/quality/shipping-candidates/",
            {"specification": self.order.specification, "material": self.order.material},
        )
        self.assertEqual(response.status_code, 200, response.content)
        candidates = response_results(response)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["order_id"], self.order.pk)
        batch = self.client.post(
            "/api/quality/shipment-batches/",
            {
                "order_id": self.order.pk,
                "inspector_ids": [self.inspector.pk],
                "specification_snapshot": self.order.specification,
                "material_snapshot": self.order.material,
                "unit_weight_g": "2",
                "total_net_weight_kg": "1.000",
                "lines": [{
                    "order_id": self.order.pk,
                    "specification_snapshot": self.order.specification,
                    "material_snapshot": self.order.material,
                    "unit_weight_g": "2",
                    "net_weight_kg": "1.000",
                }],
            },
            format="json",
        )
        self.assertEqual(batch.status_code, 201, batch.content)
        self.assertEqual(self.client.post(f"/api/quality/shipment-batches/{batch.json()['id']}/confirm/", {}, format="json").status_code, 200)
        line = QualityShipmentLine.objects.get(batch_id=batch.json()["id"])
        self.assertIsNone(line.process_card_id)
        self.assertEqual(line.order_id, self.order.pk)
        self.assertEqual(line.piece_quantity, 500)

    def test_batch_total_weight_is_copied_to_line_and_server_derives_quantity(self):
        response = self.client.post(
            "/api/quality/shipment-batches/",
            {
                "shipment_no": "WEIGHT-TOTAL-1",
                "order_id": self.order.pk,
                "specification_snapshot": self.order.specification,
                "material_snapshot": self.order.material,
                "inspector_ids": [self.inspector.pk],
                "unit_weight_g": "2",
                "total_net_weight_kg": "1.000",
                # Deliberately omit line weight and send a wrong quantity.
                # The parent total must be propagated and the server must
                # derive 500 pieces rather than trusting 999.
                "piece_quantity": 999,
                "product_batch_count": 2,
                "pieces_per_batch": 300,
                "lines": [{"order_id": self.order.pk, "piece_quantity": 999}],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        line = QualityShipmentLine.objects.get(batch_id=response.json()["id"])
        self.assertEqual(line.net_weight_kg, Decimal("1.000"))
        self.assertEqual(line.piece_quantity, 500)
        self.assertEqual(line.product_batch_count, 2)
        self.assertEqual(line.pieces_per_batch, 300)
        confirmed = self.client.post(
            f"/api/quality/shipment-batches/{response.json()['id']}/confirm/", {}, format="json"
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        line.refresh_from_db()
        self.assertEqual(line.piece_quantity, 500)

    def test_batch_total_weight_alias_rejects_multiple_lines(self):
        first = self.card("PC-TOTAL-MULTI-1")
        second = self.card("PC-TOTAL-MULTI-2")
        response = self.client.post(
            "/api/quality/shipment-batches/",
            {
                "total_net_weight_kg": "2.000",
                "unit_weight_g": "2",
                "lines": [
                    {"process_card_id": first.pk, "unit_weight_g": "2"},
                    {"process_card_id": second.pk, "unit_weight_g": "2"},
                ],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("total_net_weight_kg", response.json())

    def test_shipment_number_draft_can_resume_but_confirmed_or_void_cannot_reuse(self):
        first = self.client.post(
            "/api/quality/shipment-batches/",
            {
                "shipment_no": "RESUME-001",
                "client_key": "resume-original-key",
                "shipment_date": timezone.localdate().isoformat(),
                "inspector_ids": [self.inspector.pk],
                "lines": [{"process_card_id": self.card().pk, "net_weight_kg": "1.000"}],
            },
            format="json",
        )
        self.assertEqual(first.status_code, 201, first.content)
        first_id = first.json()["id"]
        resuming_user = get_user_model().objects.create_user(
            username="shipment-resume-user", password="resume-password"
        )
        self.client.force_authenticate(resuming_user)
        resumed = self.client.post(
            "/api/quality/shipment-batches/",
            {
                "shipment_no": "resume-001",
                "client_key": "resume-new-key-must-not-replace",
                "notes": "继续填写",
                "lines": [{"process_card_id": self.card("PC-RESUME-2").pk, "net_weight_kg": "1.000"}],
            },
            format="json",
        )
        self.assertEqual(resumed.status_code, 200, resumed.content)
        self.assertEqual(resumed.json()["id"], first_id)
        self.assertEqual(resumed.json()["notes"], "继续填写")
        resumed_batch = QualityShipmentBatch.objects.get(pk=first_id)
        self.assertEqual(resumed_batch.client_key, "resume-original-key")
        self.assertEqual(resumed_batch.created_by_id, self.user.pk)
        self.client.force_authenticate(self.user)
        check = self.client.get(
            "/api/quality/shipment-batches/check-shipment-no/",
            {"shipment_no": "RESUME-001"},
        )
        self.assertEqual(check.status_code, 200)
        self.assertTrue(check.json()["can_resume"])

        self.assertEqual(
            self.client.post(
                f"/api/quality/shipment-batches/{first_id}/confirm/", {}, format="json"
            ).status_code,
            200,
        )
        confirmed_reuse = self.client.post(
            "/api/quality/shipment-batches/",
            {"shipment_no": "RESUME-001"},
            format="json",
        )
        self.assertEqual(confirmed_reuse.status_code, 400)

        void = self.client.post(
            "/api/quality/shipment-batches/",
            {
                "shipment_no": "VOID-001",
                "lines": [{"process_card_id": self.card("PC-VOID").pk, "net_weight_kg": "1.000"}],
            },
            format="json",
        )
        self.assertEqual(void.status_code, 201, void.content)
        self.assertEqual(
            self.client.post(
                f"/api/quality/shipment-batches/{void.json()['id']}/void/", {}, format="json"
            ).status_code,
            200,
        )
        void_reuse = self.client.post(
            "/api/quality/shipment-batches/",
            {"shipment_no": "VOID-001"},
            format="json",
        )
        self.assertEqual(void_reuse.status_code, 400)

    def test_weighted_number_cannot_collide_with_legacy_shipment(self):
        QualityShipment.objects.create(
            shipment_no="LEGACY-NO-1",
            shipment_date=timezone.localdate(),
            order=self.order,
            inspector=self.inspector,
            inspection_quantity=10,
            qualified_quantity=10,
            defective_quantity=0,
            shipped_quantity=10,
            created_by=self.user,
        )
        response = self.client.post(
            "/api/quality/shipment-batches/",
            {"shipment_no": "legacy-no-1"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_free_order_cumulative_quantity_and_weight_caps_include_previous_lines(self):
        first = self.client.post(
            "/api/quality/shipment-batches/",
            {
                "shipment_no": "FREE-CAP-1",
                "order_id": self.order.pk,
                "inspector_ids": [self.inspector.pk],
                "specification_snapshot": self.order.specification,
                "material_snapshot": self.order.material,
                "unit_weight_g": "2",
                "total_net_weight_kg": "1.000",
                "lines": [{"order_id": self.order.pk}],
            },
            format="json",
        )
        self.assertEqual(first.status_code, 201, first.content)
        self.assertEqual(
            self.client.post(
                f"/api/quality/shipment-batches/{first.json()['id']}/confirm/", {}, format="json"
            ).status_code,
            200,
        )
        second = self.client.post(
            "/api/quality/shipment-batches/",
            {
                "shipment_no": "FREE-CAP-2",
                "order_id": self.order.pk,
                "inspector_ids": [self.inspector.pk],
                "specification_snapshot": self.order.specification,
                "material_snapshot": self.order.material,
                "unit_weight_g": "2",
                "total_net_weight_kg": "1.100",
                "lines": [{"order_id": self.order.pk}],
            },
            format="json",
        )
        self.assertEqual(second.status_code, 201, second.content)
        confirmed = self.client.post(
            f"/api/quality/shipment-batches/{second.json()['id']}/confirm/", {}, format="json"
        )
        self.assertEqual(confirmed.status_code, 400, confirmed.content)
        self.assertIn("订单", str(confirmed.json()))

    def test_confirmation_requires_inspector_and_accepts_line_unit_weight_for_card(self):
        card = self.card("PC-LINE-UNIT", quantity=500, unit=None)
        draft = self.client.post(
            "/api/quality/shipment-batches/",
            {
                "shipment_no": "LINE-UNIT-1",
                "lines": [{
                    "process_card_id": card.pk,
                    "unit_weight_g": "2",
                    "net_weight_kg": "1.000",
                }],
            },
            format="json",
        )
        self.assertEqual(draft.status_code, 201, draft.content)
        missing_inspector = self.client.post(
            f"/api/quality/shipment-batches/{draft.json()['id']}/confirm/", {}, format="json"
        )
        self.assertEqual(missing_inspector.status_code, 400)
        self.assertIn("inspector_ids", missing_inspector.json())
        patched = self.client.patch(
            f"/api/quality/shipment-batches/{draft.json()['id']}/",
            {"inspector_ids": [self.inspector.pk]},
            format="json",
        )
        self.assertEqual(patched.status_code, 200, patched.content)
        confirmed = self.client.post(
            f"/api/quality/shipment-batches/{draft.json()['id']}/confirm/", {}, format="json"
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        card.refresh_from_db()
        self.assertEqual(card.unit_weight_g, Decimal("2.00000"))

    def test_direct_order_customer_return_reopens_candidate_and_order_remaining(self):
        draft = self.client.post(
            "/api/quality/shipment-batches/",
            {
                "shipment_no": "DIRECT-RETURN-1",
                "shipment_date": timezone.localdate().isoformat(),
                "order_id": self.order.pk,
                "inspector_ids": [self.inspector.pk],
                "specification_snapshot": self.order.specification,
                "material_snapshot": self.order.material,
                "unit_weight_g": "2",
                "total_net_weight_kg": "2.000",
                "lines": [{"order_id": self.order.pk}],
            },
            format="json",
        )
        self.assertEqual(draft.status_code, 201, draft.content)
        self.assertEqual(
            self.client.post(
                f"/api/quality/shipment-batches/{draft.json()['id']}/confirm/", {}, format="json"
            ).status_code,
            200,
        )
        line_id = QualityShipmentLine.objects.get(batch_id=draft.json()["id"]).pk
        returned = self.client.post(
            "/api/quality/rework-cases/",
            {
                "origin": "CUSTOMER_RETURN",
                "shipment_line_id": line_id,
                "affected_quantity": 100,
            },
            format="json",
        )
        self.assertEqual(returned.status_code, 201, returned.content)
        case = QualityReworkCase.objects.get(pk=returned.json()["id"])
        self.assertIsNone(case.process_card_id)
        self.assertEqual(case.affected_weight_kg, Decimal("0.200"))

        candidates = response_results(
            self.client.get(
                "/api/quality/shipping-candidates/",
                {"order_no": self.order.order_no},
            )
        )
        self.assertEqual(candidates[0]["remaining_quantity"], 100)
        order_payload = self.client.get(
            f"/api/orders/orders/{self.order.pk}/"
        ).json()
        self.assertEqual(order_payload["weighted_shipped_quantity"], 900)
        self.assertEqual(order_payload["weighted_remaining_quantity"], 100)
        self.assertEqual(order_payload["shipment_status"], "PARTIAL")
