from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.utils import timezone
from rest_framework.test import APITestCase

from orders.models import BusinessRecordRevision, MaterialReceipt, ProductSpecification
from quality.models import QualityOrder


class BusinessApiTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="business-api", password="test")
        self.client.force_authenticate(self.user)

    def test_product_specification_crud_is_audited_and_cannot_be_deleted(self):
        created = self.client.post(
            "/api/orders/product-specifications/",
            {
                "product_name": "密封圈",
                "customer_product_no": "TEST-PRODUCT-001",
                "specification": "TEST-SPEC-A",
                "material": "SYN-RUBBER-A",
                "strip_count": "9/4",
                "is_active": True,
            },
            format="json",
        )
        self.assertEqual(created.status_code, 201, created.content)
        product_id = created.json()["id"]
        updated = self.client.patch(
            f"/api/orders/product-specifications/{product_id}/",
            {"is_active": False},
            format="json",
        )
        self.assertEqual(updated.status_code, 200, updated.content)
        self.assertEqual(
            list(
                BusinessRecordRevision.objects.filter(record_id=product_id).values_list(
                    "action", flat=True
                )
            ),
            [BusinessRecordRevision.Action.DEACTIVATE, BusinessRecordRevision.Action.CREATE],
        )
        revision = BusinessRecordRevision.objects.filter(record_id=product_id).first()
        revision.action = BusinessRecordRevision.Action.UPDATE
        with self.assertRaises(ValidationError):
            revision.save()
        with self.assertRaises(ValidationError):
            revision.delete()
        deleted = self.client.delete(f"/api/orders/product-specifications/{product_id}/")
        self.assertEqual(deleted.status_code, 405)

    def test_order_material_and_process_card_status_use_imported_plus_manual_weight(self):
        order = QualityOrder.objects.create(
            order_no="ORD-100",
            item_no="1",
            product_name="",
            specification="TEST-SPEC-A",
            material="",
            order_quantity=1000,
            order_date=None,
            required_material_kg=Decimal("100.000"),
            manual_received_material_kg=Decimal("10.000"),
            process_card_count=1,
            process_card_covered_quantity=500,
            created_by=self.user,
        )
        MaterialReceipt.objects.create(
            order=order,
            order_no=order.order_no,
            item_no=order.item_no,
            weight_kg=Decimal("40.500"),
            manufactured_on=timezone.localdate(),
        )
        response = self.client.get(f"/api/orders/orders/{order.pk}/")
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertEqual(payload["imported_received_material_kg"], "40.500")
        self.assertEqual(payload["received_material_kg"], "50.500")
        self.assertEqual(payload["material_gap_kg"], "49.500")
        self.assertEqual(payload["material_status"], "PARTIAL")
        self.assertEqual(payload["process_card_status"], "PARTIAL")

        updated = self.client.patch(
            f"/api/orders/orders/{order.pk}/",
            {
                "manual_received_material_kg": "70.000",
                "process_card_covered_quantity": 1000,
            },
            format="json",
        )
        self.assertEqual(updated.status_code, 200, updated.content)
        self.assertEqual(updated.json()["received_material_kg"], "110.500")
        self.assertEqual(updated.json()["material_status"], "OVER")
        self.assertEqual(updated.json()["process_card_status"], "RECEIVED")

    def test_identical_order_lines_are_allowed_and_delete_is_disabled(self):
        payload = {
            "order_no": "DUP-100",
            "batch_no": "",
            "product_code": "",
            "product_name": "",
            "specification": "TEST-SPEC-DUP",
            "material": "SYN-RUBBER-B",
            "order_quantity": 100,
            "order_date": None,
            "status": "OPEN",
        }
        first = self.client.post("/api/orders/orders/", payload, format="json")
        second = self.client.post("/api/orders/orders/", payload, format="json")
        self.assertEqual(first.status_code, 201, first.content)
        self.assertEqual(second.status_code, 201, second.content)
        self.assertEqual(QualityOrder.objects.filter(order_no="DUP-100").count(), 2)
        self.assertEqual(
            self.client.delete(f"/api/orders/orders/{first.json()['id']}/").status_code,
            405,
        )

    def test_material_receipt_can_link_order_and_is_audited(self):
        order = QualityOrder.objects.create(
            order_no="MAT-100",
            specification="TEST-SPEC-RECEIPT",
            order_quantity=500,
            created_by=self.user,
        )
        created = self.client.post(
            "/api/orders/material-receipts/",
            {
                "order_id": order.pk,
                "item_no": "2",
                "finished_product_name": "TEST-FINISHED-PRODUCT",
                "specification": "TEST-SPEC-RECEIPT",
                "material": "SYN-RUBBER-C",
                "batch_no": "TEST-BATCH-01",
                "sheet_size": "TEST-SHEET-SIZE",
                "weight_kg": "6.250",
                "manufactured_on": "2026-08-04",
            },
            format="json",
        )
        self.assertEqual(created.status_code, 201, created.content)
        self.assertEqual(created.json()["order_no"], "MAT-100")
        receipt = MaterialReceipt.objects.get(pk=created.json()["id"])
        self.assertEqual(receipt.order_id, order.pk)
        self.assertTrue(
            BusinessRecordRevision.objects.filter(
                record_type=BusinessRecordRevision.RecordType.MATERIAL_RECEIPT,
                record_id=receipt.pk,
            ).exists()
        )

    def test_material_receipt_rejects_a_different_item_number_for_linked_order(self):
        order = QualityOrder.objects.create(
            order_no="MAT-ITEM-100",
            item_no="10",
            specification="TEST-SPEC",
            order_quantity=500,
            created_by=self.user,
        )
        response = self.client.post(
            "/api/orders/material-receipts/",
            {
                "order_id": order.pk,
                "order_no": order.order_no,
                "item_no": "20",
                "weight_kg": "1.000",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("order_id", response.json())

    def test_material_receipt_linked_filter_and_online_link_update_are_audited(self):
        order = QualityOrder.objects.create(
            order_no="MAT-LINK",
            item_no="1",
            specification="TEST-SPEC-LINK",
            order_quantity=300,
            created_by=self.user,
        )
        linked = MaterialReceipt.objects.create(
            order=order,
            order_no=order.order_no,
            item_no="1",
            weight_kg=Decimal("3.000"),
        )
        unlinked = MaterialReceipt.objects.create(
            order_no=order.order_no,
            item_no="1",
            weight_kg=Decimal("4.000"),
        )
        linked_response = self.client.get("/api/orders/material-receipts/?linked=true")
        unlinked_response = self.client.get("/api/orders/material-receipts/?linked=false")
        self.assertEqual(linked_response.status_code, 200, linked_response.content)
        self.assertEqual(unlinked_response.status_code, 200, unlinked_response.content)
        self.assertEqual([item["id"] for item in linked_response.json()["results"]], [linked.pk])
        self.assertEqual([item["id"] for item in unlinked_response.json()["results"]], [unlinked.pk])

        updated = self.client.patch(
            f"/api/orders/material-receipts/{unlinked.pk}/",
            {"order_id": order.pk},
            format="json",
        )
        self.assertEqual(updated.status_code, 200, updated.content)
        self.assertEqual(updated.json()["order"]["id"], order.pk)
        self.assertTrue(
            BusinessRecordRevision.objects.filter(
                record_type=BusinessRecordRevision.RecordType.MATERIAL_RECEIPT,
                record_id=unlinked.pk,
                action=BusinessRecordRevision.Action.UPDATE,
            ).exists()
        )
        self.assertEqual(
            self.client.get("/api/orders/material-receipts/?linked=maybe").status_code,
            400,
        )
