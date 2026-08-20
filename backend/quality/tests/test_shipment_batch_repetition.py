from decimal import Decimal

from django.test import TestCase

from quality.models import QualityEmployee, QualityShipmentBatch, QualityShipmentLine

from .helpers import QualityTestMixin


class ShipmentBatchRepetitionRegressionTests(QualityTestMixin, TestCase):
    """Regression coverage for repeating one weighed batch several times."""

    endpoint = "/api/quality/shipment-batches/"

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.order.order_quantity = 20_000
        cls.order.save(update_fields=["order_quantity", "updated_at"])
        cls.second_inspector = QualityEmployee.objects.create(
            employee_no="QC-REPEAT-002",
            name="王品检",
            role=QualityEmployee.Role.INSPECTOR,
        )

    def payload(self, **overrides):
        values = {
            "order_id": self.order.pk,
            "specification_snapshot": self.order.specification,
            "material_snapshot": self.order.material,
            "unit_weight_g": "2",
            "single_batch_net_weight_kg": "2.000",
            "process_card_shipment_quantity": 1_050,
            "product_batch_count": 3,
            "lines": [{"order_id": self.order.pk}],
        }
        values.update(overrides)
        return values

    def line_for(self, response):
        return QualityShipmentLine.objects.get(batch_id=response.json()["id"])

    def test_three_equal_batches_expand_piece_count_and_weight_from_scale_values(self):
        response = self.client.post(self.endpoint, self.payload(), format="json")

        self.assertEqual(response.status_code, 201, response.content)
        line = self.line_for(response)
        self.assertEqual(line.single_batch_net_weight_kg, Decimal("2.000"))
        self.assertEqual(line.pieces_per_batch, 1_000)
        self.assertEqual(line.product_batch_count, 3)
        self.assertEqual(line.piece_quantity, 3_000)
        self.assertEqual(line.net_weight_kg, Decimal("6.000"))
        self.assertEqual(line.process_card_shipment_quantity, 1_050)
        self.assertEqual(response.json()["piece_quantity"], 3_000)
        self.assertEqual(Decimal(response.json()["total_net_weight_kg"]), Decimal("6.000"))

    def test_blank_batch_count_means_one_batch(self):
        response = self.client.post(
            self.endpoint,
            self.payload(product_batch_count=None),
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.content)
        line = self.line_for(response)
        self.assertIsNone(line.product_batch_count)
        self.assertEqual(line.pieces_per_batch, 1_000)
        self.assertEqual(line.piece_quantity, 1_000)
        self.assertEqual(line.net_weight_kg, Decimal("2.000"))

    def test_process_card_standard_is_only_a_cap_not_the_calculation_source(self):
        response = self.client.post(
            self.endpoint,
            self.payload(process_card_shipment_quantity=1_075),
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.content)
        line = self.line_for(response)
        self.assertEqual(line.process_card_shipment_quantity, 1_075)
        self.assertEqual(line.pieces_per_batch, 1_000)
        self.assertEqual(line.piece_quantity, 3_000)

    def test_single_batch_at_110_percent_is_allowed_but_one_more_piece_is_rejected(self):
        allowed = self.client.post(
            self.endpoint,
            self.payload(
                unit_weight_g="1",
                single_batch_net_weight_kg="1.100",
                process_card_shipment_quantity=1_000,
                product_batch_count=2,
            ),
            format="json",
        )

        self.assertEqual(allowed.status_code, 201, allowed.content)
        allowed_line = self.line_for(allowed)
        self.assertEqual(allowed_line.pieces_per_batch, 1_100)
        self.assertEqual(allowed_line.piece_quantity, 2_200)

        rejected = self.client.post(
            self.endpoint,
            self.payload(
                unit_weight_g="1",
                single_batch_net_weight_kg="1.101",
                process_card_shipment_quantity=1_000,
                product_batch_count=4,
            ),
            format="json",
        )

        self.assertEqual(rejected.status_code, 400, rejected.content)
        self.assertIn("process_card_shipment_quantity", str(rejected.json()))

    def test_blank_shipment_number_is_generated_and_unique(self):
        first = self.client.post(
            self.endpoint,
            self.payload(shipment_no=""),
            format="json",
        )
        second = self.client.post(
            self.endpoint,
            self.payload(shipment_no=""),
            format="json",
        )

        self.assertEqual(first.status_code, 201, first.content)
        self.assertEqual(second.status_code, 201, second.content)
        first_number = first.json()["shipment_no"]
        second_number = second.json()["shipment_no"]
        self.assertRegex(first_number, r"^QS-\d{8}-[A-F0-9]{8}$")
        self.assertRegex(second_number, r"^QS-\d{8}-[A-F0-9]{8}$")
        self.assertNotEqual(first_number, second_number)

    def test_confirm_without_inspector_then_assign_multiple_inspectors(self):
        draft = self.client.post(self.endpoint, self.payload(), format="json")
        self.assertEqual(draft.status_code, 201, draft.content)

        confirmed = self.client.post(
            f"{self.endpoint}{draft.json()['id']}/confirm/",
            {},
            format="json",
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        self.assertEqual(confirmed.json()["inspector_ids"], [])

        assigned = self.client.post(
            f"{self.endpoint}{draft.json()['id']}/assign-inspectors/",
            {"inspector_ids": [self.second_inspector.pk, self.inspector.pk]},
            format="json",
        )
        self.assertEqual(assigned.status_code, 200, assigned.content)
        self.assertEqual(
            assigned.json()["inspector_ids"],
            [self.second_inspector.pk, self.inspector.pk],
        )
        batch = QualityShipmentBatch.objects.get(pk=draft.json()["id"])
        self.assertEqual(batch.inspector_id, self.second_inspector.pk)
        self.assertEqual(
            set(batch.inspectors.values_list("pk", flat=True)),
            {self.second_inspector.pk, self.inspector.pk},
        )

    def test_void_batch_cannot_have_inspectors_assigned(self):
        draft = self.client.post(self.endpoint, self.payload(), format="json")
        self.assertEqual(draft.status_code, 201, draft.content)
        batch_url = f"{self.endpoint}{draft.json()['id']}"
        voided = self.client.post(f"{batch_url}/void/", {}, format="json")
        self.assertEqual(voided.status_code, 200, voided.content)

        assigned = self.client.post(
            f"{batch_url}/assign-inspectors/",
            {"inspector_ids": [self.inspector.pk]},
            format="json",
        )
        self.assertEqual(assigned.status_code, 400, assigned.content)
        self.assertIn("status", assigned.json())

    def test_resaving_reopened_draft_does_not_multiply_accumulated_weight_again(self):
        draft = self.client.post(
            self.endpoint,
            self.payload(shipment_no="QS-REOPEN-REPEAT"),
            format="json",
        )
        self.assertEqual(draft.status_code, 201, draft.content)
        draft_id = draft.json()["id"]

        reopened = self.client.get(f"{self.endpoint}{draft_id}/")
        self.assertEqual(reopened.status_code, 200, reopened.content)
        self.assertEqual(Decimal(reopened.json()["total_net_weight_kg"]), Decimal("6.000"))
        self.assertEqual(
            Decimal(reopened.json()["single_batch_net_weight_kg"]), Decimal("2.000")
        )

        saved_again = self.client.patch(
            f"{self.endpoint}{draft_id}/",
            {
                "single_batch_net_weight_kg": reopened.json()[
                    "single_batch_net_weight_kg"
                ],
                # This is the already expanded display total returned by GET.
                "total_net_weight_kg": reopened.json()["total_net_weight_kg"],
                "product_batch_count": reopened.json()["product_batch_count"],
                "process_card_shipment_quantity": reopened.json()[
                    "process_card_shipment_quantity"
                ],
                "unit_weight_g": reopened.json()["unit_weight_g"],
                "lines": [
                    {
                        "order_id": self.order.pk,
                        "single_batch_net_weight_kg": "2.000",
                        "net_weight_kg": "6.000",
                        "product_batch_count": 3,
                        "process_card_shipment_quantity": 1_050,
                        "unit_weight_g_snapshot": "2",
                    }
                ],
            },
            format="json",
        )

        self.assertEqual(saved_again.status_code, 200, saved_again.content)
        line = QualityShipmentLine.objects.get(batch_id=draft_id)
        self.assertEqual(line.single_batch_net_weight_kg, Decimal("2.000"))
        self.assertEqual(line.net_weight_kg, Decimal("6.000"))
        self.assertEqual(line.pieces_per_batch, 1_000)
        self.assertEqual(line.piece_quantity, 3_000)
