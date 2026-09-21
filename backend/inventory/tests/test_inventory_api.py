from decimal import Decimal

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APITestCase

from quality.models import QualityEmployee, QualityOrder

from inventory.models import InventoryBatch, InventoryContainer, InventoryLocation, InventoryTransaction, MaterialRemainder


class InventoryApiTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="inventory-user", password="password")
        self.client.force_authenticate(self.user)
        self.client.post("/api/inventory/locations/bootstrap/", {}, format="json")
        self.large = InventoryLocation.objects.get(code="K01-L01-P01")
        self.small = InventoryLocation.objects.get(code="K07-L01-P01")
        self.inspector = QualityEmployee.objects.create(
            employee_no="QC-001", name="品检员甲", role=QualityEmployee.Role.INSPECTOR
        )

    def receipt(self, **overrides):
        payload = {
            "product": {
                "product_code": "P-100",
                "product_name": "产品100",
                "specification": "100A",
                "material": "NBR",
                "unit_weight_g": "2.5",
            },
            "container_type": "BASKET",
            "location_id": self.large.pk,
            "quantity": 4000,
            "bag_count": 20,
            "pieces_per_bag": 200,
            "quality_status": "PASSED",
            "inspector_id": self.inspector.pk,
            "batch_no": "OPEN-001",
        }
        payload.update(overrides)
        return self.client.post("/api/inventory/receipts/", payload, format="json")

    def test_bootstrap_creates_fixed_large_and_small_positions(self):
        self.assertEqual(InventoryLocation.objects.filter(rack_type="LARGE").count(), 120)
        self.assertEqual(InventoryLocation.objects.filter(rack_type="SMALL").count(), 30)
        self.assertFalse(self.small.allows_basket)
        self.assertTrue(self.large.allows_basket)
        self.assertTrue(self.large.allows_bag)

    def test_receipt_is_independent_of_order_and_process_card(self):
        response = self.receipt()
        self.assertEqual(response.status_code, 201, response.content)
        container = InventoryContainer.objects.get(container_code=response.json()["container_code"])
        self.assertEqual(container.quantity, 4000)
        self.assertEqual(container.location.code, "K01-L01-P01")
        self.assertEqual(container.batch.quality_status, InventoryBatch.QualityStatus.PASSED)
        self.assertEqual(InventoryTransaction.objects.filter(transaction_type="RECEIPT").count(), 1)

    def test_small_rack_rejects_basket(self):
        response = self.receipt(batch_no="OPEN-002", location_id=self.small.pk)
        self.assertEqual(response.status_code, 400)
        self.assertIn("不允许放筐", str(response.data))

    def test_waiting_inspection_cannot_be_outbound(self):
        response = self.receipt(
            batch_no="WAIT-001",
            location_id=self.large.pk,
            quality_status="WAITING",
            inspector_id=None,
        )
        self.assertEqual(response.status_code, 201, response.content)
        container_id = response.json()["id"]
        outbound = self.client.post(
            "/api/inventory/outbounds/",
            {"lines": [{"container_id": container_id, "quantity": 1}]},
            format="json",
        )
        self.assertEqual(outbound.status_code, 400)
        self.assertIn("已检库存", str(outbound.data))

    def test_outbound_reduces_quantity_and_does_not_create_quality_shipment(self):
        response = self.receipt()
        container_id = response.json()["id"]
        outbound = self.client.post(
            "/api/inventory/outbounds/",
            {
                "outbound_no": "OUT-001",
                "shipment_ref": "TRUCK-001",
                "lines": [{"container_id": container_id, "quantity": 1000}],
            },
            format="json",
        )
        self.assertEqual(outbound.status_code, 201, outbound.content)
        container = InventoryContainer.objects.get(pk=container_id)
        self.assertEqual(container.quantity, 3000)
        self.assertEqual(container.location.code, "K01-L01-P01")
        self.assertEqual(InventoryTransaction.objects.filter(transaction_type="OUTBOUND").count(), 1)

    def test_material_remainder_uses_are_audited_and_cannot_overdraw(self):
        created = self.client.post(
            "/api/inventory/material-remainders/",
            {"material": "PP", "weight_kg": "3.500", "fridge_code": "F01-冰箱"},
            format="json",
        )
        self.assertEqual(created.status_code, 201, created.content)
        remainder_id = created.json()["id"]
        used = self.client.post(
            f"/api/inventory/material-remainders/{remainder_id}/use/",
            {"weight_kg": "1.250", "note": "试产使用"},
            format="json",
        )
        self.assertEqual(used.status_code, 201, used.content)
        remainder = MaterialRemainder.objects.get(pk=remainder_id)
        self.assertEqual(str(remainder.weight_kg), "3.500")
        self.assertEqual(str(remainder.uses.get().weight_kg), "1.250")
        overdraw = self.client.post(
            f"/api/inventory/material-remainders/{remainder_id}/use/",
            {"weight_kg": "3.000"},
            format="json",
        )
        self.assertEqual(overdraw.status_code, 400)
        self.assertEqual(self.client.patch(f"/api/inventory/material-remainders/{remainder_id}/", {"weight_kg": "9"}, format="json").status_code, 405)
        self.assertEqual(self.client.delete(f"/api/inventory/material-remainders/{remainder_id}/").status_code, 405)
