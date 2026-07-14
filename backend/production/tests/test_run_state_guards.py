from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from molds.models import MoldAsset, MoldModel, RackSlot
from molds.services import seed_default_racks
from production.models import ProductionRun, ProductionStation
from production.services import seed_default_stations


class ProductionRunStateGuardApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="run-state-guard-user",
            password="run-state-guard-password",
        )
        seed_default_racks()
        seed_default_stations()

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    @staticmethod
    def create_mold(asset_code, *, machine=None, slot=None):
        model = MoldModel.objects.create(
            code=f"MODEL-{asset_code}",
            product_name=f"产品 {asset_code}",
        )
        return MoldAsset.objects.create(
            asset_code=asset_code,
            mold_model=model,
            status=(
                MoldAsset.Status.ON_MACHINE
                if machine is not None
                else MoldAsset.Status.IN_STOCK
            ),
            current_machine=machine,
            current_slot=slot,
        )

    def create_running_run(self, order_no="STATE-RUNNING", station_code="1"):
        station = ProductionStation.objects.select_related("machine").get(
            code=station_code
        )
        mold = self.create_mold(
            f"MOLD-{order_no}",
            machine=station.machine,
        )
        loaded_at = timezone.now() - timedelta(hours=1)
        return ProductionRun.objects.create(
            station=station,
            mold=mold,
            order_no=order_no,
            specification="状态守卫测试",
            order_quantity=100,
            cavities=2,
            planned_mold_count=50,
            loaded_at=loaded_at,
            status=ProductionRun.Status.RUNNING,
            created_by=self.user,
        )

    def test_running_order_cannot_return_to_planned_through_regular_patch(self):
        run = self.create_running_run()

        response = self.client.patch(
            f"/api/production/runs/{run.pk}/",
            {"status": ProductionRun.Status.PLANNED},
            format="json",
        )

        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("status", response.json())
        run.refresh_from_db()
        self.assertEqual(run.status, ProductionRun.Status.RUNNING)
        self.assertIsNone(run.unloaded_at)

    def test_existing_completion_and_cancellation_patch_paths_remain_compatible(self):
        completed = self.create_running_run(order_no="STATE-PATCH-COMPLETE")

        completed_response = self.client.patch(
            f"/api/production/runs/{completed.pk}/",
            {"unloaded_at": timezone.now().isoformat()},
            format="json",
        )
        self.assertEqual(
            completed_response.status_code,
            200,
            completed_response.content,
        )
        self.assertEqual(
            completed_response.json()["status"],
            ProductionRun.Status.COMPLETED,
        )

        cancelled = self.create_running_run(
            order_no="STATE-PATCH-CANCEL",
            station_code="2",
        )
        cancelled_response = self.client.patch(
            f"/api/production/runs/{cancelled.pk}/",
            {
                "status": ProductionRun.Status.CANCELLED,
                "unloaded_at": timezone.now().isoformat(),
            },
            format="json",
        )
        self.assertEqual(
            cancelled_response.status_code,
            200,
            cancelled_response.content,
        )
        self.assertEqual(
            cancelled_response.json()["status"],
            ProductionRun.Status.CANCELLED,
        )

    def test_terminal_orders_cannot_be_reopened_as_planned_through_patch(self):
        now = timezone.now()
        completed = ProductionRun.objects.create(
            station=ProductionStation.objects.get(code="2"),
            order_no="STATE-COMPLETED",
            specification="历史完成订单",
            order_quantity=10,
            planned_mold_count=5,
            loaded_at=now - timedelta(hours=2),
            unloaded_at=now - timedelta(hours=1),
            status=ProductionRun.Status.COMPLETED,
            created_by=self.user,
        )
        cancelled = ProductionRun.objects.create(
            station=ProductionStation.objects.get(code="3"),
            order_no="STATE-CANCELLED",
            specification="已取消订单",
            order_quantity=10,
            planned_mold_count=5,
            status=ProductionRun.Status.CANCELLED,
            created_by=self.user,
        )

        for run in (completed, cancelled):
            with self.subTest(status=run.status):
                response = self.client.patch(
                    f"/api/production/runs/{run.pk}/",
                    {"status": ProductionRun.Status.PLANNED},
                    format="json",
                )
                self.assertEqual(response.status_code, 400, response.content)
                run.refresh_from_db()
                self.assertNotEqual(run.status, ProductionRun.Status.PLANNED)

    def test_running_and_terminal_orders_cannot_change_station_or_mold(self):
        run = self.create_running_run(order_no="STATE-LOCKED-LINKS")
        other_station = ProductionStation.objects.select_related("machine").get(code="2")
        other_mold = self.create_mold(
            "STATE-OTHER-MOLD",
            machine=other_station.machine,
        )

        station_response = self.client.patch(
            f"/api/production/runs/{run.pk}/",
            {"station_id": other_station.pk},
            format="json",
        )
        self.assertEqual(station_response.status_code, 400, station_response.content)
        self.assertIn("station_id", station_response.json())

        mold_response = self.client.patch(
            f"/api/production/runs/{run.pk}/",
            {"mold_id": other_mold.pk},
            format="json",
        )
        self.assertEqual(mold_response.status_code, 400, mold_response.content)
        self.assertIn("mold_id", mold_response.json())
        run.refresh_from_db()
        self.assertEqual(run.station.code, "1")
        self.assertNotEqual(run.mold_id, other_mold.pk)

    def test_planned_order_can_still_change_station_and_mold(self):
        run = ProductionRun.objects.create(
            station=ProductionStation.objects.get(code="1"),
            order_no="STATE-EDITABLE-PLAN",
            specification="可调整计划",
            order_quantity=10,
            planned_mold_count=5,
            status=ProductionRun.Status.PLANNED,
            created_by=self.user,
        )
        slot = RackSlot.objects.get(
            zone__level__rack__code="J01",
            zone__level__level_no=6,
            zone__code="A",
            capacity_mode=2,
            position_no=2,
            stack_level=1,
        )
        mold = self.create_mold("STATE-PLANNED-MOLD", slot=slot)
        target_station = ProductionStation.objects.get(code="2")

        response = self.client.patch(
            f"/api/production/runs/{run.pk}/",
            {"station_id": target_station.pk, "mold_id": mold.pk},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.content)
        run.refresh_from_db()
        self.assertEqual(run.station_id, target_station.pk)
        self.assertEqual(run.mold_id, mold.pk)
        self.assertEqual(run.status, ProductionRun.Status.PLANNED)

    def test_running_order_requires_a_mold(self):
        station = ProductionStation.objects.get(code="1")
        response = self.client.post(
            "/api/production/runs/",
            {
                "station_id": station.pk,
                "order_no": "STATE-NO-MOLD",
                "specification": "无模具运行",
                "order_quantity": 10,
                "cavities": 2,
                "planned_mold_count": 5,
                "loaded_at": timezone.now().isoformat(),
                "status": ProductionRun.Status.RUNNING,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("mold_id", response.json())
        self.assertFalse(
            ProductionRun.objects.filter(order_no="STATE-NO-MOLD").exists()
        )
