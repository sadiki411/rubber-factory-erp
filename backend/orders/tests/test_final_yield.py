from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APITestCase

from inventory.models import InventoryBatch, InventoryTransaction
from orders.models import OrderYieldRemainder
from orders.yields import order_yields
from production.models import ProductionDailyLog, ProductionRun, ProductionRunOrder, ProductionYieldRecord
from quality.models import (
    ProcessCard, QualityOrder, QualityReworkCase, QualityShipment,
    QualityShipmentBatch, QualityShipmentLine, ReturnRework,
)


class OrderFinalYieldTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="order-yield")
        self.client.force_authenticate(self.user)
        self.serial = 0

    def order(self, **overrides):
        self.serial += 1
        return QualityOrder.objects.create(**{
            "order_no": f"YIELD-{self.serial}", "specification": "240", "material": "N7200",
            "order_quantity": 900, "status": "COMPLETED", "created_by": self.user, **overrides,
        })

    def create_run(self, orders, molds=100, *, links=True, **overrides):
        order = orders[0]
        run = ProductionRun.objects.create(**{
            "order": order, "order_no": order.order_no, "specification": order.specification,
            "material": order.material, "order_quantity": sum(o.order_quantity for o in orders),
            "cavities": 10, "planned_mold_count": 100, "is_ledger_only": True,
            "status": "COMPLETED", "created_by": self.user, **overrides,
        })
        if links:
            for i, target in enumerate(orders):
                ProductionRunOrder.objects.create(run=run, order=target, planned_quantity=target.order_quantity, sequence=i + 1)
        if molds:
            ProductionDailyLog.objects.create(run=run, operator="测试", produced_mold_count=molds, cavities_snapshot=run.cavities)
        return run

    def ship(self, order, quantity=900):
        self.serial += 1
        return QualityShipment.objects.create(
            shipment_no=f"SHIP-{self.serial}", shipment_date=timezone.localdate(), order=order,
            inspection_quantity=quantity, qualified_quantity=quantity,
            shipped_quantity=quantity, created_by=self.user,
        )

    def get_yield(self, order):
        response = self.client.get(f"/api/orders/orders/{order.pk}/final-yield/")
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def save_yield(self, order, remaining, *, token=None):
        return self.client.post(f"/api/orders/orders/{order.pk}/final-yield/", {
            "remaining_quantity": remaining,
            "source_token": token or self.get_yield(order)["source_token"],
            "notes": "现场清点",
        }, format="json")

    def test_missing_production_does_not_guess_from_order_or_shipments(self):
        order = self.order()
        self.ship(order)
        data = self.get_yield(order)
        self.assertEqual(data["status"], "NO_PRODUCTION")
        self.assertIsNone(data["yield_percent"])
        self.assertEqual(data["production_quantity"], 0)
        self.assertEqual(self.save_yield(order, 0).status_code, 400)
        self.assertEqual(OrderYieldRemainder.objects.count(), 0)

    def test_actual_quantity_uses_cavity_snapshots_without_subtracting_defects(self):
        order = self.order()
        run = self.create_run([order])
        run.daily_logs.update(defective_quantity=70)
        ProductionRun.objects.filter(pk=run.pk).update(cavities=20)
        self.ship(order)
        data = self.get_yield(order)
        self.assertEqual(data["production_quantity"], 1000)
        self.assertEqual(data["yield_percent"], "90.00")
        self.assertFalse(data["remaining_recorded"])
        self.assertEqual(data["remaining_quantity"], 0)
        self.assertEqual(OrderYieldRemainder.objects.count(), 0)

    def test_multiple_runs_do_not_duplicate_the_order_delivery(self):
        order = self.order()
        self.create_run([order], 40)
        self.create_run([order], 60)
        self.ship(order)
        data = self.get_yield(order)
        self.assertEqual(data["production_quantity"], 1000)
        self.assertEqual(data["effective_shipped_quantity"], 900)
        self.assertEqual(data["yield_percent"], "90.00")

    def test_combined_group_shares_one_remainder_across_orders(self):
        first, second = self.order(order_quantity=400), self.order(order_quantity=500)
        self.create_run([first, second])
        self.ship(first, 400)
        self.ship(second, 500)
        saved = self.save_yield(first, 50)
        self.assertEqual(saved.status_code, 200, saved.content)
        other = self.get_yield(second)
        self.assertTrue(other["combined"])
        self.assertEqual(other["yield_percent"], "95.00")
        self.assertEqual(other["remaining_quantity"], 50)
        self.assertEqual(OrderYieldRemainder.objects.count(), 1)
        zero = self.save_yield(second, 0)
        self.assertEqual(zero.json()["yield_percent"], "90.00")
        self.assertTrue(zero.json()["remaining_recorded"])

    def test_transitive_groups_and_pagination_keep_the_full_scope(self):
        a, b, c = [self.order(order_quantity=60) for _ in range(3)]
        one, two = self.create_run([a, b], 10), self.create_run([b, c], 10)
        for order in (a, b, c):
            self.ship(order, 60)
        response = self.client.get("/api/orders/orders/", {"status": "COMPLETED", "page_size": 1})
        self.assertEqual(response.status_code, 200, response.content)
        summary = response.json()["results"][0]["final_yield"]
        self.assertEqual(summary["order_ids"], [a.pk, b.pk, c.pk])
        self.assertEqual(summary["run_ids"], [one.pk, two.pk])
        self.assertEqual(summary["production_quantity"], 200)
        self.assertEqual(summary["effective_shipped_quantity"], 180)
        self.assertEqual(summary["yield_percent"], "90.00")

    def test_cancelled_logs_runs_and_false_primary_link_are_not_counted(self):
        a, b = self.order(), self.order()
        legacy = self.create_run([a], 100, links=False)
        cancelled_log = self.create_run([a], 10)
        cancelled_log.daily_logs.update(is_cancelled=True)
        cancelled_run = self.create_run([a], 50)
        ProductionRun.objects.filter(pk=cancelled_run.pk).update(status="CANCELLED")
        other = self.create_run([b], 20)
        ProductionRun.objects.filter(pk=other.pk).update(order=a)
        self.ship(a)
        data = self.get_yield(a)
        self.assertEqual(data["production_quantity"], 1000)
        self.assertNotIn(other.pk, data["run_ids"])
        self.assertIn(legacy.pk, data["run_ids"])

    def test_existing_run_yield_snapshot_is_not_reused_as_order_yield(self):
        order = self.order()
        run = self.create_run([order])
        ProductionYieldRecord.objects.create(run=run, source_order_ids=[order.pk], production_quantity=10,
                                             effective_shipped_quantity=10, remaining_quantity=5, confirmed_by=self.user)
        self.ship(order)
        self.assertEqual(self.get_yield(order)["yield_percent"], "90.00")
        self.assertEqual(ProductionYieldRecord.objects.get().remaining_quantity, 5)

    def test_remainders_do_not_modify_inventory_or_original_quantities(self):
        order = self.order()
        run = self.create_run([order])
        shipment = self.ship(order)
        inventory = (InventoryBatch.objects.count(), InventoryTransaction.objects.count())
        saved = self.save_yield(order, 50)
        self.assertEqual(saved.status_code, 200, saved.content)
        self.assertEqual(saved.json()["yield_percent"], "95.00")
        self.assertEqual(inventory, (InventoryBatch.objects.count(), InventoryTransaction.objects.count()))
        self.assertEqual(run.daily_logs.get().produced_mold_count, 100)
        shipment.refresh_from_db()
        self.assertEqual(shipment.shipped_quantity, 900)
        self.assertEqual(QualityOrder.objects.get(pk=order.pk).order_quantity, 900)

    def test_return_reopens_order_then_reshipment_recalculates_net_not_gross(self):
        order = self.order()
        self.create_run([order])
        shipment = self.ship(order)
        self.save_yield(order, 50)
        ReturnRework.objects.create(shipment=shipment, rework_date=timezone.localdate(),
                                   reason_category="PACKAGING", returned_quantity=100, created_by=self.user)
        QualityOrder.objects.filter(pk=order.pk).update(status="OPEN")
        response = self.client.get(f"/api/orders/orders/{order.pk}/")
        self.assertIsNone(response.json()["final_yield"])
        self.assertIsNone(self.get_yield(order)["yield_percent"])
        self.ship(order, 100)
        QualityOrder.objects.filter(pk=order.pk).update(status="COMPLETED")
        self.assertEqual(self.get_yield(order)["effective_shipped_quantity"], 900)
        self.assertEqual(self.get_yield(order)["yield_percent"], "95.00")

    def test_weighted_scan_shipments_and_returns_use_the_canonical_balance(self):
        order = self.order()
        self.create_run([order])
        card = ProcessCard.objects.create(card_no="YIELD-CARD", order=order, created_by=self.user,
                                          unit_weight_g=Decimal("1"), quantity=900)
        batch = QualityShipmentBatch.objects.create(shipment_no="YIELD-WEIGHTED", shipment_date=timezone.localdate(),
                                                     order=order, unit_weight_g=Decimal("1"), status="CONFIRMED", created_by=self.user)
        line = QualityShipmentLine.objects.create(batch=batch, order=order, process_card=card,
                                                   net_weight_kg=Decimal("0.900"), piece_quantity=900, unit_weight_g_snapshot=Decimal("1"))
        QualityReworkCase.objects.create(shipment_line=line, process_card=card, origin="CUSTOMER_RETURN",
                                         affected_quantity=100, affected_weight_kg=Decimal("0.100"), created_by=self.user)
        self.assertEqual(self.get_yield(order)["effective_shipped_quantity"], 800)
        self.assertEqual(self.get_yield(order)["yield_percent"], "80.00")

    def test_source_and_two_tab_conflicts_are_rejected_without_overwriting(self):
        order = self.order()
        self.create_run([order])
        shipment = self.ship(order)
        old = self.get_yield(order)["source_token"]
        QualityShipment.objects.filter(pk=shipment.pk).update(shipped_quantity=800)
        self.assertEqual(self.save_yield(order, 20, token=old).status_code, 409)
        fresh = self.get_yield(order)["source_token"]
        self.assertEqual(self.save_yield(order, 50, token=fresh).status_code, 200)
        self.assertEqual(self.save_yield(order, 60, token=fresh).status_code, 409)
        self.assertEqual(OrderYieldRemainder.objects.get().remaining_quantity, 50)

    def test_new_production_scope_does_not_reuse_old_remainder(self):
        order = self.order()
        self.create_run([order])
        self.ship(order)
        self.save_yield(order, 50)
        self.create_run([order], 10)
        current = self.get_yield(order)
        self.assertTrue(current["scope_changed"])
        self.assertEqual(current["saved_remaining_quantity"], 50)
        self.assertEqual(current["remaining_quantity"], 0)
        self.assertFalse(current["remaining_recorded"])
        self.assertEqual(current["yield_percent"], "81.82")

    def test_merge_does_not_sum_prior_groups_remainders(self):
        first, second = self.order(), self.order()
        self.create_run([first])
        self.create_run([second])
        self.ship(first)
        self.ship(second)
        self.save_yield(first, 30)
        self.save_yield(second, 40)
        self.create_run([first, second], 10)
        data = self.get_yield(first)
        self.assertEqual(data["remaining_quantity"], 0)
        self.assertFalse(data["remaining_recorded"])

    def test_incomplete_or_mismatched_group_does_not_show_final_percentage(self):
        a, b = self.order(), self.order(status="OPEN")
        run = self.create_run([a, b])
        self.assertEqual(self.get_yield(a)["status"], "GROUP_INCOMPLETE")
        QualityOrder.objects.filter(pk=b.pk).update(status="COMPLETED", material="EPDM")
        self.assertEqual(self.get_yield(a)["status"], "SOURCE_MISMATCH")
        QualityOrder.objects.filter(pk=b.pk).update(material=a.material)
        ProductionRun.objects.filter(pk=run.pk).update(status="PLANNED")
        self.assertEqual(self.get_yield(a)["status"], "PRODUCTION_INCOMPLETE")

    def test_percentage_above_100_is_visible_and_not_clamped(self):
        order = self.order()
        self.create_run([order])
        self.ship(order)
        saved = self.save_yield(order, 200)
        self.assertEqual(saved.status_code, 200, saved.content)
        self.assertEqual(saved.json()["yield_percent"], "110.00")
        self.assertTrue(saved.json()["over_100"])

    def test_input_and_read_only_permissions(self):
        order = self.order()
        self.create_run([order])
        for invalid in (-1, "wrong", 1.5, 2147483648):
            self.assertEqual(self.save_yield(order, invalid).status_code, 400)
        self.user.groups.add(Group.objects.create(name="GPT只读测试"))
        self.assertEqual(self.get_yield(order)["status"], "READY")
        self.assertEqual(self.save_yield(order, 10).status_code, 403)
        self.assertEqual(OrderYieldRemainder.objects.count(), 0)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(f"/api/orders/orders/{order.pk}/final-yield/").status_code, 403)

    def test_batch_queries_do_not_grow_per_order(self):
        orders = [self.order() for _ in range(20)]
        for order in orders:
            self.create_run([order])
        with CaptureQueriesContext(connection) as single:
            order_yields([orders[0].pk])
        with CaptureQueriesContext(connection) as many:
            results = order_yields([order.pk for order in orders])
        self.assertEqual(len(results), 20)
        self.assertLessEqual(len(many), len(single) + 1)
        self.assertLessEqual(len(many), 8)
