from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

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

