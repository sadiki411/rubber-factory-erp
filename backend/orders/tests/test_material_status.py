from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase

from orders.models import MaterialReceipt
from quality.models import QualityOrder


class OrderMaterialStatusTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="material-status")
        self.client.force_authenticate(self.user)
        self.orders = {}
        for name, required, received, expected in (
            ("unknown", None, "20", "UNKNOWN"),
            ("not_received", "100", "0", "NOT_RECEIVED"),
            ("partial", "100", "99.999", "PARTIAL"),
            ("exact", "100", "100", "SUFFICIENT"),
            ("excess", "100", "100.001", "SUFFICIENT"),
            ("zero_required_zero", "0", "0", "NOT_RECEIVED"),
            ("zero_required_received", "0", "0.001", "SUFFICIENT"),
            ("null_received", "100", None, "NOT_RECEIVED"),
        ):
            self.orders[name] = (self.create_order(name, required, received), expected)

    def create_order(self, name, required="100", received="0", **extra):
        return QualityOrder.objects.create(
            order_no=f"MATERIAL-{name}", specification="STATUS-SPEC", material="N7200",
            order_quantity=1000, created_by=self.user,
            required_material_kg=Decimal(required) if required is not None else None,
            manual_received_material_kg=Decimal(received) if received is not None else None,
            **extra,
        )

    def list_orders(self, **filters):
        response = self.client.get("/api/orders/orders/", {"page_size": 1000, **filters})
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def test_filters_match_list_and_detail_statuses_including_zero_boundaries(self):
        before = list(QualityOrder.objects.order_by("pk").values())
        for status in ("UNKNOWN", "NOT_RECEIVED", "PARTIAL", "SUFFICIENT"):
            with self.subTest(status=status):
                payload = self.list_orders(material_status=status)
                self.assertEqual(
                    {row["id"] for row in payload["results"]},
                    {order.pk for order, expected in self.orders.values() if expected == status},
                )
                self.assertTrue(all(row["material_status"] == status for row in payload["results"]))
        for order, expected in self.orders.values():
            response = self.client.get(f"/api/orders/orders/{order.pk}/")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["material_status"], expected)
        self.assertEqual(before, list(QualityOrder.objects.order_by("pk").values()))

    def test_imported_and_manual_weights_are_added_for_exact_and_excess_receipts(self):
        for name, receipt, expected in (
            ("MIXED-exact", "90", "SUFFICIENT"),
            ("MIXED-excess", "90.001", "SUFFICIENT"),
            ("MIXED-partial", "89.999", "PARTIAL"),
        ):
            order = self.create_order(name, received="10")
            MaterialReceipt.objects.create(
                order=order, order_no=order.order_no, item_no=order.item_no,
                weight_kg=Decimal(receipt), manufactured_on=timezone.localdate(),
            )
            response = self.client.get(f"/api/orders/orders/{order.pk}/")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["material_status"], expected)
        payload = self.list_orders(q="MIXED", material_status="SUFFICIENT")
        self.assertEqual(payload["count"], 2)
        self.assertEqual({row["received_material_kg"] for row in payload["results"]}, {"100.000", "100.001"})

    def test_sufficient_pagination_counts_exact_and_excess_orders_together(self):
        expected = {order.pk for order, status in self.orders.values() if status == "SUFFICIENT"}
        found = set()
        for page in range(1, len(expected) + 1):
            payload = self.list_orders(material_status="SUFFICIENT", page_size=1, page=page, ordering="order_no")
            self.assertEqual(payload["count"], len(expected))
            self.assertEqual(len(payload["results"]), 1)
            found.add(payload["results"][0]["id"])
        self.assertEqual(found, expected)

    def test_sufficient_filter_respects_order_status(self):
        for status in ("COMPLETED", "CANCELLED"):
            order = self.create_order(status, received="200", status=status)
            payload = self.list_orders(material_status="SUFFICIENT", status=status)
            self.assertEqual([row["id"] for row in payload["results"]], [order.pk])
        payload = self.list_orders(material_status="SUFFICIENT", status="OPEN")
        self.assertEqual(payload["count"], 3)
        self.assertTrue(all(row["status"] == "OPEN" for row in payload["results"]))

    def test_legacy_excess_query_remains_supported_but_returns_unified_status(self):
        payload = self.list_orders(material_status="OVER")
        self.assertEqual(
            {row["id"] for row in payload["results"]},
            {self.orders[name][0].pk for name in ("excess", "zero_required_received")},
        )
        self.assertTrue(all(row["material_status"] == "SUFFICIENT" for row in payload["results"]))
