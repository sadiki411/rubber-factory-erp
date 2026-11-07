from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from quality.models import QualityOrder, QualityShipmentBatch
from quality.prototypes.allocation_review import ReviewChanged, SimulatedConfirmation, preview
from .helpers import QualityTestMixin


class AllocationReviewPrototypeTests(QualityTestMixin, TestCase):
    def other(self, quantity):
        other = QualityOrder.objects.create(order_no="ISOLATED-SECOND", item_no="2", specification=self.order.specification,
            material=self.order.material, order_quantity=quantity, order_date=timezone.localdate(), created_by=self.user)
        self.identify_same_customer_product(self.order, other)
        return other

    def test_65933_preview_reuses_current_rules_and_writes_nothing(self):
        self.order.order_quantity = 47664
        self.order.save()
        other = self.other(18269)
        with CaptureQueriesContext(connection) as queries:
            result = preview(self.order, 65933, [f"ISOLATED-CARD-{n}" for n in range(7)])
            receipt = SimulatedConfirmation().confirm("request-1", result, self.order)
        self.assertEqual([(row["order_id"], row["allocated_quantity"]) for row in result["plan"]["allocations"]], [(self.order.pk, 47664), (other.pk, 18269)])
        self.assertEqual(receipt["mode"], "SIMULATED_ONLY_NO_SHIPMENT_CREATED")
        self.assertFalse(any(row["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for row in queries))
        self.assertEqual(QualityShipmentBatch.objects.count(), 0)

    def test_9597_preview_and_changed_balance_requires_review(self):
        self.order.order_quantity = 7619
        self.order.save()
        other = self.other(125000)
        reviewed = preview(self.order, 9597, ["ISOLATED-CARD"])
        self.assertEqual([row["allocated_quantity"] for row in reviewed["plan"]["allocations"]], [7619, 1978])
        # Deterministic stale-balance interleaving, not a real parallel HTTP test.
        QualityOrder.objects.filter(pk=self.order.pk).update(order_quantity=7000)
        simulator = SimulatedConfirmation()
        with self.assertRaises(ReviewChanged) as error:
            simulator.confirm("stale", reviewed, self.order)
        self.assertEqual([row["allocated_quantity"] for row in error.exception.latest["plan"]["allocations"]], [7000, 2597])
        self.assertEqual(simulator.receipts, {})
        self.assertEqual(other.status, QualityOrder.Status.OPEN)

    def test_lost_response_retry_and_changed_payload_are_distinct(self):
        self.order.order_quantity = 1000
        self.order.save()
        reviewed = preview(self.order, 100, ["ISOLATED-CARD"])
        simulator = SimulatedConfirmation()
        receipt = simulator.confirm("retry-key", reviewed, self.order)
        # Simulate a lost response and subsequent business changes after acceptance.
        QualityOrder.objects.filter(pk=self.order.pk).update(order_quantity=900)
        self.assertIs(simulator.confirm("retry-key", reviewed, self.order), receipt)
        self.assertEqual(len(simulator.receipts), 1)
        with self.assertRaises(ValueError):
            simulator.confirm("retry-key", {**reviewed, "card_numbers": ["DIFFERENT"]}, self.order)
        self.assertEqual(QualityShipmentBatch.objects.count(), 0)
