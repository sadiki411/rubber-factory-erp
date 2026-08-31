from datetime import datetime, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from analytics.models import ManualFinancialEntry, ManualPerformanceEntry
from production.models import ProductionDailyLog, ProductionRun, ProductionStation
from production.services import seed_default_stations
from quality.models import (
    QualityEmployee,
    QualityOrder,
    QualityReturnAllocation,
    QualityReworkAttempt,
    QualityReworkCase,
    QualityShipment,
    QualityShipmentBatch,
    QualityShipmentLine,
    QualityShipmentOrderAllocation,
    ReturnRework,
)


class AnalyticsApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="analytics-user", password="analytics-password"
        )
        seed_default_stations()
        cls.stations = list(
            ProductionStation.objects.select_related("machine").order_by("code")
        )
        cls.inspector = QualityEmployee.objects.create(
            employee_no="QC-A01",
            name="王品检",
            team="品检组",
            role=QualityEmployee.Role.INSPECTOR,
        )
        cls.reworker = QualityEmployee.objects.create(
            employee_no="RW-A01",
            name="李返工",
            team="返工组",
            role=QualityEmployee.Role.REWORKER,
        )
        cls.order = QualityOrder.objects.create(
            order_no="ORD-ANALYTICS-001",
            product_name="密封圈",
            specification="20x30",
            material="NBR",
            order_quantity=1000,
            order_date=timezone.localdate(),
            created_by=cls.user,
        )

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def create_automatic_records(self, day):
        tz = timezone.get_current_timezone()
        loaded_at = timezone.make_aware(datetime.combine(day, datetime.min.time()), tz) + timedelta(hours=8)
        run = ProductionRun.objects.create(
            station=self.stations[0],
            order=self.order,
            order_no=self.order.order_no,
            specification=self.order.specification,
            material=self.order.material,
            order_quantity=20,
            cavities=2,
            planned_mold_count=10,
            curing_seconds=360,
            estimated_hours=1,
            loaded_at=loaded_at,
            unloaded_at=loaded_at + timedelta(hours=2),
            status=ProductionRun.Status.COMPLETED,
            operator="张生产",
            unit_price=Decimal("10"),
            material_unit_price=Decimal("5"),
            created_by=self.user,
        )
        ProductionDailyLog.objects.create(
            run=run,
            production_date=day,
            operator="张生产",
            produced_mold_count=10,
        )
        run.actual_good_quantity = 18
        run.actual_defective_quantity = 2
        run.total_material_kg = Decimal("2")
        run.labor_cost = Decimal("10")
        run.energy_cost = Decimal("5")
        run.other_cost = Decimal("5")
        run.settled_at = loaded_at + timedelta(hours=3)
        run.settled_by = self.user
        run.save()

        ProductionRun.objects.create(
            station=self.stations[1],
            order_no="ORD-UNSETTLED-001",
            specification="未结算",
            material="NBR",
            order_quantity=10,
            cavities=1,
            planned_mold_count=10,
            curing_seconds=60,
            estimated_hours=1,
            loaded_at=loaded_at,
            unloaded_at=loaded_at + timedelta(hours=1),
            status=ProductionRun.Status.COMPLETED,
            created_by=self.user,
        )

        shipment = QualityShipment.objects.create(
            shipment_no="SHP-ANALYTICS-001",
            shipment_date=day,
            order=self.order,
            inspector=self.inspector,
            inspection_quantity=100,
            qualified_quantity=90,
            defective_quantity=10,
            shipped_quantity=80,
            created_by=self.user,
        )
        ReturnRework.objects.create(
            shipment=shipment,
            rework_date=day,
            reason_category=ReturnRework.ReasonCategory.APPEARANCE,
            responsible_inspector=self.inspector,
            rework_employee=self.reworker,
            returned_quantity=10,
            reworked_quantity=8,
            recovered_quantity=7,
            scrap_quantity=1,
            work_hours=Decimal("1.5"),
            created_by=self.user,
        )

    def create_manual_records(self, day):
        ManualPerformanceEntry.objects.create(
            entry_date=day,
            entry_type=ManualPerformanceEntry.EntryType.PRODUCTION,
            staff_name="赵生产",
            order_no=self.order.order_no,
            machine=self.stations[0].machine,
            produced_mold_count=5,
            production_hours=Decimal("2"),
            created_by=self.user,
        )
        ManualPerformanceEntry.objects.create(
            entry_date=day,
            entry_type=ManualPerformanceEntry.EntryType.QUALITY,
            quality_employee=self.inspector,
            order_no=self.order.order_no,
            inspection_quantity=50,
            qualified_quantity=45,
            defective_quantity=5,
            shipped_quantity=40,
            created_by=self.user,
        )
        ManualPerformanceEntry.objects.create(
            entry_date=day,
            entry_type=ManualPerformanceEntry.EntryType.REWORK,
            quality_employee=self.reworker,
            order_no=self.order.order_no,
            returned_quantity=5,
            reworked_quantity=5,
            recovered_quantity=4,
            scrap_quantity=1,
            rework_hours=Decimal("0.5"),
            created_by=self.user,
        )
        ManualFinancialEntry.objects.create(
            occurred_on=day,
            direction=ManualFinancialEntry.Direction.INCOME,
            category=ManualFinancialEntry.Category.SALES,
            amount=Decimal("100"),
            order_no=self.order.order_no,
            description="补录销售收入",
            created_by=self.user,
        )
        ManualFinancialEntry.objects.create(
            occurred_on=day,
            direction=ManualFinancialEntry.Direction.EXPENSE,
            category=ManualFinancialEntry.Category.MATERIAL,
            amount=Decimal("20"),
            order_no=self.order.order_no,
            description="补录材料成本",
            created_by=self.user,
        )

    def test_dashboard_merges_automatic_and_manual_sources_with_clear_basis(self):
        # Use a completed calendar day. The dashboard intentionally caps machine
        # time at ``timezone.now()`` so a same-day fixture with 08:00-10:00 runs
        # would produce a clock-dependent result when the suite runs before 10:00.
        day = timezone.localdate() - timedelta(days=1)
        self.create_automatic_records(day)
        self.create_manual_records(day)

        response = self.client.get(
            "/api/analytics/dashboard/",
            {"date_from": day.isoformat(), "date_to": day.isoformat()},
        )
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()

        self.assertEqual(payload["sources"]["production"], {
            "automatic": 1,
            "manual": 1,
            "total": 2,
            "automatic_settled_runs": 1,
        })
        self.assertEqual(payload["sources"]["finance"], {
            "automatic": 1,
            "manual": 2,
            "total": 3,
        })
        self.assertEqual(payload["production"]["automatic"]["produced_mold_count"], 10)
        self.assertEqual(payload["production"]["manual"]["produced_mold_count"], 5)
        self.assertEqual(payload["production"]["total"]["produced_mold_count"], 15)
        self.assertEqual(payload["production"]["automatic"]["automatic_equivalent_hours"], "1.00")
        self.assertEqual(payload["production"]["automatic"]["automatic_actual_machine_hours"], "3.00")
        self.assertEqual(payload["production"]["automatic"]["efficiency_percent"], "33.33")
        self.assertEqual(payload["production"]["run_count"], 2)
        self.assertEqual(payload["production"]["unsettled_completed_run_count"], 1)

        self.assertEqual(payload["finance"]["automatic"]["revenue"], "180.00")
        self.assertEqual(payload["finance"]["automatic"]["total_cost"], "30.00")
        self.assertEqual(payload["finance"]["manual"]["profit"], "80.00")
        self.assertEqual(payload["finance"]["total"]["profit"], "230.00")

        quality = payload["quality"]["total"]
        self.assertEqual(quality["inspection_quantity"], 150)
        self.assertEqual(quality["qualified_quantity"], 135)
        self.assertEqual(quality["defective_quantity"], 15)
        self.assertEqual(quality["shipped_quantity"], 120)
        self.assertEqual(quality["returned_quantity"], 15)
        self.assertEqual(quality["first_pass_rate"], "90.00")
        self.assertEqual(quality["return_rate"], "12.50")

        self.assertEqual(payload["data_basis"]["manual_finance_date"], "ManualFinancialEntry.occurred_on")
        self.assertIn("按order_id归集", payload["data_basis"]["order_link"])
        self.assertEqual(len(payload["manual_entries"]), 3)
        self.assertEqual(len(payload["manual_financial_entries"]), 2)
        linked_order = next(
            item
            for item in payload["order_performance"]
            if item["order_id"] == self.order.pk
        )
        self.assertEqual(linked_order["row_key"], f"order:{self.order.pk}")
        self.assertEqual(linked_order["link_type"], "ORDER")
        self.assertEqual(linked_order["produced_mold_count"], 10)
        self.assertEqual(linked_order["inspection_quantity"], 100)
        self.assertEqual(linked_order["profit"], "150.00")

        legacy_order = next(
            item
            for item in payload["order_performance"]
            if item["link_type"] == "LEGACY"
            and item["order_no"] == self.order.order_no
        )
        self.assertEqual(legacy_order["row_key"], f"legacy:{self.order.order_no}")
        self.assertIsNone(legacy_order["order_id"])
        self.assertEqual(legacy_order["produced_mold_count"], 5)
        self.assertEqual(legacy_order["inspection_quantity"], 50)
        self.assertEqual(legacy_order["profit"], "80.00")

    def test_same_order_number_with_distinct_order_ids_is_not_merged(self):
        day = timezone.localdate()
        self.create_automatic_records(day)
        second_order = QualityOrder.objects.create(
            order_no=self.order.order_no,
            item_no="002",
            product_name="油封",
            specification="40x50",
            material="FKM",
            order_quantity=500,
            order_date=day,
            created_by=self.user,
        )
        QualityShipment.objects.create(
            shipment_no="SHP-ANALYTICS-SECOND",
            shipment_date=day,
            order=second_order,
            inspector=self.inspector,
            inspection_quantity=30,
            qualified_quantity=30,
            defective_quantity=0,
            shipped_quantity=30,
            created_by=self.user,
        )

        payload = self.client.get(
            "/api/analytics/dashboard/",
            {"date_from": day.isoformat(), "date_to": day.isoformat()},
        ).json()
        rows = [
            item
            for item in payload["order_performance"]
            if item["order_no"] == self.order.order_no
            and item["link_type"] == "ORDER"
        ]
        self.assertEqual(len(rows), 2)
        self.assertEqual(
            {item["row_key"] for item in rows},
            {f"order:{self.order.pk}", f"order:{second_order.pk}"},
        )
        self.assertEqual(
            {item["order_id"] for item in rows},
            {self.order.pk, second_order.pk},
        )

    def test_dashboard_includes_direct_weighted_order_shipments(self):
        day = timezone.localdate()
        batch = QualityShipmentBatch.objects.create(
            shipment_no="WEIGHTED-DIRECT-ANALYTICS",
            shipment_date=day,
            order=self.order,
            unit_weight_g=Decimal("2"),
            status=QualityShipmentBatch.Status.CONFIRMED,
            created_by=self.user,
        )
        QualityShipmentLine.objects.create(
            batch=batch,
            order=self.order,
            net_weight_kg=Decimal("1.000"),
            piece_quantity=500,
            unit_weight_g_snapshot=Decimal("2"),
        )
        response = self.client.get(
            "/api/analytics/dashboard/",
            {"date_from": day.isoformat(), "date_to": day.isoformat()},
        )
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertEqual(payload["quality"]["automatic"]["shipped_quantity"], 500)
        linked = next(
            item for item in payload["order_performance"]
            if item["row_key"] == f"order:{self.order.pk}"
        )
        self.assertEqual(linked["shipped_quantity"], 500)

    def test_cross_order_weighted_allocations_split_orders_without_double_counting(self):
        day = timezone.localdate()
        target = QualityOrder.objects.create(
            order_no="ORD-ANALYTICS-ALLOCATION-TARGET",
            product_name=self.order.product_name,
            specification=self.order.specification,
            material=self.order.material,
            order_quantity=500,
            order_date=day,
            created_by=self.user,
        )
        second_inspector = QualityEmployee.objects.create(
            employee_no="QC-A02",
            name="赵品检",
            team="品检组",
            role=QualityEmployee.Role.BOTH,
        )
        batch = QualityShipmentBatch.objects.create(
            shipment_no="WEIGHTED-CROSS-ORDER-ANALYTICS",
            shipment_date=day,
            order=self.order,
            inspector=self.inspector,
            unit_weight_g=Decimal("1"),
            single_batch_net_weight_kg=Decimal("0.300"),
            product_batch_count=1,
            pieces_per_batch=300,
            status=QualityShipmentBatch.Status.CONFIRMED,
            created_by=self.user,
        )
        batch.inspectors.set([self.inspector, second_inspector])
        line = QualityShipmentLine.objects.create(
            batch=batch,
            order=self.order,
            net_weight_kg=Decimal("0.300"),
            piece_quantity=300,
            unit_weight_g_snapshot=Decimal("1"),
        )
        source_first = QualityShipmentOrderAllocation.objects.create(
            shipment_line=line,
            order=self.order,
            sequence=1,
            piece_start=0,
            piece_end=100,
            piece_quantity=100,
            net_weight_kg=Decimal("0.100"),
        )
        target_share = QualityShipmentOrderAllocation.objects.create(
            shipment_line=line,
            order=target,
            sequence=2,
            piece_start=100,
            piece_end=250,
            piece_quantity=150,
            net_weight_kg=Decimal("0.150"),
        )
        QualityShipmentOrderAllocation.objects.create(
            shipment_line=line,
            order=self.order,
            sequence=3,
            piece_start=250,
            piece_end=300,
            piece_quantity=50,
            net_weight_kg=Decimal("0.050"),
            is_overflow=True,
        )
        case = QualityReworkCase.objects.create(
            origin=QualityReworkCase.Origin.CUSTOMER_RETURN,
            shipment_batch=batch,
            shipment_line=line,
            shipment_unit_no=1,
            opened_on=day,
            reason_category=ReturnRework.ReasonCategory.STICKING,
            responsible_inspector=self.inspector,
            affected_quantity=100,
            affected_weight_kg=Decimal("0.100"),
            created_by=self.user,
        )
        QualityReturnAllocation.objects.create(
            case=case,
            shipment_line=line,
            shipment_order_allocation=source_first,
            piece_quantity=40,
            net_weight_kg=Decimal("0.040"),
        )
        QualityReturnAllocation.objects.create(
            case=case,
            shipment_line=line,
            shipment_order_allocation=target_share,
            piece_quantity=60,
            net_weight_kg=Decimal("0.060"),
        )
        QualityReworkAttempt.objects.create(
            case=case,
            attempt_date=day,
            rework_employee=self.reworker,
            input_quantity=100,
            reworked_quantity=100,
            recovered_quantity=80,
            scrap_quantity=20,
            input_weight_kg=Decimal("0.100"),
            reworked_weight_kg=Decimal("0.100"),
            recovered_weight_kg=Decimal("0.080"),
            scrap_weight_kg=Decimal("0.020"),
            status=QualityReworkCase.Status.COMPLETED,
            created_by=self.user,
        )

        response = self.client.get(
            "/api/analytics/dashboard/",
            {"date_from": day.isoformat(), "date_to": day.isoformat()},
        )
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        automatic = payload["quality"]["automatic"]
        self.assertEqual(automatic["shipped_quantity"], 300)
        self.assertEqual(automatic["returned_quantity"], 100)
        self.assertEqual(automatic["reworked_quantity"], 100)
        self.assertEqual(payload["daily_trend"][0]["shipped_quantity"], 300)
        self.assertEqual(payload["quality"]["shipment_count"], 1)
        self.assertEqual(payload["sources"]["quality"]["automatic"], 1)

        employees = {
            row["employee_no"]: row
            for row in payload["quality_employee_performance"]
        }
        self.assertEqual(employees[self.inspector.employee_no]["shipped_quantity"], 150)
        self.assertEqual(employees[second_inspector.employee_no]["shipped_quantity"], 150)

        orders = {
            row["order_id"]: row
            for row in payload["order_performance"]
            if row["order_id"] in {self.order.pk, target.pk}
        }
        self.assertEqual(orders[self.order.pk]["shipped_quantity"], 150)
        self.assertEqual(orders[target.pk]["shipped_quantity"], 150)
        self.assertEqual(orders[self.order.pk]["returned_quantity"], 40)
        self.assertEqual(orders[target.pk]["returned_quantity"], 60)
        self.assertEqual(orders[self.order.pk]["reworked_quantity"], 40)
        self.assertEqual(orders[target.pk]["reworked_quantity"], 60)
        self.assertEqual(orders[self.order.pk]["recovered_quantity"], 32)
        self.assertEqual(orders[target.pk]["recovered_quantity"], 48)
        self.assertEqual(orders[self.order.pk]["scrap_quantity"], 8)
        self.assertEqual(orders[target.pk]["scrap_quantity"], 12)

    def test_dashboard_includes_whole_batch_returns_and_r1_r2_r3(self):
        day = timezone.localdate()
        batch = QualityShipmentBatch.objects.create(
            shipment_no="WEIGHTED-RETURN-ANALYTICS",
            shipment_date=day,
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
            opened_on=day,
            reason_category=ReturnRework.ReasonCategory.STICKING,
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
        for index, recovered in enumerate((0, 0, 90), start=1):
            QualityReworkAttempt.objects.create(
                case=case,
                attempt_date=day,
                rework_employee=self.reworker,
                input_quantity=100,
                reworked_quantity=100,
                recovered_quantity=recovered,
                scrap_quantity=10 if index == 3 else 0,
                input_weight_kg=Decimal("0.200"),
                reworked_weight_kg=Decimal("0.200"),
                recovered_weight_kg=(
                    Decimal("0.180") if index == 3 else Decimal("0")
                ),
                scrap_weight_kg=(
                    Decimal("0.020") if index == 3 else Decimal("0")
                ),
                status=(
                    QualityReworkCase.Status.COMPLETED
                    if index == 3
                    else QualityReworkCase.Status.WAITING_REINSPECTION
                ),
                created_by=self.user,
            )

        response = self.client.get(
            "/api/analytics/dashboard/",
            {"date_from": day.isoformat(), "date_to": day.isoformat()},
        )
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        automatic = payload["quality"]["automatic"]
        self.assertEqual(automatic["shipped_quantity"], 300)
        self.assertEqual(automatic["returned_quantity"], 100)
        self.assertEqual(automatic["reworked_quantity"], 300)
        self.assertEqual(automatic["recovered_quantity"], 90)
        self.assertEqual(automatic["scrap_quantity"], 10)
        self.assertEqual(payload["quality"]["rework_count"], 3)
        self.assertEqual(payload["sources"]["rework"]["automatic"], 3)
        self.assertIn(
            "QualityReworkAttempt.attempt_date",
            payload["data_basis"]["rework_date"],
        )

        daily = payload["daily_trend"][0]
        self.assertEqual(daily["returned_quantity"], 100)
        self.assertEqual(daily["reworked_quantity"], 300)
        order = next(
            row
            for row in payload["order_performance"]
            if row["row_key"] == f"order:{self.order.pk}"
        )
        self.assertEqual(order["returned_quantity"], 100)
        self.assertEqual(order["reworked_quantity"], 300)
        employees = {
            row["employee_no"]: row
            for row in payload["quality_employee_performance"]
        }
        self.assertEqual(
            employees[self.inspector.employee_no]["responsible_return_quantity"],
            100,
        )
        self.assertEqual(
            employees[self.reworker.employee_no]["reworked_quantity"], 300
        )
        reason = next(
            row
            for row in payload["defect_reason_breakdown"]
            if row["reason_category"] == ReturnRework.ReasonCategory.STICKING
        )
        self.assertEqual(reason["reason_category_display"], "粘皮")
        self.assertEqual(reason["returned_quantity"], 100)
        self.assertEqual(reason["reworked_quantity"], 300)

    def test_multi_inspector_legacy_shipment_is_split_without_double_counting(self):
        day = timezone.localdate()
        second = QualityEmployee.objects.create(
            employee_no="QC-A02",
            name="赵品检",
            team="品检组",
            role=QualityEmployee.Role.BOTH,
        )
        shipment = QualityShipment.objects.create(
            shipment_no="MULTI-LEGACY-ANALYTICS",
            shipment_date=day,
            order=self.order,
            inspector=self.inspector,
            inspection_quantity=100,
            qualified_quantity=80,
            defective_quantity=20,
            shipped_quantity=80,
            created_by=self.user,
        )
        shipment.inspectors.set([self.inspector, second])
        payload = self.client.get(
            "/api/analytics/dashboard/",
            {"date_from": day.isoformat(), "date_to": day.isoformat()},
        ).json()
        employees = {
            row["employee_no"]: row
            for row in payload["quality_employee_performance"]
        }
        self.assertEqual(employees["QC-A01"]["shipped_quantity"], 40)
        self.assertEqual(employees["QC-A02"]["shipped_quantity"], 40)
        self.assertEqual(payload["quality"]["automatic"]["shipped_quantity"], 80)

    def test_manual_crud_soft_void_restore_and_validation(self):
        day = timezone.localdate()
        payload = {
            "entry_date": day.isoformat(),
            "entry_type": "PRODUCTION",
            "staff_name": "陈生产",
            "machine_id": self.stations[0].machine_id,
            "produced_mold_count": 8,
            "production_hours": "1.50",
        }
        created = self.client.post("/api/analytics/manual-entries/", payload, format="json")
        self.assertEqual(created.status_code, 201, created.content)
        entry_id = created.json()["id"]

        voided = self.client.post(
            f"/api/analytics/manual-entries/{entry_id}/void/",
            {"void_reason": "重复录入"},
            format="json",
        )
        self.assertEqual(voided.status_code, 200, voided.content)
        self.assertIsNotNone(voided.json()["voided_at"])
        self.assertEqual(voided.json()["void_reason"], "重复录入")

        dashboard = self.client.get(
            "/api/analytics/dashboard/",
            {"date_from": day.isoformat(), "date_to": day.isoformat()},
        ).json()
        self.assertEqual(dashboard["sources"]["production"]["manual"], 0)

        restored = self.client.post(
            f"/api/analytics/manual-entries/{entry_id}/restore/", {}, format="json"
        )
        self.assertEqual(restored.status_code, 200, restored.content)
        self.assertIsNone(restored.json()["voided_at"])

        invalid = self.client.post(
            "/api/analytics/financial-entries/",
            {
                "occurred_on": day.isoformat(),
                "direction": "EXPENSE",
                "category": "SALES",
                "amount": "10.00",
                "description": "错误分类",
            },
            format="json",
        )
        self.assertEqual(invalid.status_code, 400, invalid.content)

    def test_zero_denominator_rates_are_null_and_authentication_is_required(self):
        day = timezone.localdate() - timedelta(days=10)
        response = self.client.get(
            "/api/analytics/dashboard/",
            {"date_from": day.isoformat(), "date_to": day.isoformat()},
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIsNone(response.json()["quality"]["total"]["first_pass_rate"])
        self.assertIsNone(response.json()["finance"]["total"]["profit_margin"])

        anonymous = APIClient().get(
            "/api/analytics/dashboard/",
            {"date_from": day.isoformat(), "date_to": day.isoformat()},
        )
        self.assertIn(anonymous.status_code, (401, 403))

    def test_manual_detail_lists_follow_machine_and_group_filters(self):
        day = timezone.localdate()
        station_a = self.stations[0]
        station_b = next(station for station in self.stations if station.group == "B")
        production_a = ManualPerformanceEntry.objects.create(
            entry_date=day,
            entry_type=ManualPerformanceEntry.EntryType.PRODUCTION,
            staff_name="A组生产",
            machine=station_a.machine,
            produced_mold_count=3,
            created_by=self.user,
        )
        production_b = ManualPerformanceEntry.objects.create(
            entry_date=day,
            entry_type=ManualPerformanceEntry.EntryType.PRODUCTION,
            staff_name="B组生产",
            machine=station_b.machine,
            produced_mold_count=4,
            created_by=self.user,
        )
        quality = ManualPerformanceEntry.objects.create(
            entry_date=day,
            entry_type=ManualPerformanceEntry.EntryType.QUALITY,
            quality_employee=self.inspector,
            inspection_quantity=2,
            qualified_quantity=2,
            created_by=self.user,
        )
        finance_a = ManualFinancialEntry.objects.create(
            occurred_on=day,
            direction=ManualFinancialEntry.Direction.EXPENSE,
            category=ManualFinancialEntry.Category.ENERGY,
            amount=Decimal("10"),
            machine=station_a.machine,
            description="A组能耗",
            created_by=self.user,
        )
        finance_b = ManualFinancialEntry.objects.create(
            occurred_on=day,
            direction=ManualFinancialEntry.Direction.EXPENSE,
            category=ManualFinancialEntry.Category.ENERGY,
            amount=Decimal("20"),
            machine=station_b.machine,
            description="B组能耗",
            created_by=self.user,
        )

        manual = self.client.get(
            "/api/analytics/manual-entries/",
            {
                "date_from": day.isoformat(),
                "date_to": day.isoformat(),
                "machine_id": station_a.machine_id,
                "page_size": 1000,
            },
        )
        self.assertEqual(manual.status_code, 200, manual.content)
        manual_ids = {item["id"] for item in manual.json()["results"]}
        self.assertEqual(manual_ids, {production_a.pk, quality.pk})
        self.assertNotIn(production_b.pk, manual_ids)

        financial = self.client.get(
            "/api/analytics/financial-entries/",
            {
                "date_from": day.isoformat(),
                "date_to": day.isoformat(),
                "group": "B",
                "page_size": 1000,
            },
        )
        self.assertEqual(financial.status_code, 200, financial.content)
        financial_ids = {item["id"] for item in financial.json()["results"]}
        self.assertEqual(financial_ids, {finance_b.pk})
        self.assertNotIn(finance_a.pk, financial_ids)
