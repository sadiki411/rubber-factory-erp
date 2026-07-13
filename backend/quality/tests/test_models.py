from django.core.exceptions import ValidationError
from django.test import TestCase

from quality.models import QualityShipment, ReturnRework

from .helpers import QualityTestMixin


class QualityQuantityValidationTests(QualityTestMixin, TestCase):
    def _shipment(self, **overrides):
        values = {
            "shipment_no": "SHP-MODEL-001",
            "shipment_date": self.order.order_date,
            "order": self.order,
            "inspector": self.inspector,
            "inspection_quantity": 100,
            "qualified_quantity": 90,
            "defective_quantity": 10,
            "shipped_quantity": 80,
            "created_by": self.user,
        }
        values.update(overrides)
        return QualityShipment(**values)

    def _rework(self, shipment, **overrides):
        values = {
            "shipment": shipment,
            "rework_date": shipment.shipment_date,
            "reason_category": ReturnRework.ReasonCategory.DIMENSION,
            "responsible_inspector": self.inspector,
            "rework_employee": self.reworker,
            "returned_quantity": 10,
            "reworked_quantity": 8,
            "recovered_quantity": 7,
            "scrap_quantity": 1,
            "created_by": self.user,
        }
        values.update(overrides)
        return ReturnRework(**values)

    def test_valid_shipment_quantity_balance_passes_model_validation(self):
        shipment = self._shipment()
        shipment.full_clean()
        shipment.save()
        self.assertEqual(shipment.inspection_quantity, 100)
        self.assertEqual(shipment.qualified_quantity + shipment.defective_quantity, 100)

    def test_inspection_must_equal_qualified_plus_defective(self):
        shipment = self._shipment(inspection_quantity=99)
        with self.assertRaises(ValidationError):
            shipment.full_clean()

    def test_shipped_quantity_cannot_exceed_qualified_quantity(self):
        shipment = self._shipment(shipped_quantity=91)
        with self.assertRaises(ValidationError):
            shipment.full_clean()

    def test_rework_quantities_must_follow_returned_processed_result_balance(self):
        shipment = self.create_shipment(
            inspection_quantity=100,
            qualified_quantity=100,
            defective_quantity=0,
            shipped_quantity=100,
        )

        reworked_over_returned = self._rework(
            shipment, returned_quantity=10, reworked_quantity=11
        )
        with self.assertRaises(ValidationError):
            reworked_over_returned.full_clean()

        results_over_reworked = self._rework(
            shipment,
            returned_quantity=10,
            reworked_quantity=8,
            recovered_quantity=8,
            scrap_quantity=1,
        )
        with self.assertRaises(ValidationError):
            results_over_reworked.full_clean()

        valid_pending_rework = self._rework(shipment)
        valid_pending_rework.full_clean()

