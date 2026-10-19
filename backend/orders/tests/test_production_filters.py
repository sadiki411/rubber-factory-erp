from datetime import date

from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APITestCase

from orders.services import production_totals_by_order
from production.models import ProductionDailyLog, ProductionRun, ProductionRunOrder
from quality.models import QualityEmployee, QualityOrder, QualityShipment


class ProductionRequirementFilterTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="production-filter")
        self.client.force_authenticate(self.user)

    def order(self, suffix, **overrides):
        return QualityOrder.objects.create(**{
            "order_no": f"FILTER-{suffix}", "specification": "FILTER-SPEC",
            "material": "NBR", "order_quantity": 100, "production_required": True,
            "created_by": self.user, **overrides,
        })

    def create_run(self, order, molds, **overrides):
        run = ProductionRun.objects.create(**{
            "order": order, "order_no": order.order_no,
            "specification": order.specification, "material": order.material,
            "order_quantity": order.order_quantity, "cavities": 10,
            "planned_mold_count": 10, "is_ledger_only": True,
            "created_by": self.user, **overrides,
        })
        ProductionDailyLog.objects.create(
            run=run, operator="测试员工", sequence_no=1,
            cumulative_mold_count=molds, produced_mold_count=molds,
            cavities_snapshot=run.cavities,
        )
        return run

    def filtered(self, value, **params):
        response = self.client.get("/api/orders/orders/", {
            "production_required": value, "page_size": 100, **params,
        })
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def test_completed_output_is_excluded_from_need_and_included_in_no_need(self):
        complete = self.order("DONE")
        partial = self.order("PARTIAL")
        no_logs = self.order("NO-LOGS")
        self.create_run(complete, 12)
        self.create_run(partial, 4)
        before = list(QualityOrder.objects.order_by("pk").values())

        for alias in ("true", "yes", "1"):
            data = self.filtered(alias, status="OPEN")
            self.assertEqual({row["id"] for row in data["results"]}, {partial.pk, no_logs.pk})
            self.assertTrue(all(row["production_requirement_status"] == "REQUIRED" for row in data["results"]))
        for alias in ("false", "no", "0"):
            data = self.filtered(alias, status="OPEN")
            self.assertEqual([row["id"] for row in data["results"]], [complete.pk])
            self.assertEqual(data["results"][0]["production_requirement_status"], "PRODUCTION_COMPLETE")
            self.assertEqual(data["results"][0]["status"], "OPEN")
            self.assertEqual(data["results"][0]["produced_quantity"], 120)
        self.assertEqual(list(QualityOrder.objects.order_by("pk").values()), before)

    def test_delivery_manual_no_need_cancelled_and_unknown_match_display(self):
        delivered = self.order("DELIVERED")
        manual = self.order("MANUAL", production_required=False)
        cancelled = self.order("CANCELLED", status=QualityOrder.Status.CANCELLED)
        unknown = self.order("UNKNOWN", production_required=None)
        unknown_done = self.order("UNKNOWN-DONE", production_required=None)
        self.create_run(unknown_done, 10)
        inspector = QualityEmployee.objects.create(name="筛选品检员", role=QualityEmployee.Role.INSPECTOR)
        QualityShipment.objects.create(
            shipment_no="FILTER-DELIVERY", shipment_date=timezone.localdate(),
            order=delivered, inspector=inspector, inspection_quantity=100,
            qualified_quantity=100, defective_quantity=0, shipped_quantity=100,
            created_by=self.user,
        )

        self.assertEqual(self.filtered("true")["count"], 0)
        data = self.filtered("false")
        states = {row["id"]: row["production_requirement_status"] for row in data["results"]}
        self.assertEqual(states, {
            delivered.pk: "DELIVERY_COMPLETE", manual.pk: "NOT_REQUIRED",
            cancelled.pk: "CANCELLED", unknown_done.pk: "PRODUCTION_COMPLETE",
        })
        self.assertNotIn(unknown.pk, states)
        self.assertEqual(self.filtered("false", status="OPEN")["count"], 3)

    def test_combined_task_keeps_all_selected_orders_when_filtering_one(self):
        early = self.order("EARLY", order_quantity=60, due_date=date(2026, 1, 1))
        late = self.order("LATE", order_quantity=40, due_date=date(2026, 1, 2))
        unselected = self.order("UNSELECTED", order_quantity=10)
        run = self.create_run(early, 8)
        ProductionRunOrder.objects.create(run=run, order=late, planned_quantity=40, sequence=1)
        ProductionRunOrder.objects.create(run=run, order=early, planned_quantity=60, sequence=2)

        # Searching only the later order must not give it the earlier one's output.
        data = self.filtered("true", q=late.order_no)
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["results"][0]["produced_quantity"], 20)
        self.assertEqual(data["results"][0]["production_remaining_quantity"], 20)
        self.assertEqual([row["id"] for row in self.filtered("false")["results"]], [early.pk])
        ProductionDailyLog.objects.filter(run=run).update(produced_mold_count=12)
        done = self.filtered("false")["results"]
        self.assertEqual({row["id"]: row["produced_quantity"] for row in done}, {early.pk: 60, late.pk: 60})
        self.assertEqual([row["id"] for row in self.filtered("true")["results"]], [unselected.pk])

    def test_cancelled_records_and_cavity_snapshots_use_existing_quantity_rules(self):
        order = self.order("CANCELLED-LOGS")
        first = self.create_run(order, 5)
        ProductionDailyLog.objects.filter(run=first).update(cavities_snapshot=6, defective_quantity=2)
        cancelled = self.create_run(order, 20)
        ProductionRun.objects.filter(pk=cancelled.pk).update(status=ProductionRun.Status.CANCELLED)
        removed = self.create_run(order, 20)
        ProductionDailyLog.objects.filter(run=removed).update(is_cancelled=True)
        data = self.filtered("true")["results"][0]
        self.assertEqual(data["produced_quantity"], 28)
        self.assertEqual(data["production_run_count"], 2)
        self.assertEqual(data["production_remaining_quantity"], 72)
        # The unfiltered/detail serializer must agree with the filter's batch facts.
        detail = self.client.get(f"/api/orders/orders/{order.pk}/").json()
        for field in ("produced_quantity", "production_run_count", "production_requirement_status"):
            self.assertEqual(data[field], detail[field])

    def test_log_correction_changes_filter_without_rewriting_order_flag(self):
        order = self.order("CORRECTION")
        run = self.create_run(order, 10)
        self.assertEqual(self.filtered("true")["count"], 0)
        ProductionDailyLog.objects.filter(run=run).update(produced_mold_count=9)
        self.assertEqual(self.filtered("true")["count"], 1)
        self.assertEqual(self.filtered("false")["count"], 0)
        order.refresh_from_db()
        self.assertTrue(order.production_required)
        self.assertEqual(order.status, QualityOrder.Status.OPEN)

    def test_filter_runs_before_pagination_and_respects_other_filters(self):
        required = [self.order(f"PAGE-{index}") for index in range(5)]
        done = self.order("PAGE-DONE")
        self.create_run(done, 10)
        other = self.order("OTHER-SPEC", specification="OTHER")
        first = self.filtered("true", q="PAGE", page_size=2, page=1)
        second = self.filtered("true", q="PAGE", page_size=2, page=2)
        third = self.filtered("true", q="PAGE", page_size=2, page=3)
        self.assertEqual([page["count"] for page in (first, second, third)], [5, 5, 5])
        ids = [row["id"] for page in (first, second, third) for row in page["results"]]
        self.assertEqual(set(ids), {order.pk for order in required})
        self.assertEqual(len(ids), len(set(ids)))
        self.assertNotIn(other.pk, ids)

    def test_production_totals_queries_do_not_grow_per_order(self):
        orders = [self.order(f"BATCH-{index}") for index in range(12)]
        for order in orders:
            self.create_run(order, 10)
        with CaptureQueriesContext(connection) as small:
            production_totals_by_order([orders[0].pk])
        with CaptureQueriesContext(connection) as large:
            totals = production_totals_by_order([order.pk for order in orders])
        self.assertEqual(len(large), len(small))
        self.assertLessEqual(len(large), 3)
        self.assertTrue(all(value == (100, 1) for value in totals.values()))
