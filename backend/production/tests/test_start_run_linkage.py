from datetime import datetime, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from molds.models import MoldAsset, MoldModel, MoldMovement
from molds.services import (
    seed_default_racks,
    switch_zone_stacking,
    transition_mold,
)
from production.models import ProductionRun, ProductionStation
from production.services import seed_default_stations


class StartProductionRunLinkageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="start-run-user",
            password="start-run-password",
        )
        seed_default_racks()
        seed_default_stations()

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    @staticmethod
    def slot(rack="J01", level=1, zone="A", position=1, stack=1):
        from molds.models import RackSlot

        return RackSlot.objects.select_related("zone__level__rack").get(
            zone__level__rack__code=rack,
            zone__level__level_no=level,
            zone__code=zone,
            capacity_mode=2,
            position_no=position,
            stack_level=stack,
        )

    def create_stock_mold(
        self,
        asset_code,
        slot,
        *,
        allows_stacking=False,
    ):
        model = MoldModel.objects.create(
            code=f"MODEL-{asset_code}",
            product_name=f"产品 {asset_code}",
        )
        return MoldAsset.objects.create(
            asset_code=asset_code,
            mold_model=model,
            status=MoldAsset.Status.IN_STOCK,
            current_slot=slot,
            allows_stacking=allows_stacking,
        )

    def create_planned_run(self, mold, *, station_code="1", order_no="PLAN-001"):
        return ProductionRun.objects.create(
            station=ProductionStation.objects.get(code=station_code),
            mold=mold,
            order_no=order_no,
            specification="测试产品",
            material="NBR",
            order_quantity=100,
            cavities=2,
            planned_mold_count=50,
            estimated_hours="2.00",
            status=ProductionRun.Status.PLANNED,
            created_by=self.user,
        )

    def board_station(self, code):
        response = self.client.get("/api/production/board/")
        self.assertEqual(response.status_code, 200, response.content)
        stations = [
            station
            for group in response.json()["groups"]
            for station in group["stations"]
        ]
        return response.json(), next(station for station in stations if station["code"] == code)

    def test_start_atomically_mounts_stock_mold_and_is_idempotent(self):
        source = self.slot()
        mold = self.create_stock_mold("START-001", source)
        run = self.create_planned_run(mold)
        loaded_at = timezone.make_aware(datetime(2026, 7, 14, 8, 30))

        started = self.client.post(
            f"/api/production/runs/{run.pk}/start/",
            {"loaded_at": loaded_at.isoformat(), "note": "计划确认上机"},
            format="json",
        )

        self.assertEqual(started.status_code, 200, started.content)
        run.refresh_from_db()
        mold.refresh_from_db()
        self.assertEqual(run.status, ProductionRun.Status.RUNNING)
        self.assertEqual(run.loaded_at, loaded_at)
        self.assertEqual(run.expected_change_at, loaded_at + timedelta(hours=2))
        self.assertEqual(mold.status, MoldAsset.Status.ON_MACHINE)
        self.assertEqual(mold.current_machine_id, run.station.machine_id)
        self.assertIsNone(mold.current_slot_id)
        movement = MoldMovement.objects.get(
            mold=mold,
            action=MoldMovement.Action.LOAD_MACHINE,
        )
        self.assertEqual(movement.from_slot_id, source.pk)
        self.assertEqual(movement.to_machine_id, run.station.machine_id)
        self.assertEqual(movement.operator_id, self.user.pk)
        self.assertEqual(movement.note, "计划确认上机")

        board = self.client.get("/api/production/board/")
        self.assertEqual(board.status_code, 200, board.content)
        station = board.json()["groups"][0]["stations"][0]
        self.assertEqual(station["run"]["id"], run.pk)
        self.assertEqual(station["run"]["status"], ProductionRun.Status.RUNNING)
        self.assertEqual(station["mounted_molds"][0]["asset_code"], mold.asset_code)

        repeated = self.client.post(
            f"/api/production/runs/{run.pk}/start/",
            {"loaded_at": (loaded_at + timedelta(hours=1)).isoformat()},
            format="json",
        )
        self.assertEqual(repeated.status_code, 200, repeated.content)
        run.refresh_from_db()
        self.assertEqual(run.loaded_at, loaded_at)
        self.assertEqual(
            MoldMovement.objects.filter(
                mold=mold,
                action=MoldMovement.Action.LOAD_MACHINE,
            ).count(),
            1,
        )

    def test_stacking_warning_rolls_back_then_confirmed_start_succeeds(self):
        lower_slot = self.slot(rack="J05", zone="A", position=1, stack=1)
        upper_slot = self.slot(rack="J05", zone="A", position=1, stack=2)
        switch_zone_stacking(lower_slot.zone, True)
        lower = self.create_stock_mold(
            "START-LOWER",
            lower_slot,
            allows_stacking=True,
        )
        upper = self.create_stock_mold("START-UPPER", upper_slot)
        run = self.create_planned_run(lower, order_no="PLAN-STACK")

        warning = self.client.post(
            f"/api/production/runs/{run.pk}/start/",
            {},
            format="json",
        )

        self.assertEqual(warning.status_code, 409, warning.content)
        self.assertTrue(warning.json()["requires_confirmation"])
        run.refresh_from_db()
        lower.refresh_from_db()
        upper.refresh_from_db()
        self.assertEqual(run.status, ProductionRun.Status.PLANNED)
        self.assertIsNone(run.loaded_at)
        self.assertEqual(lower.current_slot_id, lower_slot.pk)
        self.assertEqual(upper.current_slot_id, upper_slot.pk)
        self.assertFalse(MoldMovement.objects.filter(mold=lower).exists())

        confirmed = self.client.post(
            f"/api/production/runs/{run.pk}/start/",
            {"confirm_warnings": True},
            format="json",
        )

        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        run.refresh_from_db()
        lower.refresh_from_db()
        upper.refresh_from_db()
        self.assertEqual(run.status, ProductionRun.Status.RUNNING)
        self.assertEqual(lower.status, MoldAsset.Status.ON_MACHINE)
        self.assertEqual(lower.current_machine_id, run.station.machine_id)
        self.assertEqual(upper.current_slot_id, upper_slot.pk)
        self.assertEqual(MoldMovement.objects.filter(mold=lower).count(), 1)

    def test_planned_mold_already_on_target_machine_starts_without_duplicate_movement(self):
        source = self.slot(level=2, position=2)
        mold = self.create_stock_mold("START-MOUNTED", source)
        run = self.create_planned_run(mold, order_no="PLAN-MOUNTED")
        transition_mold(
            mold,
            MoldMovement.Action.LOAD_MACHINE,
            self.user,
            machine=run.station.machine,
            note="台账先行上机",
        )
        self.assertEqual(MoldMovement.objects.filter(mold=mold).count(), 1)

        response = self.client.post(
            f"/api/production/runs/{run.pk}/start/",
            {},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.content)
        run.refresh_from_db()
        mold.refresh_from_db()
        self.assertEqual(run.status, ProductionRun.Status.RUNNING)
        self.assertIsNotNone(run.loaded_at)
        self.assertEqual(mold.current_machine_id, run.station.machine_id)
        self.assertEqual(MoldMovement.objects.filter(mold=mold).count(), 1)

    def test_unconfirmed_plan_is_not_counted_as_physical_machine_occupancy(self):
        mold = self.create_stock_mold(
            "START-PLANNED",
            self.slot(level=3, position=2),
        )
        run = self.create_planned_run(mold, order_no="PLAN-NOT-OCCUPIED")

        payload, station = self.board_station(run.station.code)

        self.assertEqual(station["reminder_status"], "PLANNED")
        self.assertEqual(station["run"]["id"], run.pk)
        self.assertEqual(station["mounted_molds"], [])
        self.assertEqual(payload["counts"]["planned"], 1)
        self.assertEqual(payload["counts"]["occupied"], 0)
        self.assertEqual(payload["counts"]["mounted"], 0)

    def test_completed_mold_release_actions_remove_machine_occupancy_from_board(self):
        cases = (
            ("putaway", "1", 4, 1, MoldAsset.Status.IN_STOCK),
            ("send-out", "2", 5, 2, MoldAsset.Status.OUTSOURCED),
        )
        for action, station_code, source_level, target_position, expected_status in cases:
            with self.subTest(action=action):
                mold = self.create_stock_mold(
                    f"START-RELEASE-{station_code}",
                    self.slot(level=source_level, position=1),
                )
                run = self.create_planned_run(
                    mold,
                    station_code=station_code,
                    order_no=f"PLAN-RELEASE-{station_code}",
                )
                started = self.client.post(
                    f"/api/production/runs/{run.pk}/start/",
                    {},
                    format="json",
                )
                self.assertEqual(started.status_code, 200, started.content)
                completed = self.client.post(
                    f"/api/production/runs/{run.pk}/complete/",
                    {},
                    format="json",
                )
                self.assertEqual(completed.status_code, 200, completed.content)

                body = {}
                if action == "putaway":
                    body["slot_id"] = self.slot(
                        level=6,
                        position=target_position,
                    ).pk
                released = self.client.post(
                    f"/api/molds/{mold.pk}/actions/{action}/",
                    body,
                    format="json",
                )
                self.assertEqual(released.status_code, 200, released.content)

                mold.refresh_from_db()
                self.assertEqual(mold.status, expected_status)
                self.assertIsNone(mold.current_machine_id)
                _, station = self.board_station(station_code)
                self.assertEqual(station["reminder_status"], "IDLE")
                self.assertEqual(station["mounted_molds"], [])
                self.assertIsNone(station["run"])

    def test_start_failure_after_mold_transition_rolls_back_everything(self):
        source = self.slot(position=2)
        mold = self.create_stock_mold("START-ROLLBACK", source)
        run = self.create_planned_run(mold, order_no="PLAN-ROLLBACK")

        with patch.object(
            ProductionRun,
            "save",
            side_effect=ValidationError("强制验证失败"),
        ):
            response = self.client.post(
                f"/api/production/runs/{run.pk}/start/",
                {},
                format="json",
            )

        self.assertEqual(response.status_code, 400, response.content)
        run.refresh_from_db()
        mold.refresh_from_db()
        self.assertEqual(run.status, ProductionRun.Status.PLANNED)
        self.assertIsNone(run.loaded_at)
        self.assertEqual(mold.status, MoldAsset.Status.IN_STOCK)
        self.assertEqual(mold.current_slot_id, source.pk)
        self.assertIsNone(mold.current_machine_id)
        self.assertFalse(MoldMovement.objects.filter(mold=mold).exists())

    def test_start_rejects_unlinked_machine_and_invalid_mold_states(self):
        cases = ("unlinked", "customer_returned", "deleted")
        for index, case in enumerate(cases, start=1):
            with self.subTest(case=case):
                source = self.slot(level=index + 1, position=1)
                mold = self.create_stock_mold(f"START-BLOCK-{index}", source)
                run = self.create_planned_run(
                    mold,
                    station_code=str(index),
                    order_no=f"PLAN-BLOCK-{index}",
                )
                if case == "unlinked":
                    ProductionStation.objects.filter(pk=run.station_id).update(
                        machine=None
                    )
                elif case == "customer_returned":
                    MoldAsset.objects.filter(pk=mold.pk).update(
                        status=MoldAsset.Status.OUTSOURCED,
                        current_slot=None,
                        current_machine=None,
                        current_processor=None,
                    )
                else:
                    MoldAsset.objects.filter(pk=mold.pk).update(
                        is_active=False,
                        current_slot=None,
                        current_machine=None,
                        current_processor=None,
                    )

                response = self.client.post(
                    f"/api/production/runs/{run.pk}/start/",
                    {},
                    format="json",
                )

                self.assertEqual(response.status_code, 400, response.content)
                run.refresh_from_db()
                self.assertEqual(run.status, ProductionRun.Status.PLANNED)
                self.assertIsNone(run.loaded_at)
                self.assertFalse(MoldMovement.objects.filter(mold=mold).exists())
