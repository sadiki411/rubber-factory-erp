from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APITestCase

from erp.permissions import READ_ONLY_GROUP_NAME
from inventory.models import InventoryBatch, InventoryContainer, InventoryOutbound, InventoryProduct
from orders.models import MaterialReceipt
from production.models import ProductionDailyLog, ProductionRun, ProductionRunOrder
from quality.models import (
    ProcessCard, ProcessCardUnitBinding, QualityOrder, QualityReworkCase,
    QualityReturnAllocation, QualityShipment, QualityShipmentBatch,
    QualityShipmentLine, QualityShipmentOrderAllocation,
)


class ReadOnlyOrderOverviewTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="overview-reader")
        self.client.force_authenticate(self.user)
        self.day = timezone.localdate()
        self.a = self.order("A", 100)
        self.b = self.order("B", 200)
        self.batch = QualityShipmentBatch.objects.create(
            shipment_no="TRACE-SH", shipment_date=self.day, created_by=self.user,
            status="CONFIRMED", order=self.a,
        )
        self.line = QualityShipmentLine.objects.create(
            batch=self.batch, order=self.a, piece_quantity=300,
            net_weight_kg=Decimal("0.300"), unit_weight_g_snapshot=Decimal("1"),
        )
        shares = []
        for sequence, (order, start, end) in enumerate(((self.a, 0, 100), (self.b, 100, 300)), 1):
            shares.append(QualityShipmentOrderAllocation.objects.create(
                shipment_line=self.line, order=order, sequence=sequence,
                piece_start=start, piece_end=end, piece_quantity=end-start,
                net_weight_kg=Decimal(end-start)/1000,
            ))
        self.case = QualityReworkCase.objects.create(
            origin="CUSTOMER_RETURN", shipment_batch=self.batch, shipment_line=self.line,
            shipment_unit_no=1, affected_quantity=60, affected_weight_kg=Decimal("0.060"),
            status="WAITING_REWORK", opened_on=self.day, created_by=self.user,
        )
        for share, quantity in zip(shares, (20, 40)):
            QualityReturnAllocation.objects.create(
                case=self.case, shipment_line=self.line, shipment_order_allocation=share,
                piece_quantity=quantity, net_weight_kg=Decimal(quantity)/1000,
            )

    def order(self, name, quantity):
        return QualityOrder.objects.create(
            order_no=f"TRACE-{name}", specification="240", material="N7200",
            order_quantity=quantity, due_date=self.day, created_by=self.user,
        )

    def overview(self, order=None, **filters):
        response = self.client.get(f"/api/orders/orders/{(order or self.b).pk}/overview/", filters)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def test_cross_order_physical_quantity_and_own_share_remain_separate(self):
        data = self.overview(section="shipments")
        self.assertEqual(data["summary"]["net_delivered"], 160)
        self.assertEqual(data["summary"]["delivery_remaining"], 40)
        self.assertEqual(data["results"][0]["physical_quantity"], 300)
        self.assertEqual(data["results"][0]["own_quantity"], 200)
        returned = self.overview(section="returns")["results"][0]
        self.assertEqual(returned["physical_quantity"], 60)
        self.assertEqual(returned["own_quantity"], 40)
        self.assertIn("实际作业0条", returned["details"])

    def test_inventory_is_only_explicit_outbound_reference_not_same_product_stock(self):
        product = InventoryProduct.objects.create(specification="240", material="N7200")
        batch = InventoryBatch.objects.create(batch_no="INV-TRACE", product=product)
        InventoryContainer.objects.create(container_code="CT-TRACE", batch=batch, quantity=50, container_type="BAG")
        InventoryOutbound.objects.create(outbound_no="UNLINKED-OUT", created_by=self.user)
        InventoryOutbound.objects.create(outbound_no="LINKED-OUT", order=self.b, created_by=self.user)
        data = self.overview(section="inventory")
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["results"][0]["document_no"], "LINKED-OUT")
        self.assertEqual(data["summary"]["net_delivered"], 160)
        self.assertIsNone(data["results"][0]["own_quantity"])

    def test_workbench_counts_are_global_to_search_not_current_page_or_kind(self):
        MaterialReceipt.objects.create(order_no="TRACE-OLD", item_no="1", weight_kg=Decimal("4.20"))
        result = self.client.get("/api/orders/workbench/", {"kind": "ORDER_SHORTAGE", "page_size": 1}).json()
        self.assertEqual(result["count"], 2)
        self.assertEqual(len(result["results"]), 1)
        self.assertEqual(result["summary"]["RETURN_PENDING"], 1)
        self.assertEqual(result["summary"]["RECEIPT_UNLINKED"], 1)
        self.assertIsNone(result["results"][0]["owner"])
        result = self.client.get("/api/orders/workbench/", {"q": "TRACE-B"}).json()
        self.assertEqual(result["summary"]["ORDER_SHORTAGE"], 1)
        self.assertEqual(result["count"], 1)

    def test_missing_fk_can_show_batch_binding_without_claiming_exact_line_card(self):
        card = ProcessCard.objects.create(card_no="TRACE-CARD", order=self.a, quantity=300, created_by=self.user)
        ProcessCardUnitBinding.objects.create(
            process_card=card, shipment_batch=self.batch, shipment_unit_no=1,
            piece_quantity=300, net_weight_kg=Decimal("0.300"), created_by=self.user,
        )
        row = self.overview(section="shipments")["results"][0]
        self.assertEqual(row["cards"], ["TRACE-CARD"])
        self.assertIn("未逐行猜配", row["details"])

    def test_reading_every_section_does_not_write_or_repair_history(self):
        from orders.overview import SECTIONS
        self.user.groups.add(Group.objects.get_or_create(name=READ_ONLY_GROUP_NAME)[0])
        with CaptureQueriesContext(connection) as queries:
            for section in SECTIONS:
                self.overview(section=section)
            self.assertEqual(self.client.get("/api/orders/workbench/").status_code, 200)
        self.assertFalse(any(q["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for q in queries))
        self.assertEqual(self.client.post(f"/api/orders/orders/{self.b.pk}/overview/", {}).status_code, 403)
        self.client.force_authenticate(None)
        self.assertIn(self.client.get("/api/orders/workbench/").status_code, (401, 403))

    def test_invalid_section_page_size_and_unknown_order_are_explicit_errors(self):
        endpoint = f"/api/orders/orders/{self.b.pk}/overview/"
        for filters in ({"section": "invented"}, {"page_size": 500}, {"page": 0}):
            self.assertEqual(self.client.get(endpoint, filters).status_code, 400)
        self.assertEqual(self.client.get(endpoint, {"page": 100}).status_code, 404)
        self.assertEqual(self.client.get("/api/orders/orders/999999/overview/").status_code, 404)

    def test_combined_run_does_not_give_all_production_to_later_order(self):
        run = ProductionRun.objects.create(order=self.a, order_no=self.a.order_no, specification="240", material="N7200", order_quantity=300, cavities=10, planned_mold_count=30, is_ledger_only=True, status="COMPLETED", created_by=self.user)
        for sequence, order in enumerate((self.a, self.b), 1):
            ProductionRunOrder.objects.create(run=run, order=order, sequence=sequence, planned_quantity=order.order_quantity)
        ProductionDailyLog.objects.create(run=run, produced_mold_count=15, cavities_snapshot=10)
        data = self.overview(section="production")
        self.assertEqual(data["summary"]["production_progress_quantity"], 50)
        self.assertEqual(data["results"][0]["physical_quantity"], 150)
        self.assertEqual(data["results"][0]["own_quantity"], 50)

    def test_completed_return_waiting_for_reshipment_is_not_lost_from_workbench(self):
        self.case.status = "COMPLETED"
        self.case.save()
        result = self.client.get("/api/orders/workbench/", {"kind": "RETURN_PENDING"}).json()
        self.assertEqual(result["count"], 1)
        self.assertIn("尚未登记再发", result["results"][0]["reason"])

    def test_queries_do_not_grow_per_workbench_row(self):
        with CaptureQueriesContext(connection) as before:
            self.client.get("/api/orders/workbench/")
        for index in range(25):
            self.order(f"BULK-{index}", 100)
        with CaptureQueriesContext(connection) as after:
            response = self.client.get("/api/orders/workbench/", {"page_size": 1})
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(after), len(before) + 1)
        self.assertLess(len(after), 20)

    def test_due_scope_and_exact_source_urls_do_not_change_business_state(self):
        from datetime import timedelta
        self.order("OLD", 10).due_date = self.day - timedelta(days=1)
        result = self.client.get("/api/orders/workbench/", {"due_scope": "today"})
        self.assertEqual(result.status_code, 200, result.content)
        self.assertTrue(all(row["due_date"] == self.day.isoformat() for row in result.json()["results"]))
        returns = self.client.get("/api/orders/workbench/", {"kind": "RETURN_PENDING"}).json()
        self.assertIn(f"rework_case_id={self.case.pk}", returns["results"][0]["url"])
        receipt = MaterialReceipt.objects.create(order_no="UNLINKED", weight_kg=1)
        receipts = self.client.get("/api/orders/workbench/", {"kind": "RECEIPT_UNLINKED"}).json()
        self.assertIn(f"receipt_id={receipt.pk}", receipts["results"][0]["url"])
