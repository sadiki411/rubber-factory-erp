from datetime import datetime, time, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from drf_spectacular.generators import SchemaGenerator
from rest_framework.test import APIClient

from production.models import ProductionDailyLog, ProductionRun, ProductionStation

from .helpers import ProductionTestMixin


class ProductionMetricTests(ProductionTestMixin, TestCase):
    def test_expected_change_and_financial_efficiency_metrics(self):
        loaded_at = timezone.now() - timedelta(hours=2)
        run = ProductionRun.objects.create(
            station=ProductionStation.objects.get(code="1"),
            order_no="ORD-METRIC",
            specification="O型圈",
            material="NBR",
            order_quantity=600,
            cavities=6,
            estimated_defect_rate=0,
            planned_mold_count=100,
            curing_seconds=144,
            estimated_hours=4,
            loaded_at=loaded_at,
            unloaded_at=loaded_at + timedelta(hours=2),
            status=ProductionRun.Status.COMPLETED,
            unit_price=2,
            material_unit_price=10,
            created_by=self.user,
        )
        self.assertEqual(run.expected_change_at, loaded_at + timedelta(hours=4))
        ProductionDailyLog.objects.create(
            run=run,
            production_date=timezone.localdate(),
            operator="张三",
            produced_mold_count=50,
        )
        run.actual_good_quantity = 290
        run.actual_defective_quantity = 10
        run.total_material_kg = 12
        run.labor_cost = 100
        run.energy_cost = 20
        run.other_cost = 5
        run.settled_at = run.unloaded_at
        run.settled_by = self.user
        run.save()
        self.assertEqual(run.actual_hours, Decimal("2.00"))
        self.assertEqual(run.progress_percent, Decimal("50.00"))
        self.assertEqual(run.remaining_mold_count, 50)
        self.assertEqual(run.revenue, Decimal("580.00"))
        self.assertEqual(run.total_cost, Decimal("245.00"))
        self.assertEqual(run.profit, Decimal("335.00"))
        self.assertEqual(run.hourly_efficiency, Decimal("100.00"))


class ProductionSchemaTests(TestCase):
    def test_settlement_get_and_post_have_distinct_response_schemas(self):
        schema = SchemaGenerator().get_schema(request=None, public=True)
        settlement_path = schema["paths"]["/api/production/runs/{id}/settlement/"]
        get_schema = settlement_path["get"]["responses"]["200"]["content"][
            "application/json"
        ]["schema"]
        post_schema = settlement_path["post"]["responses"]["200"]["content"][
            "application/json"
        ]["schema"]
        self.assertEqual(
            get_schema,
            {"$ref": "#/components/schemas/ProductionSettlementDetail"},
        )
        self.assertEqual(
            post_schema,
            {"$ref": "#/components/schemas/ProductionRun"},
        )
        detail_schema = schema["components"]["schemas"][
            "ProductionSettlementDetail"
        ]
        self.assertEqual(set(detail_schema["properties"]), {"run", "revisions"})


