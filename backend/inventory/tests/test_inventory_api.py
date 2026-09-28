from decimal import Decimal

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APITestCase

from orders.models import ProductSpecification
from quality.models import ProductUnitWeight, QualityEmployee, QualityOrder

from inventory.models import InventoryBatch, InventoryContainer, InventoryLocation, InventoryProduct, InventoryTransaction, MaterialRemainder


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

    def test_location_selector_returns_every_active_fixed_position(self):
        response = self.client.get("/api/inventory/locations/?active=true")

        self.assertEqual(response.status_code, 200, response.content)
        self.assertIsInstance(response.json(), list)
        self.assertEqual(len(response.json()), 151)
        self.assertIn("K09-L05-P02", {item["code"] for item in response.json()})

    def test_inventory_list_page_size_can_be_requested_for_selectors(self):
        for index in range(35):
            MaterialRemainder.objects.create(
                material=f"测试胶料-{index:02d}",
                weight_kg=Decimal("1.000"),
                created_by=self.user,
            )

        default_response = self.client.get("/api/inventory/material-remainders/")
        complete_response = self.client.get("/api/inventory/material-remainders/?page_size=1000")

        self.assertEqual(len(default_response.json()["results"]), 30)
        self.assertEqual(len(complete_response.json()["results"]), 35)

    def test_receipt_is_independent_of_order_and_process_card(self):
        response = self.receipt()
        self.assertEqual(response.status_code, 201, response.content)
        container = InventoryContainer.objects.get(container_code=response.json()["container_code"])
        self.assertEqual(container.quantity, 4000)
        self.assertEqual(container.location.code, "K01-L01-P01")
        self.assertEqual(container.batch.quality_status, InventoryBatch.QualityStatus.PASSED)
        self.assertEqual(InventoryTransaction.objects.filter(transaction_type="RECEIPT").count(), 1)

    def test_receipt_reuses_quality_unit_weight_and_saves_an_inventory_override_to_the_same_history(self):
        specification = ProductSpecification.objects.create(
            product_name="产品362", customer_product_no="P-362",
            specification="362", material="NBR",
        )
        product = InventoryProduct.objects.create(
            product_code="P-362", product_name="产品362", specification="362",
            material="NBR", unit_weight_g="1.80000", product_specification=specification,
        )
        ProductUnitWeight.objects.create(
            product_specification=specification, unit_weight_g="2.10000",
            created_by=self.user,
        )

        listed = self.client.get("/api/inventory/products/?page_size=1000").json()["results"]
        listed_product = next(item for item in listed if item["id"] == product.pk)
        self.assertEqual(Decimal(listed_product["effective_unit_weight_g"]), Decimal("2.10000"))

        response = self.receipt(
            product_id=product.pk,
            unit_weight_g="2.25000",
            batch_no="WEIGHT-001",
        )
        self.assertEqual(response.status_code, 201, response.content)
        product.refresh_from_db()
        self.assertEqual(product.unit_weight_g, Decimal("2.25000"))
        latest = ProductUnitWeight.objects.filter(product_specification=specification).order_by("-created_at", "-id").first()
        self.assertEqual(latest.unit_weight_g, Decimal("2.25000"))
        self.assertEqual(latest.notes, "由库存入库确认自动保存的产品单重")

        location = self.client.get("/api/inventory/locations/?active=true").json()
        occupied = next(item for item in location if item["id"] == self.large.pk)
        self.assertEqual(Decimal(occupied["container"]["unit_weight_g"]), Decimal("2.25000"))
        self.assertTrue(occupied["container"]["received_on"])

    def test_manual_inventory_product_links_to_a_unique_matching_product_specification(self):
        specification = ProductSpecification.objects.create(
            product_name="唯一产品", specification="14x2.5", material="EPDM",
        )
        response = self.receipt(
            batch_no="MATCH-001",
            product={
                "product_code": "",
                "product_name": "唯一产品",
                "specification": "14x2.5",
                "material": "EPDM",
                "unit_weight_g": "0.50000",
            },
        )

        self.assertEqual(response.status_code, 201, response.content)
        product = InventoryContainer.objects.get(pk=response.json()["id"]).batch.product
        self.assertEqual(product.product_specification_id, specification.pk)
        self.assertTrue(ProductUnitWeight.objects.filter(
            product_specification=specification,
            unit_weight_g=Decimal("0.50000"),
        ).exists())

    def test_manual_inventory_product_does_not_guess_when_specification_and_material_are_ambiguous(self):
        first = ProductSpecification.objects.create(
            product_name="同规格产品甲", customer_product_no="P-MATCH",
            specification="14x2.5", material="EPDM",
        )
        ProductSpecification.objects.create(
            product_name="同规格产品乙", customer_product_no="P-OTHER",
            specification="14x2.5", material="EPDM",
        )
        response = self.receipt(
            batch_no="AMBIGUOUS-001",
            location_id=InventoryLocation.objects.get(code="K01-L01-P02").pk,
            product={
                "product_code": "P-MATCH",
                "product_name": "同规格库存产品",
                "specification": "14x2.5",
                "material": "EPDM",
                "unit_weight_g": "0.50000",
            },
        )

        self.assertEqual(response.status_code, 201, response.content)
        product = InventoryContainer.objects.get(pk=response.json()["id"]).batch.product
        self.assertIsNone(product.product_specification_id)
        self.assertFalse(ProductUnitWeight.objects.filter(product_specification=first).exists())

    def test_received_inventory_product_details_can_be_corrected_and_weight_is_remembered(self):
        specification = ProductSpecification.objects.create(
            product_name="产品100-正确名称", specification="100A", material="NBR",
        )
        created = self.receipt(batch_no="CORRECT-001")
        container_id = created.json()["id"]

        corrected = self.client.post(
            f"/api/inventory/containers/{container_id}/correct-product/",
            {
                "product_code": "P-100-CORRECT",
                "product_name": "产品100-正确名称",
                "specification": "100A",
                "material": "NBR",
                "unit_weight_g": "2.75000",
                "reason": "入库时编号和单重填错",
            },
            format="json",
        )

        self.assertEqual(corrected.status_code, 200, corrected.content)
        container = InventoryContainer.objects.select_related("batch__product").get(pk=container_id)
        self.assertEqual(container.batch.product.product_code, "P-100-CORRECT")
        self.assertEqual(container.batch.product.unit_weight_g, Decimal("2.75000"))
        self.assertEqual(container.batch.product.product_specification_id, specification.pk)
        self.assertTrue(ProductUnitWeight.objects.filter(
            product_specification=specification,
            unit_weight_g=Decimal("2.75000"),
        ).exists())
        correction = container.transactions.filter(transaction_type="ADJUST").get()
        self.assertIn("入库时编号和单重填错", correction.reason)

    def test_received_inventory_can_be_reassigned_to_an_existing_product(self):
        created = self.receipt(batch_no="REASSIGN-001")
        container_id = created.json()["id"]
        replacement = InventoryProduct.objects.create(
            product_code="P-200", product_name="产品200",
            specification="200A", material="EPDM", unit_weight_g="3.10000",
        )

        corrected = self.client.post(
            f"/api/inventory/containers/{container_id}/correct-product/",
            {"replacement_product_id": replacement.pk, "reason": "入库时选错产品"},
            format="json",
        )

        self.assertEqual(corrected.status_code, 200, corrected.content)
        container = InventoryContainer.objects.select_related("batch__product").get(pk=container_id)
        self.assertEqual(container.batch.product_id, replacement.pk)
        self.assertEqual(corrected.json()["batch"]["product"]["product_code"], "P-200")

    def test_product_correction_rejects_an_empty_identity(self):
        created = self.receipt(batch_no="CORRECT-EMPTY-001")
        container_id = created.json()["id"]
        corrected = self.client.post(
            f"/api/inventory/containers/{container_id}/correct-product/",
            {"product_code": "", "product_name": "", "specification": "", "material": ""},
            format="json",
        )

        self.assertEqual(corrected.status_code, 400, corrected.content)
        container = InventoryContainer.objects.select_related("batch__product").get(pk=container_id)
        self.assertEqual(container.batch.product.product_code, "P-100")

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
