from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.test import APITestCase

from molds.models import MoldModel
from orders.models import (
    BusinessRecordRevision,
    MaterialReceipt,
    OrderStatusChange,
    ProductSpecification,
)
from production.models import ProductionDailyLog, ProductionRun, ProductionStation
from quality.models import (
    QualityEmployee,
    QualityOrder,
    QualityShipment,
    QualityShipmentBatch,
    QualityShipmentLine,
    QualityShipmentOrderAllocation,
)


class BusinessApiTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="business-api", password="test")
        self.client.force_authenticate(self.user)

    def test_product_specification_crud_is_audited_and_cannot_be_deleted(self):
        mold_model = MoldModel.objects.create(
            code="API-MOLD-MODEL-001",
            product_name="API模具产品",
        )
        created = self.client.post(
            "/api/orders/product-specifications/",
            {
                "product_name": "密封圈",
                "customer_product_no": "TEST-PRODUCT-001",
                "specification": "TEST-SPEC-A",
                "material": "SYN-RUBBER-A",
                "strip_count": "9/4",
                "mold_model_id": mold_model.pk,
                "standard_hours": "SHOULD-NOT-BE-WRITABLE",
                "is_active": True,
            },
            format="json",
        )
        self.assertEqual(created.status_code, 201, created.content)
        self.assertEqual(created.json()["mold_model_id"], mold_model.pk)
        self.assertEqual(created.json()["mold_model"]["code"], mold_model.code)
        self.assertNotIn("standard_hours", created.json())
        product_id = created.json()["id"]
        product = ProductSpecification.objects.get(pk=product_id)
        self.assertEqual(product.mold_model_id, mold_model.pk)
        self.assertEqual(product.standard_hours, "")
        self.assertTrue(product.normalized_key.endswith("|api-mold-model-001"))
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

    def test_product_specification_keeps_current_inactive_mold_but_rejects_new_inactive_link(self):
        current = MoldModel.objects.create(
            code="INACTIVE-CURRENT-MOLD",
            product_name="历史停用模具",
            is_active=False,
        )
        other = MoldModel.objects.create(
            code="INACTIVE-OTHER-MOLD",
            product_name="其他停用模具",
            is_active=False,
        )
        product = ProductSpecification.objects.create(
            product_name="历史产品",
            specification="INACTIVE-LINK-SPEC",
            mold_model=current,
        )

        unchanged = self.client.patch(
            f"/api/orders/product-specifications/{product.pk}/",
            {"notes": "只修改备注", "mold_model_id": current.pk},
            format="json",
        )
        self.assertEqual(unchanged.status_code, 200, unchanged.content)
        self.assertEqual(unchanged.json()["mold_model_id"], current.pk)

        rejected = self.client.patch(
            f"/api/orders/product-specifications/{product.pk}/",
            {"mold_model_id": other.pk},
            format="json",
        )
        self.assertEqual(rejected.status_code, 400, rejected.content)
        self.assertIn("mold_model_id", rejected.json())

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

    def test_process_card_text_is_used_only_without_structured_counts(self):
        cases = (
            ("CARD-TEXT-YES", "已收到", None, None, "RECEIVED"),
            ("CARD-TEXT-NO", "未收到", None, None, "NOT_RECEIVED"),
            ("CARD-TEXT-COUNT", "人工确认：2张", None, None, "RECEIVED"),
            ("CARD-STRUCTURED", "有", 0, None, "NOT_RECEIVED"),
        )
        for order_no, text, count, covered, expected in cases:
            order = QualityOrder.objects.create(
                order_no=order_no,
                specification="TEST-CARD-SPEC",
                order_quantity=100,
                process_card_text=text,
                process_card_count=count,
                process_card_covered_quantity=covered,
                created_by=self.user,
            )
            response = self.client.get(f"/api/orders/orders/{order.pk}/")
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual(response.json()["process_card_status"], expected)

    def test_manual_order_status_change_requires_reason_and_keeps_history(self):
        order = QualityOrder.objects.create(
            order_no="STATUS-REASON-001",
            specification="TEST-STATUS-SPEC",
            order_quantity=100,
            created_by=self.user,
        )
        rejected = self.client.patch(
            f"/api/orders/orders/{order.pk}/",
            {"status": QualityOrder.Status.COMPLETED},
            format="json",
        )
        self.assertEqual(rejected.status_code, 400, rejected.content)
        self.assertIn("status_change_reason", rejected.json())
        order.refresh_from_db()
        self.assertEqual(order.status, QualityOrder.Status.OPEN)

        accepted = self.client.patch(
            f"/api/orders/orders/{order.pk}/",
            {
                "status": QualityOrder.Status.COMPLETED,
                "status_change_reason": "历史订单已人工核对完成",
            },
            format="json",
        )
        self.assertEqual(accepted.status_code, 200, accepted.content)
        change = OrderStatusChange.objects.get(order=order)
        self.assertEqual(change.from_status, QualityOrder.Status.OPEN)
        self.assertEqual(change.to_status, QualityOrder.Status.COMPLETED)
        self.assertEqual(change.reason, "历史订单已人工核对完成")
        self.assertEqual(change.operator, self.user)

        history = self.client.get(
            f"/api/orders/orders/{order.pk}/status-history/"
        )
        self.assertEqual(history.status_code, 200, history.content)
        self.assertEqual(history.json()[0]["reason"], "历史订单已人工核对完成")

    def test_order_last_data_updated_at_includes_receipt_production_and_shipment(self):
        order = QualityOrder.objects.create(
            order_no="ACTIVITY-100",
            specification="TEST-ACTIVITY-SPEC",
            order_quantity=100,
            created_by=self.user,
        )
        receipt = MaterialReceipt.objects.create(
            order=order,
            order_no=order.order_no,
            weight_kg=Decimal("1.000"),
        )
        station = ProductionStation.objects.create(code="ACT-01", group="A", position_no=1)
        run = ProductionRun.objects.create(
            station=station,
            order=order,
            order_no=order.order_no,
            specification=order.specification,
            order_quantity=order.order_quantity,
            planned_mold_count=1,
            created_by=self.user,
        )
        inspector = QualityEmployee.objects.create(
            employee_no="ACT-QA-01",
            name="活动测试品检",
            role=QualityEmployee.Role.INSPECTOR,
        )
        shipment = QualityShipment.objects.create(
            shipment_no="ACT-SHIP-01",
            shipment_date=timezone.localdate(),
            order=order,
            inspector=inspector,
            inspection_quantity=1,
            qualified_quantity=1,
            defective_quantity=0,
            shipped_quantity=1,
            created_by=self.user,
        )
        base = timezone.now()
        MaterialReceipt.objects.filter(pk=receipt.pk).update(updated_at=base + timedelta(minutes=1))
        ProductionRun.objects.filter(pk=run.pk).update(updated_at=base + timedelta(minutes=2))
        newest = base + timedelta(minutes=3)
        QualityShipment.objects.filter(pk=shipment.pk).update(updated_at=newest)

        response = self.client.get(f"/api/orders/orders/{order.pk}/")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(parse_datetime(response.json()["last_data_updated_at"]), newest)

    def test_order_last_data_updated_at_includes_weighted_allocation_target(self):
        source = QualityOrder.objects.create(
            order_no="ACTIVITY-WEIGHTED-SOURCE",
            specification="TEST-ACTIVITY-WEIGHTED",
            material="NBR",
            order_quantity=100,
            created_by=self.user,
        )
        target = QualityOrder.objects.create(
            order_no="ACTIVITY-WEIGHTED-TARGET",
            specification=source.specification,
            material=source.material,
            order_quantity=200,
            created_by=self.user,
        )
        batch = QualityShipmentBatch.objects.create(
            shipment_no="ACTIVITY-WEIGHTED-SHIPMENT",
            shipment_date=timezone.localdate(),
            order=source,
            unit_weight_g=Decimal("1"),
            status=QualityShipmentBatch.Status.CONFIRMED,
            created_by=self.user,
        )
        line = QualityShipmentLine.objects.create(
            batch=batch,
            order=source,
            net_weight_kg=Decimal("0.200"),
            piece_quantity=200,
            unit_weight_g_snapshot=Decimal("1"),
        )
        QualityShipmentOrderAllocation.objects.create(
            shipment_line=line,
            order=target,
            sequence=1,
            piece_start=0,
            piece_end=200,
            piece_quantity=200,
            net_weight_kg=Decimal("0.200"),
        )
        newest = timezone.now() + timedelta(minutes=3)
        QualityShipmentBatch.objects.filter(pk=batch.pk).update(updated_at=newest)

        response = self.client.get(f"/api/orders/orders/{target.pk}/")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(parse_datetime(response.json()["last_data_updated_at"]), newest)

    def test_order_production_progress_sums_multiple_valid_tasks_and_cavity_snapshots(self):
        order = QualityOrder.objects.create(
            order_no="PROGRESS-MULTI-001",
            item_no="10",
            specification="TEST-PROGRESS-SPEC",
            material="TEST-PROGRESS-MATERIAL",
            order_quantity=120,
            created_by=self.user,
        )
        first = ProductionRun.objects.create(
            order=order,
            order_no=order.order_no,
            specification=order.specification,
            material=order.material,
            order_quantity=order.order_quantity,
            cavities=6,
            planned_mold_count=20,
            is_ledger_only=True,
            created_by=self.user,
        )
        second = ProductionRun.objects.create(
            order=order,
            order_no=order.order_no,
            specification=order.specification,
            material=order.material,
            order_quantity=order.order_quantity,
            cavities=8,
            planned_mold_count=10,
            is_ledger_only=True,
            created_by=self.user,
        )
        ProductionDailyLog.objects.create(
            run=first,
            operator="甲",
            sequence_no=1,
            cumulative_mold_count=10,
            produced_mold_count=10,
            cavities_snapshot=6,
        )
        ProductionDailyLog.objects.create(
            run=second,
            operator="乙",
            sequence_no=1,
            cumulative_mold_count=5,
            produced_mold_count=5,
            cavities_snapshot=8,
        )

        response = self.client.get(f"/api/orders/orders/{order.pk}/")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["produced_quantity"], 100)
        self.assertEqual(response.json()["production_remaining_quantity"], 20)
        self.assertFalse(response.json()["production_target_reached"])
        self.assertEqual(response.json()["production_run_count"], 2)

    def test_order_number_and_item_must_be_unique_and_delete_is_disabled(self):
        payload = {
            "order_no": "DUP-100",
            "item_no": "1",
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
        self.assertEqual(first.status_code, 201, first.content)
        different_item = self.client.post(
            "/api/orders/orders/", {**payload, "item_no": "2"}, format="json"
        )
        duplicate = self.client.post("/api/orders/orders/", payload, format="json")
        self.assertEqual(different_item.status_code, 201, different_item.content)
        self.assertEqual(duplicate.status_code, 400, duplicate.content)
        self.assertIn("item_no", duplicate.json())
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
                "issued_on": "2026-08-05",
                "manufactured_on": "2026-08-04",
            },
            format="json",
        )
        self.assertEqual(created.status_code, 201, created.content)
        self.assertEqual(created.json()["order_no"], "MAT-100")
        self.assertEqual(created.json()["issued_on"], "2026-08-05")
        self.assertEqual(created.json()["manufactured_on"], "2026-08-04")
        receipt = MaterialReceipt.objects.get(pk=created.json()["id"])
        self.assertEqual(receipt.order_id, order.pk)
        self.assertEqual(receipt.issued_on.isoformat(), "2026-08-05")
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