class ProductionApiTests(ProductionTestMixin, TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _payload(self, station="1", order_no="ORD-001", mold_id=None, **overrides):
        payload = {
            "station_id": ProductionStation.objects.get(code=station).pk,
            "mold_id": mold_id,
            "order_no": order_no,
            "specification": "密封圈 20x30",
            "material": "NBR",
            "order_quantity": 600,
            "cavities": 6,
            "estimated_defect_rate": "2.00",
            "curing_seconds": 72,
            "loaded_at": timezone.now().isoformat(),
            "operator": "张三",
            "unit_price": "2.0000",
            "material_unit_price": "10.0000",
        }
        payload.update(overrides)
        return payload

    def test_station_list_has_three_groups_with_two_linked_machines_each(self):
        response = self.client.get("/api/production/stations/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 6)
        self.assertEqual(
            [
                (item["group"], item["position_no"], item["code"])
                for item in response.json()
            ],
            [
                ("A", 1, "1"),
                ("A", 2, "2"),
                ("B", 1, "3"),
                ("B", 2, "4"),
                ("C", 1, "5"),
                ("C", 2, "6"),
            ],
        )

    def test_run_filter_accepts_numeric_machine_code_and_valid_legacy_alias(self):
        created = self.client.post(
            "/api/production/runs/",
            self._payload(station="3", order_no="FILTER-MACHINE-3"),
            format="json",
        )
        self.assertEqual(created.status_code, 201, created.content)

        for station_filter in ("3", "B01"):
            with self.subTest(station=station_filter):
                response = self.client.get(
                    "/api/production/runs/", {"station": station_filter}
                )
                self.assertEqual(response.status_code, 200, response.content)
                self.assertEqual(
                    [item["order_no"] for item in response.json()["results"]],
                    ["FILTER-MACHINE-3"],
                )

    def test_create_run_computes_plan_hours_and_blocks_station_and_mold_conflicts(self):
        mold = self.create_mold()
        created = self.client.post(
            "/api/production/runs/", self._payload(mold_id=mold.pk), format="json"
        )
        self.assertEqual(created.status_code, 201, created.content)
        item = created.json()
        self.assertEqual(item["status"], ProductionRun.Status.RUNNING)
        self.assertEqual(item["planned_mold_count"], 102)
        self.assertEqual(item["estimated_hours"], "2.04")
        self.assertIsNotNone(item["expected_change_at"])

        same_station = self.client.post(
            "/api/production/runs/",
            self._payload(order_no="ORD-002", mold_id=None),
            format="json",
        )
        self.assertEqual(same_station.status_code, 400)
        self.assertIn("station_id", same_station.json())

        same_mold = self.client.post(
            "/api/production/runs/",
            self._payload(station="2", order_no="ORD-003", mold_id=mold.pk),
            format="json",
        )
        self.assertEqual(same_mold.status_code, 400)
        self.assertIn("mold_id", same_mold.json())

    def test_daily_log_complete_board_and_summary(self):
        now = timezone.now()
        created = self.client.post(
            "/api/production/runs/",
            self._payload(
                planned_mold_count=100,
                estimated_hours="1.00",
                loaded_at=(now - timedelta(minutes=50)).isoformat(),
            ),
            format="json",
        )
        self.assertEqual(created.status_code, 201, created.content)
        run_id = created.json()["id"]
        log = self.client.post(
            f"/api/production/runs/{run_id}/daily-logs/",
            {
                "date": timezone.localdate().isoformat(),
                "operator": "张三",
                "produced_mold_count": 50,
            },
            format="json",
        )
        self.assertEqual(log.status_code, 201, log.content)
        self.assertEqual(log.json()["progress_percent"], "50.00")

        board = self.client.get("/api/production/board/", {"reminder_minutes": 30})
        self.assertEqual(board.status_code, 200)
        a01 = board.json()["groups"][0]["stations"][0]
        self.assertEqual(a01["reminder_status"], "DUE_SOON")
        self.assertEqual(a01["run"]["id"], run_id)
        self.assertEqual(board.json()["counts"]["total"], 6)

        summary = self.client.get("/api/production/summary/")
        self.assertEqual(summary.status_code, 200)
        self.assertEqual(summary.json()["run_count"], 1)
        self.assertEqual(summary.json()["good_quantity"], 0)
        self.assertEqual(summary.json()["revenue"], "0.00")
        self.assertEqual(summary.json()["total_cost"], "0.00")

        completed = self.client.post(
            f"/api/production/runs/{run_id}/complete/",
            {"unloaded_at": now.isoformat()},
            format="json",
        )
        self.assertEqual(completed.status_code, 200, completed.content)
        self.assertEqual(completed.json()["status"], ProductionRun.Status.COMPLETED)
        self.assertIsNotNone(completed.json()["unloaded_at"])
        settled = self.client.post(
            f"/api/production/runs/{run_id}/settlement/",
            {
                "actual_good_quantity": 295,
                "actual_defective_quantity": 5,
                "total_material_kg": "12.000",
                "labor_cost": "100.00",
                "energy_cost": "20.00",
                "other_cost": "5.00",
                "settlement_notes": "首轮结算",
            },
            format="json",
        )
        self.assertEqual(settled.status_code, 200, settled.content)
        self.assertTrue(settled.json()["is_settled"])
        summary = self.client.get("/api/production/summary/")
        self.assertEqual(summary.json()["good_quantity"], 295)
        self.assertEqual(summary.json()["revenue"], "590.00")
        self.assertEqual(summary.json()["total_cost"], "245.00")

        replacement = self.client.post(
            "/api/production/runs/",
            self._payload(order_no="ORD-REPLACE", loaded_at=None),
            format="json",
        )
        self.assertEqual(replacement.status_code, 201, replacement.content)
        self.assertEqual(replacement.json()["status"], ProductionRun.Status.PLANNED)

    def test_summary_date_range_only_counts_logs_inside_period(self):
        today = timezone.localdate()
        loaded_at = timezone.now() - timedelta(days=2)
        run = ProductionRun.objects.create(
            station=ProductionStation.objects.get(code="3"),
            order_no="ORD-PERIOD",
            specification="汇总范围测试",
            order_quantity=600,
            cavities=6,
            planned_mold_count=100,
            estimated_hours=48,
            loaded_at=loaded_at,
            unloaded_at=timezone.now(),
            status=ProductionRun.Status.COMPLETED,
            unit_price=2,
            material_unit_price=10,
            created_by=self.user,
        )
        ProductionDailyLog.objects.create(
            run=run,
            production_date=today - timedelta(days=1),
            operator="张三",
            produced_mold_count=40,
        )
        ProductionDailyLog.objects.create(
            run=run,
            production_date=today,
            operator="李四",
            produced_mold_count=10,
        )
        settled = self.client.post(
            f"/api/production/runs/{run.pk}/settlement/",
            {
                "actual_good_quantity": 293,
                "actual_defective_quantity": 7,
                "total_material_kg": "10.000",
                "labor_cost": "70.00",
                "energy_cost": "0.00",
                "other_cost": "0.00",
            },
            format="json",
        )
        self.assertEqual(settled.status_code, 200, settled.content)
        response = self.client.get(
            "/api/production/summary/",
            {"date_from": today.isoformat(), "date_to": today.isoformat()},
        )
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertEqual(payload["produced_mold_count"], 10)
        self.assertEqual(payload["good_quantity"], 293)
        self.assertEqual(payload["material_kg"], "10.00")
        self.assertEqual(payload["revenue"], "586.00")
        self.assertEqual(payload["total_cost"], "170.00")

    def test_daily_log_can_be_corrected_after_entry(self):
        created = self.client.post(
            "/api/production/runs/",
            self._payload(station="6", order_no="ORD-CORRECT"),
            format="json",
        )
        self.assertEqual(created.status_code, 201, created.content)
        run_id = created.json()["id"]
        logged = self.client.post(
            f"/api/production/runs/{run_id}/daily-logs/",
            {
                "date": timezone.localdate().isoformat(),
                "operator": "张三",
                "produced_mold_count": 10,
            },
            format="json",
        )
        self.assertEqual(logged.status_code, 201, logged.content)
        log_id = logged.json()["daily_logs"][0]["id"]
        corrected = self.client.patch(
            f"/api/production/runs/{run_id}/daily-logs/{log_id}/",
            {"produced_mold_count": 12, "notes": "修正模数"},
            format="json",
        )
        self.assertEqual(corrected.status_code, 200, corrected.content)
        self.assertEqual(corrected.json()["produced_mold_count"], 12)
        self.assertEqual(corrected.json()["daily_logs"][0]["notes"], "修正模数")

    def test_multiple_operators_same_day_and_normalized_uniqueness(self):
        created = self.client.post(
            "/api/production/runs/",
            self._payload(station="5", order_no="MULTI-OPERATOR"),
            format="json",
        )
        self.assertEqual(created.status_code, 201, created.content)
        run_id = created.json()["id"]
        today = timezone.localdate().isoformat()
        first = self.client.post(
            f"/api/production/runs/{run_id}/daily-logs/",
            {"date": today, "operator": "张三", "produced_mold_count": 5},
            format="json",
        )
        second = self.client.post(
            f"/api/production/runs/{run_id}/daily-logs/",
            {"date": today, "operator": "李四", "produced_mold_count": 7},
            format="json",
        )
        duplicate = self.client.post(
            f"/api/production/runs/{run_id}/daily-logs/",
            {"date": today, "operator": "  张三  ", "produced_mold_count": 1},
            format="json",
        )
        self.assertEqual(first.status_code, 201, first.content)
        self.assertEqual(second.status_code, 201, second.content)
        self.assertEqual(duplicate.status_code, 400, duplicate.content)
        self.assertEqual(second.json()["produced_mold_count"], 12)

    def test_settlement_revision_and_daily_or_price_change_invalidation(self):
        created = self.client.post(
            "/api/production/runs/",
            self._payload(station="5", order_no="SETTLEMENT-AUDIT"),
            format="json",
        )
        self.assertEqual(created.status_code, 201, created.content)
        run_id = created.json()["id"]
        logged = self.client.post(
            f"/api/production/runs/{run_id}/daily-logs/",
            {
                "date": timezone.localdate().isoformat(),
                "operator": "张三",
                "produced_mold_count": 10,
            },
            format="json",
        )
        self.assertEqual(logged.status_code, 201, logged.content)
        log_id = logged.json()["daily_logs"][0]["id"]
        completed = self.client.post(
            f"/api/production/runs/{run_id}/complete/", {}, format="json"
        )
        self.assertEqual(completed.status_code, 200, completed.content)
        settled = self.client.post(
            f"/api/production/runs/{run_id}/settlement/",
            {
                "actual_good_quantity": 58,
                "actual_defective_quantity": 2,
                "total_material_kg": "2.000",
                "labor_cost": "10.00",
                "energy_cost": "5.00",
                "other_cost": "0.00",
                "settlement_notes": "初次结算",
            },
            format="json",
        )
        self.assertEqual(settled.status_code, 200, settled.content)
        self.assertTrue(settled.json()["is_settled"])

        changed_log = self.client.patch(
            f"/api/production/runs/{run_id}/daily-logs/{log_id}/",
            {"produced_mold_count": 11},
            format="json",
        )
        self.assertEqual(changed_log.status_code, 200, changed_log.content)
        self.assertFalse(changed_log.json()["is_settled"])
        self.assertEqual(changed_log.json()["revenue"], "0.00")
        audit = self.client.get(f"/api/production/runs/{run_id}/settlement/")
        self.assertEqual(audit.status_code, 200, audit.content)
        self.assertEqual(audit.json()["revisions"][0]["action"], "INVALIDATED")
        self.assertEqual(audit.json()["revisions"][0]["produced_mold_count"], 10)

        resettled = self.client.post(
            f"/api/production/runs/{run_id}/settlement/",
            {
                "actual_good_quantity": 64,
                "actual_defective_quantity": 2,
                "total_material_kg": "2.200",
                "labor_cost": "11.00",
                "energy_cost": "5.00",
                "other_cost": "0.00",
            },
            format="json",
        )
        self.assertEqual(resettled.status_code, 200, resettled.content)
        changed_price = self.client.patch(
            f"/api/production/runs/{run_id}/",
            {"unit_price": "3.0000"},
            format="json",
        )
        self.assertEqual(changed_price.status_code, 200, changed_price.content)
        self.assertFalse(changed_price.json()["is_settled"])
        audit = self.client.get(f"/api/production/runs/{run_id}/settlement/")
        self.assertEqual(audit.json()["revisions"][0]["unit_price"], "2.0000")

    def test_monthly_performance_uses_operator_and_curing_snapshot(self):
        current_tz = timezone.get_current_timezone()
        loaded_at = timezone.make_aware(datetime(2026, 6, 1, 8), current_tz)
        unloaded_at = timezone.make_aware(datetime(2026, 6, 30, 18), current_tz)
        run = ProductionRun.objects.create(
            station=ProductionStation.objects.get(code="5"),
            order_no="PERFORMANCE-JUNE",
            specification="月绩效",
            order_quantity=600,
            cavities=6,
            planned_mold_count=100,
            curing_seconds=60,
            estimated_hours=10,
            loaded_at=loaded_at,
            unloaded_at=unloaded_at,
            status=ProductionRun.Status.COMPLETED,
            created_by=self.user,
        )
        ProductionDailyLog.objects.create(
            run=run,
            production_date=datetime(2026, 6, 2).date(),
            operator="张三",
            produced_mold_count=10,
        )
        ProductionDailyLog.objects.create(
            run=run,
            production_date=datetime(2026, 6, 3).date(),
            operator="张三",
            produced_mold_count=20,
        )
        ProductionDailyLog.objects.create(
            run=run,
            production_date=datetime(2026, 6, 2).date(),
            operator="李四",
            produced_mold_count=5,
        )
        run.curing_seconds = 120
        run.save()

        response = self.client.get(
            "/api/production/performance/monthly/", {"month": "2026-06"}
        )
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        by_operator = {item["operator"]: item for item in payload["operators"]}
        self.assertEqual(by_operator["张三"]["total_mold_count"], 30)
        self.assertEqual(by_operator["张三"]["production_days"], 2)
        self.assertEqual(by_operator["张三"]["average_daily_mold_count"], "15.00")
        self.assertEqual(by_operator["张三"]["production_hours"], "0.50")
        self.assertEqual(by_operator["李四"]["production_hours"], "0.08")
        self.assertEqual(payload["totals"]["total_mold_count"], 35)
        self.assertEqual(payload["totals"]["production_days"], 2)
        self.assertEqual(payload["totals"]["operator_day_count"], 3)

    def test_summary_finance_is_counted_only_on_settlement_date(self):
        current_tz = timezone.get_current_timezone()
        run = ProductionRun.objects.create(
            station=ProductionStation.objects.get(code="4"),
            order_no="CROSS-MONTH-SETTLEMENT",
            specification="跨月结算",
            order_quantity=180,
            cavities=6,
            planned_mold_count=30,
            loaded_at=timezone.make_aware(datetime(2026, 6, 30, 8), current_tz),
            unloaded_at=timezone.make_aware(datetime(2026, 7, 2, 10), current_tz),
            status=ProductionRun.Status.COMPLETED,
            unit_price=2,
            material_unit_price=10,
            created_by=self.user,
        )
        ProductionDailyLog.objects.create(
            run=run,
            production_date=datetime(2026, 6, 30).date(),
            operator="张三",
            produced_mold_count=10,
        )
        ProductionDailyLog.objects.create(
            run=run,
            production_date=datetime(2026, 7, 1).date(),
            operator="张三",
            produced_mold_count=20,
        )
        run.actual_good_quantity = 178
        run.actual_defective_quantity = 2
        run.total_material_kg = 5
        run.labor_cost = 20
        run.settled_at = timezone.make_aware(datetime(2026, 7, 2, 12), current_tz)
        run.settled_by = self.user
        run.save()

        june = self.client.get(
            "/api/production/summary/",
            {"date_from": "2026-06-30", "date_to": "2026-06-30"},
        ).json()
        july_first = self.client.get(
            "/api/production/summary/",
            {"date_from": "2026-07-01", "date_to": "2026-07-01"},
        ).json()
        settlement_day = self.client.get(
            "/api/production/summary/",
            {"date_from": "2026-07-02", "date_to": "2026-07-02"},
        ).json()
        self.assertEqual(june["produced_mold_count"], 10)
        self.assertEqual(june["revenue"], "0.00")
        self.assertEqual(july_first["produced_mold_count"], 20)
        self.assertEqual(july_first["revenue"], "0.00")
        self.assertEqual(settlement_day["produced_mold_count"], 0)
        self.assertEqual(settlement_day["revenue"], "356.00")
        self.assertEqual(settlement_day["total_cost"], "70.00")

    def test_status_time_matrix_is_rejected_by_api_and_database(self):
        now = timezone.now().replace(microsecond=0)
        invalid_payloads = [
            self._payload(
                station="1",
                order_no="BAD-PLANNED",
                status=ProductionRun.Status.PLANNED,
                loaded_at=now.isoformat(),
            ),
            self._payload(
                station="2",
                order_no="BAD-RUNNING",
                status=ProductionRun.Status.RUNNING,
                unloaded_at=(now + timedelta(hours=1)).isoformat(),
            ),
            self._payload(
                station="3",
                order_no="BAD-COMPLETED",
                status=ProductionRun.Status.COMPLETED,
                loaded_at=None,
                unloaded_at=(now + timedelta(hours=1)).isoformat(),
            ),
            self._payload(
                station="4",
                order_no="BAD-CANCELLED",
                status=ProductionRun.Status.CANCELLED,
                loaded_at=now.isoformat(),
                unloaded_at=None,
            ),
        ]
        for payload in invalid_payloads:
            response = self.client.post(
                "/api/production/runs/", payload, format="json"
            )
            self.assertEqual(response.status_code, 400, response.content)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ProductionRun.objects.bulk_create(
                    [
                        ProductionRun(
                            station=ProductionStation.objects.get(code="5"),
                            order_no="DB-BAD-PLANNED",
                            specification="数据库约束测试",
                            order_quantity=60,
                            cavities=6,
                            planned_mold_count=10,
                            loaded_at=now,
                            expected_change_at=now + timedelta(hours=1),
                            status=ProductionRun.Status.PLANNED,
                            created_by=self.user,
                        )
                    ]
                )

    def test_expected_change_recalculates_and_allows_explicit_override(self):
        loaded_at = timezone.now().replace(microsecond=0)
        created = self.client.post(
            "/api/production/runs/",
            self._payload(
                station="3",
                order_no="EXPECTED-001",
                loaded_at=loaded_at.isoformat(),
                estimated_hours="1.00",
            ),
            format="json",
        )
        self.assertEqual(created.status_code, 201, created.content)
        run_id = created.json()["id"]

        hours_changed = self.client.patch(
            f"/api/production/runs/{run_id}/",
            {"estimated_hours": "2.00"},
            format="json",
        )
        self.assertEqual(hours_changed.status_code, 200, hours_changed.content)
        self.assertEqual(
            parse_datetime(hours_changed.json()["expected_change_at"]),
            loaded_at + timedelta(hours=2),
        )

        new_loaded_at = loaded_at + timedelta(hours=1)
        load_changed = self.client.patch(
            f"/api/production/runs/{run_id}/",
            {"loaded_at": new_loaded_at.isoformat()},
            format="json",
        )
        self.assertEqual(load_changed.status_code, 200, load_changed.content)
        self.assertEqual(
            parse_datetime(load_changed.json()["expected_change_at"]),
            new_loaded_at + timedelta(hours=2),
        )

        manual_change = new_loaded_at + timedelta(hours=5)
        overridden = self.client.patch(
            f"/api/production/runs/{run_id}/",
            {
                "estimated_hours": "3.00",
                "expected_change_at": manual_change.isoformat(),
            },
            format="json",
        )
        self.assertEqual(overridden.status_code, 200, overridden.content)
        self.assertEqual(
            parse_datetime(overridden.json()["expected_change_at"]), manual_change
        )

    def test_daily_log_requires_operator_and_settlement_requires_balance(self):
        created = self.client.post(
            "/api/production/runs/",
            self._payload(station="4", order_no="LOG-BALANCE"),
            format="json",
        )
        self.assertEqual(created.status_code, 201, created.content)
        run_id = created.json()["id"]
        logged = self.client.post(
            f"/api/production/runs/{run_id}/daily-logs/",
            {
                "date": timezone.localdate().isoformat(),
                "operator": "  张三  ",
                "produced_mold_count": 10,
            },
            format="json",
        )
        self.assertEqual(logged.status_code, 201, logged.content)
        self.assertEqual(logged.json()["daily_logs"][0]["operator"], "张三")
        log_id = logged.json()["daily_logs"][0]["id"]

        corrected = self.client.patch(
            f"/api/production/runs/{run_id}/daily-logs/{log_id}/",
            {"notes": "当班记录"},
            format="json",
        )
        self.assertEqual(corrected.status_code, 200, corrected.content)
        self.assertEqual(corrected.json()["daily_logs"][0]["notes"], "当班记录")

        rejected = self.client.post(
            f"/api/production/runs/{run_id}/daily-logs/",
            {
                "date": (timezone.localdate() - timedelta(days=1)).isoformat(),
                "produced_mold_count": 1,
            },
            format="json",
        )
        self.assertEqual(rejected.status_code, 400, rejected.content)

        run = ProductionRun.objects.get(pk=run_id)
        with self.assertRaises(ValidationError):
            ProductionDailyLog.objects.create(
                run=run,
                production_date=timezone.localdate() - timedelta(days=2),
                operator=" ",
                produced_mold_count=1,
            )

        completed = self.client.post(
            f"/api/production/runs/{run_id}/complete/", {}, format="json"
        )
        self.assertEqual(completed.status_code, 200, completed.content)
        bad_settlement = self.client.post(
            f"/api/production/runs/{run_id}/settlement/",
            {
                "actual_good_quantity": 59,
                "actual_defective_quantity": 0,
                "total_material_kg": "0.000",
                "labor_cost": "0.00",
                "energy_cost": "0.00",
                "other_cost": "0.00",
            },
            format="json",
        )
        self.assertEqual(bad_settlement.status_code, 400, bad_settlement.content)

    def test_complete_is_idempotent_and_delete_is_disabled(self):
        created = self.client.post(
            "/api/production/runs/",
            self._payload(station="3", order_no="COMPLETE-ONCE"),
            format="json",
        )
        self.assertEqual(created.status_code, 201, created.content)
        run_id = created.json()["id"]
        first_unloaded = parse_datetime(created.json()["loaded_at"]) + timedelta(hours=1)
        first = self.client.post(
            f"/api/production/runs/{run_id}/complete/",
            {"unloaded_at": first_unloaded.isoformat()},
            format="json",
        )
        self.assertEqual(first.status_code, 200, first.content)

        second = self.client.post(
            f"/api/production/runs/{run_id}/complete/",
            {"unloaded_at": (first_unloaded + timedelta(hours=2)).isoformat()},
            format="json",
        )
        self.assertEqual(second.status_code, 200, second.content)
        self.assertEqual(
            parse_datetime(second.json()["unloaded_at"]), first_unloaded
        )
        deleted = self.client.delete(f"/api/production/runs/{run_id}/")
        self.assertEqual(deleted.status_code, 405)
        self.assertTrue(ProductionRun.objects.filter(pk=run_id).exists())

    def test_station_time_overlap_is_rejected_but_adjacent_runs_are_allowed(self):
        start = timezone.now().replace(microsecond=0) - timedelta(hours=4)
        end = start + timedelta(hours=2)
        first = self.client.post(
            "/api/production/runs/",
            self._payload(
                station="4",
                order_no="PERIOD-001",
                status=ProductionRun.Status.COMPLETED,
                loaded_at=start.isoformat(),
                unloaded_at=end.isoformat(),
            ),
            format="json",
        )
        self.assertEqual(first.status_code, 201, first.content)
        overlapping = self.client.post(
            "/api/production/runs/",
            self._payload(
                station="4",
                order_no="PERIOD-002",
                status=ProductionRun.Status.COMPLETED,
                loaded_at=(start + timedelta(hours=1)).isoformat(),
                unloaded_at=(end + timedelta(hours=1)).isoformat(),
            ),
            format="json",
        )
        self.assertEqual(overlapping.status_code, 400, overlapping.content)
        adjacent = self.client.post(
            "/api/production/runs/",
            self._payload(
                station="4",
                order_no="PERIOD-003",
                status=ProductionRun.Status.COMPLETED,
                loaded_at=end.isoformat(),
                unloaded_at=(end + timedelta(hours=1)).isoformat(),
            ),
            format="json",
        )
        self.assertEqual(adjacent.status_code, 201, adjacent.content)

    def test_date_filters_use_production_overlap_and_ignore_historical_created_today(self):
        today = timezone.localdate()
        current_tz = timezone.get_current_timezone()
        today_start = timezone.make_aware(
            datetime.combine(today, time.min),
            current_tz,
        )
        ProductionRun.objects.create(
            station=ProductionStation.objects.get(code="5"),
            order_no="OLD-IMPORTED-TODAY",
            specification="历史导入",
            order_quantity=60,
            cavities=6,
            planned_mold_count=10,
            loaded_at=today_start - timedelta(days=10),
            unloaded_at=today_start - timedelta(days=10) + timedelta(hours=1),
            status=ProductionRun.Status.COMPLETED,
            created_by=self.user,
        )
        ProductionRun.objects.create(
            station=ProductionStation.objects.get(code="6"),
            order_no="CROSS-DAY",
            specification="跨日生产",
            order_quantity=60,
            cavities=6,
            planned_mold_count=10,
            loaded_at=today_start - timedelta(hours=1),
            unloaded_at=today_start + timedelta(days=1, hours=1),
            status=ProductionRun.Status.COMPLETED,
            created_by=self.user,
        )
        params = {"date_from": today.isoformat(), "date_to": today.isoformat()}
        listed = self.client.get("/api/production/runs/", params)
        self.assertEqual(listed.status_code, 200, listed.content)
        order_numbers = [item["order_no"] for item in listed.json()["results"]]
        self.assertEqual(order_numbers, ["CROSS-DAY"])

        summary = self.client.get("/api/production/summary/", params)
        self.assertEqual(summary.status_code, 200, summary.content)
        self.assertEqual(summary.json()["run_count"], 1)
        self.assertEqual(summary.json()["planned_quantity"], 60)

    def test_running_mold_must_be_on_the_station_machine_but_history_is_allowed(self):
        mold = self.create_mold(asset_code="MOLD-MISMATCH", machine_code="2")
        running = self.client.post(
            "/api/production/runs/",
            self._payload(
                station="1",
                order_no="MOLD-RUNNING",
                mold_id=mold.pk,
            ),
            format="json",
        )
        self.assertEqual(running.status_code, 400, running.content)
        self.assertIn("mold_id", running.json())

        end = timezone.now().replace(microsecond=0) - timedelta(days=1)
        historical = self.client.post(
            "/api/production/runs/",
            self._payload(
                station="1",
                order_no="MOLD-HISTORY",
                mold_id=mold.pk,
                status=ProductionRun.Status.COMPLETED,
                loaded_at=(end - timedelta(hours=1)).isoformat(),
                unloaded_at=end.isoformat(),
            ),
            format="json",
        )
        self.assertEqual(historical.status_code, 201, historical.content)

    def test_daily_logs_follow_run_lifecycle_rules(self):
        planned = self.client.post(
            "/api/production/runs/",
            self._payload(
                station="5",
                order_no="PLANNED-NO-LOG",
                loaded_at=None,
            ),
            format="json",
        )
        self.assertEqual(planned.status_code, 201, planned.content)
        planned_run = ProductionRun.objects.get(pk=planned.json()["id"])
        blocked = self.client.post(
            f"/api/production/runs/{planned_run.pk}/daily-logs/",
            {
                "date": timezone.localdate().isoformat(),
                "operator": "张三",
                "produced_mold_count": 1,
            },
            format="json",
        )
        self.assertEqual(blocked.status_code, 400, blocked.content)
        with self.assertRaises(ValidationError):
            ProductionDailyLog.objects.create(
                run=planned_run,
                production_date=timezone.localdate(),
                operator="张三",
                produced_mold_count=1,
            )

        running = self.client.post(
            "/api/production/runs/",
            self._payload(station="6", order_no="CANCELLED-HISTORY"),
            format="json",
        )
        self.assertEqual(running.status_code, 201, running.content)
        run_id = running.json()["id"]
        logged = self.client.post(
            f"/api/production/runs/{run_id}/daily-logs/",
            {
                "date": timezone.localdate().isoformat(),
                "operator": "张三",
                "produced_mold_count": 10,
            },
            format="json",
        )
        self.assertEqual(logged.status_code, 201, logged.content)
        log_id = logged.json()["daily_logs"][0]["id"]
        unloaded_at = parse_datetime(running.json()["loaded_at"]) + timedelta(hours=1)
        cancelled = self.client.patch(
            f"/api/production/runs/{run_id}/",
            {
                "status": ProductionRun.Status.CANCELLED,
                "unloaded_at": unloaded_at.isoformat(),
            },
            format="json",
        )
        self.assertEqual(cancelled.status_code, 200, cancelled.content)

        corrected = self.client.patch(
            f"/api/production/runs/{run_id}/daily-logs/{log_id}/",
            {"notes": "取消后修正备注"},
            format="json",
        )
        self.assertEqual(corrected.status_code, 200, corrected.content)
        self.assertEqual(
            corrected.json()["daily_logs"][0]["notes"], "取消后修正备注"
        )
        new_log = self.client.post(
            f"/api/production/runs/{run_id}/daily-logs/",
            {
                "date": (timezone.localdate() - timedelta(days=1)).isoformat(),
                "operator": "张三",
                "produced_mold_count": 1,
            },
            format="json",
        )
        self.assertEqual(new_log.status_code, 400, new_log.content)

    def test_daily_log_date_must_stay_inside_production_window(self):
        today = timezone.localdate()
        current_tz = timezone.get_current_timezone()
        running_loaded = timezone.make_aware(
            datetime.combine(today, time(hour=8)), current_tz
        )
        running = self.client.post(
            "/api/production/runs/",
            self._payload(
                station="6",
                order_no="RUNNING-DATE-WINDOW",
                loaded_at=running_loaded.isoformat(),
            ),
            format="json",
        )
        self.assertEqual(running.status_code, 201, running.content)
        run_id = running.json()["id"]
        future = self.client.post(
            f"/api/production/runs/{run_id}/daily-logs/",
            {
                "date": (today + timedelta(days=1)).isoformat(),
                "operator": "张三",
                "produced_mold_count": 1,
            },
            format="json",
        )
        self.assertEqual(future.status_code, 400, future.content)
        before_load = self.client.post(
            f"/api/production/runs/{run_id}/daily-logs/",
            {
                "date": (today - timedelta(days=1)).isoformat(),
                "operator": "张三",
                "produced_mold_count": 1,
            },
            format="json",
        )
        self.assertEqual(before_load.status_code, 400, before_load.content)

        loaded_date = today - timedelta(days=3)
        unloaded_date = today - timedelta(days=2)
        completed = self.client.post(
            "/api/production/runs/",
            self._payload(
                station="6",
                order_no="COMPLETED-DATE-WINDOW",
                status=ProductionRun.Status.COMPLETED,
                loaded_at=timezone.make_aware(
                    datetime.combine(loaded_date, time(hour=8)), current_tz
                ).isoformat(),
                unloaded_at=timezone.make_aware(
                    datetime.combine(unloaded_date, time(hour=18)), current_tz
                ).isoformat(),
            ),
            format="json",
        )
        self.assertEqual(completed.status_code, 201, completed.content)
        completed_id = completed.json()["id"]
        too_early = self.client.post(
            f"/api/production/runs/{completed_id}/daily-logs/",
            {
                "date": (loaded_date - timedelta(days=1)).isoformat(),
                "operator": "张三",
                "produced_mold_count": 1,
            },
            format="json",
        )
        self.assertEqual(too_early.status_code, 400, too_early.content)
        too_late = self.client.post(
            f"/api/production/runs/{completed_id}/daily-logs/",
            {
                "date": (unloaded_date + timedelta(days=1)).isoformat(),
                "operator": "张三",
                "produced_mold_count": 1,
            },
            format="json",
        )
        self.assertEqual(too_late.status_code, 400, too_late.content)
        valid = self.client.post(
            f"/api/production/runs/{completed_id}/daily-logs/",
            {
                "date": unloaded_date.isoformat(),
                "operator": "张三",
                "produced_mold_count": 1,
            },
            format="json",
        )
        self.assertEqual(valid.status_code, 201, valid.content)

        completed_run = ProductionRun.objects.get(pk=completed_id)
        with self.assertRaises(ValidationError):
            ProductionDailyLog.objects.create(
                run=completed_run,
                production_date=unloaded_date + timedelta(days=2),
                operator="张三",
                produced_mold_count=1,
            )
