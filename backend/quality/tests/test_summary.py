from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from quality.models import (
    QualityEmployee,
    QualityReturnAllocation,
    QualityReworkAttempt,
    QualityReworkCase,
    QualityShipmentBatch,
    QualityShipmentLine,
)

from .helpers import QualityTestMixin


class QualitySummaryApiTests(QualityTestMixin, TestCase):
    def assert_decimal_value(self, value, expected):
        self.assertEqual(Decimal(str(value)), Decimal(str(expected)))

    def test_summary_returns_period_totals_daily_order_and_employee_statistics(self):
        today = timezone.localdate()
        first_day = today - timedelta(days=1)
        first = self.create_shipment(
            shipment_no="SHP-SUMMARY-001",
            shipment_date=first_day,
            inspection_quantity=100,
            qualified_quantity=90,
            defective_quantity=10,
            shipped_quantity=80,
        )
        self.create_shipment(
            shipment_no="SHP-SUMMARY-002",
            shipment_date=today,
            inspection_quantity=50,
            qualified_quantity=45,
            defective_quantity=5,
            shipped_quantity=40,
        )
        self.create_rework(
            first,
            rework_date=today,
            returned_quantity=20,
            reworked_quantity=20,
            recovered_quantity=18,
            scrap_quantity=2,
        )

        outside_order = type(self.order).objects.create(
            order_no="ORD-OUTSIDE-RANGE",
            product_name="区间外产品",
            specification="OUT",
            material="NBR",
            order_quantity=100,
            order_date=first_day - timedelta(days=10),
            created_by=self.user,
        )
        self.create_shipment(
            shipment_no="SHP-OUTSIDE-RANGE",
            shipment_date=first_day - timedelta(days=10),
            order=outside_order,
            inspection_quantity=1000,
            qualified_quantity=1000,
            defective_quantity=0,
            shipped_quantity=1000,
        )

        response = self.client.get(
            "/api/quality/summary/",
            {"date_from": first_day.isoformat(), "date_to": today.isoformat()},
        )
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertEqual(
            payload["period"],
            {"date_from": first_day.isoformat(), "date_to": today.isoformat()},
        )

        totals = payload["totals"]
        expected_quantities = {
            "inspection_quantity": 150,
            "qualified_quantity": 135,
            "defective_quantity": 15,
            "shipped_quantity": 120,
            "returned_quantity": 20,
            "reworked_quantity": 20,
            "recovered_quantity": 18,
            "scrap_quantity": 2,
            "shipment_count": 2,
            "order_count": 1,
        }
        for key, expected in expected_quantities.items():
            self.assertEqual(totals[key], expected, key)
        self.assert_decimal_value(totals["first_pass_rate"], "90.00")
        self.assert_decimal_value(totals["return_rate"], "16.67")
        self.assert_decimal_value(totals["rework_pass_rate"], "90.00")

        trend = {item["date"]: item for item in payload["daily_trend"]}
        self.assertEqual(
            {
                key: trend[first_day.isoformat()][key]
                for key in (
                    "inspection_quantity",
                    "qualified_quantity",
                    "defective_quantity",
                    "shipped_quantity",
                    "returned_quantity",
                    "reworked_quantity",
                    "recovered_quantity",
                    "scrap_quantity",
                )
            },
            {
                "inspection_quantity": 100,
                "qualified_quantity": 90,
                "defective_quantity": 10,
                "shipped_quantity": 80,
                "returned_quantity": 0,
                "reworked_quantity": 0,
                "recovered_quantity": 0,
                "scrap_quantity": 0,
            },
        )
        self.assertEqual(trend[today.isoformat()]["inspection_quantity"], 50)
        self.assertEqual(trend[today.isoformat()]["returned_quantity"], 20)
        self.assertEqual(trend[today.isoformat()]["reworked_quantity"], 20)

        order_stats = {item["order_no"]: item for item in payload["order_stats"]}
        order_item = order_stats[self.order.order_no]
        self.assertEqual(order_item["inspection_quantity"], 150)
        self.assertEqual(order_item["shipped_quantity"], 120)
        self.assertEqual(order_item["returned_quantity"], 20)
        self.assertNotIn(outside_order.order_no, order_stats)

        employee_stats = {
            item["employee_no"]: item for item in payload["employee_stats"]
        }
        inspector = employee_stats[self.inspector.employee_no]
        self.assertEqual(inspector["inspection_quantity"], 150)
        self.assertEqual(inspector["inspection_days"], 2)
        self.assertEqual(inspector["shipment_count"], 2)
        self.assertEqual(inspector["responsible_return_quantity"], 20)
        reworker = employee_stats[self.reworker.employee_no]
        self.assertEqual(reworker["reworked_quantity"], 20)

    def test_summary_zero_denominators_return_zero_rates(self):
        day = timezone.localdate() - timedelta(days=30)
        response = self.client.get(
            "/api/quality/summary/",
            {"date_from": day.isoformat(), "date_to": day.isoformat()},
        )
        self.assertEqual(response.status_code, 200, response.content)
        totals = response.json()["totals"]
        self.assert_decimal_value(totals["first_pass_rate"], 0)
        self.assert_decimal_value(totals["return_rate"], 0)
        self.assert_decimal_value(totals["rework_pass_rate"], 0)

    def test_summary_includes_only_confirmed_weighted_batches_once(self):
        today = timezone.localdate()
        second_inspector = QualityEmployee.objects.create(
            employee_no="QC-SUM-002",
            name="王品检",
            role=QualityEmployee.Role.INSPECTOR,
            team="白班",
        )
        self.create_shipment(
            shipment_no="SHP-SUM-LEGACY",
            shipment_date=today,
            inspection_quantity=20,
            qualified_quantity=20,
            defective_quantity=0,
            shipped_quantity=20,
        )

        confirmed = QualityShipmentBatch.objects.create(
            shipment_no="SHP-SUM-WEIGHTED",
            shipment_date=today,
            order=self.order,
            inspector=self.inspector,
            status=QualityShipmentBatch.Status.CONFIRMED,
            created_by=self.user,
        )
        confirmed.inspectors.set([self.inspector, second_inspector])
        for quantity in (41, 60):
            QualityShipmentLine.objects.create(
                batch=confirmed,
                order=self.order,
                net_weight_kg=Decimal(quantity) / Decimal("1000"),
                piece_quantity=quantity,
                unit_weight_g_snapshot=Decimal("1"),
            )

        draft = QualityShipmentBatch.objects.create(
            shipment_no="SHP-SUM-DRAFT",
            shipment_date=today,
            order=self.order,
            inspector=self.inspector,
            status=QualityShipmentBatch.Status.DRAFT,
            created_by=self.user,
        )
        QualityShipmentLine.objects.create(
            batch=draft,
            order=self.order,
            net_weight_kg=Decimal("0.300"),
            piece_quantity=300,
            unit_weight_g_snapshot=Decimal("1"),
        )

        void = QualityShipmentBatch.objects.create(
            shipment_no="SHP-SUM-VOID",
            shipment_date=today,
            order=self.order,
            inspector=self.inspector,
            status=QualityShipmentBatch.Status.DRAFT,
            created_by=self.user,
        )
        QualityShipmentLine.objects.create(
            batch=void,
            order=self.order,
            net_weight_kg=Decimal("0.400"),
            piece_quantity=400,
            unit_weight_g_snapshot=Decimal("1"),
        )
        QualityShipmentBatch.objects.filter(pk=void.pk).update(
            status=QualityShipmentBatch.Status.VOID
        )

        response = self.client.get(
            "/api/quality/summary/",
            {"date_from": today.isoformat(), "date_to": today.isoformat()},
        )
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()

        # The company total is legacy 20 + weighted 101, not one copy of the
        # weighted quantity for each of the two inspectors.
        self.assertEqual(payload["totals"]["shipped_quantity"], 121)
        self.assertEqual(payload["totals"]["shipment_count"], 2)
        self.assertEqual(payload["totals"]["order_count"], 1)

        day = payload["daily_trend"][0]
        self.assertEqual(day["shipped_quantity"], 121)
        self.assertEqual(day["shipment_count"], 2)

        order = payload["order_stats"][0]
        self.assertEqual(order["shipped_quantity"], 121)
        self.assertEqual(order["shipment_count"], 2)

        employees = {
            item["employee_no"]: item for item in payload["employee_stats"]
        }
        primary = employees[self.inspector.employee_no]
        secondary = employees[second_inspector.employee_no]
        self.assertEqual(primary["shipped_quantity"], 71)
        self.assertEqual(primary["shipment_count"], 2)
        self.assertEqual(primary["inspection_days"], 1)
        self.assertEqual(secondary["shipped_quantity"], 50)
        self.assertEqual(secondary["shipment_count"], 1)
        self.assertEqual(secondary["inspection_days"], 1)
        self.assertEqual(
            primary["shipped_quantity"] + secondary["shipped_quantity"], 121
        )

    def test_summary_and_ledger_include_whole_batch_return_and_every_rework_round(self):
        today = timezone.localdate()
        batch = QualityShipmentBatch.objects.create(
            shipment_no="SHP-SUM-WHOLE-RETURN",
            shipment_date=today,
            order=self.order,
            inspector=self.inspector,
            single_batch_net_weight_kg=Decimal("0.200"),
            product_batch_count=3,
            pieces_per_batch=100,
            status=QualityShipmentBatch.Status.CONFIRMED,
            created_by=self.user,
        )
        line = QualityShipmentLine.objects.create(
            batch=batch,
            order=self.order,
            net_weight_kg=Decimal("0.600"),
            piece_quantity=300,
            unit_weight_g_snapshot=Decimal("2"),
        )
        case = QualityReworkCase.objects.create(
            origin=QualityReworkCase.Origin.CUSTOMER_RETURN,
            shipment_batch=batch,
            shipment_unit_no=1,
            opened_on=today,
            reason_category="APPEARANCE",
            responsible_inspector=self.inspector,
            affected_quantity=100,
            affected_weight_kg=Decimal("0.200"),
            created_by=self.user,
        )
        QualityReturnAllocation.objects.create(
            case=case,
            shipment_line=line,
            piece_quantity=100,
            net_weight_kg=Decimal("0.200"),
        )
        QualityReworkAttempt.objects.create(
            case=case,
            attempt_date=today,
            rework_employee=self.reworker,
            input_quantity=100,
            reworked_quantity=100,
            recovered_quantity=0,
            scrap_quantity=0,
            input_weight_kg=Decimal("0.200"),
            reworked_weight_kg=Decimal("0.200"),
            status=QualityReworkCase.Status.WAITING_REINSPECTION,
            created_by=self.user,
        )
        QualityReworkAttempt.objects.create(
            case=case,
            attempt_date=today,
            rework_employee=self.reworker,
            input_quantity=100,
            reworked_quantity=100,
            recovered_quantity=90,
            scrap_quantity=10,
            input_weight_kg=Decimal("0.200"),
            reworked_weight_kg=Decimal("0.200"),
            recovered_weight_kg=Decimal("0.180"),
            scrap_weight_kg=Decimal("0.020"),
            status=QualityReworkCase.Status.COMPLETED,
            created_by=self.user,
        )

        response = self.client.get(
            "/api/quality/summary/",
            {"date_from": today.isoformat(), "date_to": today.isoformat()},
        )
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertEqual(payload["totals"]["shipped_quantity"], 300)
        self.assertEqual(payload["totals"]["returned_quantity"], 100)
        self.assertEqual(payload["totals"]["reworked_quantity"], 200)
        self.assertEqual(payload["totals"]["recovered_quantity"], 90)
        self.assertEqual(payload["totals"]["scrap_quantity"], 10)
        self.assertEqual(payload["daily_trend"][0]["returned_quantity"], 100)
        self.assertEqual(payload["daily_trend"][0]["reworked_quantity"], 200)

        order = payload["order_stats"][0]
        self.assertEqual(order["returned_quantity"], 100)
        self.assertEqual(order["reworked_quantity"], 200)
        self.assertEqual(order["rework_count"], 2)
        employees = {
            item["employee_no"]: item for item in payload["employee_stats"]
        }
        self.assertEqual(
            employees[self.inspector.employee_no]["responsible_return_quantity"],
            100,
        )
        self.assertEqual(
            employees[self.reworker.employee_no]["reworked_quantity"], 200
        )

        ledger = self.client.get(
            "/api/quality/shipment-ledger/",
            {"shipment_status": "CONFIRMED", "page_size": 1000},
        )
        self.assertEqual(ledger.status_code, 200, ledger.content)
        rows = ledger.json().get("results", ledger.json())
        row = next(item for item in rows if item["source_id"] == batch.pk)
        self.assertEqual(row["returned_quantity"], 100)
        self.assertEqual(row["rework_count"], 2)

    def test_whole_batch_return_is_split_to_its_allocated_orders_without_double_counting(self):
        today = timezone.localdate()
        second_order = type(self.order).objects.create(
            order_no="ORD-SUM-RETURN-SECOND",
            product_name=self.order.product_name,
            specification=self.order.specification,
            material=self.order.material,
            order_quantity=100,
            order_date=today,
            created_by=self.user,
        )
        batch = QualityShipmentBatch.objects.create(
            shipment_no="SHP-SUM-MULTI-ORDER-RETURN",
            shipment_date=today,
            order=self.order,
            single_batch_net_weight_kg=Decimal("0.200"),
            product_batch_count=1,
            pieces_per_batch=100,
            status=QualityShipmentBatch.Status.CONFIRMED,
            created_by=self.user,
        )
        first_line = QualityShipmentLine.objects.create(
            batch=batch,
            order=self.order,
            net_weight_kg=Decimal("0.080"),
            piece_quantity=40,
            unit_weight_g_snapshot=Decimal("2"),
        )
        second_line = QualityShipmentLine.objects.create(
            batch=batch,
            order=second_order,
            net_weight_kg=Decimal("0.120"),
            piece_quantity=60,
            unit_weight_g_snapshot=Decimal("2"),
        )
        case = QualityReworkCase.objects.create(
            origin=QualityReworkCase.Origin.CUSTOMER_RETURN,
            shipment_batch=batch,
            shipment_unit_no=1,
            opened_on=today,
            affected_quantity=100,
            affected_weight_kg=Decimal("0.200"),
            created_by=self.user,
        )
        QualityReturnAllocation.objects.create(
            case=case,
            shipment_line=first_line,
            piece_quantity=40,
            net_weight_kg=Decimal("0.080"),
        )
        QualityReturnAllocation.objects.create(
            case=case,
            shipment_line=second_line,
            piece_quantity=60,
            net_weight_kg=Decimal("0.120"),
        )

        payload = self.client.get(
            "/api/quality/summary/",
            {"date_from": today.isoformat(), "date_to": today.isoformat()},
        ).json()
        self.assertEqual(payload["totals"]["returned_quantity"], 100)
        orders = {row["order_id"]: row for row in payload["order_stats"]}
        self.assertEqual(orders[self.order.pk]["returned_quantity"], 40)
        self.assertEqual(orders[second_order.pk]["returned_quantity"], 60)

        QualityReworkCase.objects.filter(pk=case.pk).update(
            status=QualityReworkCase.Status.CANCELLED
        )
        cancelled_payload = self.client.get(
            "/api/quality/summary/",
            {"date_from": today.isoformat(), "date_to": today.isoformat()},
        ).json()
        self.assertEqual(cancelled_payload["totals"]["returned_quantity"], 0)
