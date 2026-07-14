from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from molds.models import Machine, MoldAsset, MoldModel, MoldMovement, RackSlot
from molds.services import seed_default_racks
from production.models import (
    ProductionRun,
    ProductionStation,
    normalize_production_station_code,
)
from production.services import seed_default_stations, start_production_run


class StartProductionRunServiceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="start-service-user")
        seed_default_racks()
        seed_default_stations()
        self.slot = RackSlot.objects.get(
            zone__level__rack__code="J01",
            zone__level__level_no=1,
            zone__code="A",
            capacity_mode=2,
            position_no=1,
            stack_level=1,
        )
        model = MoldModel.objects.create(
            code="START-ROLLBACK-MODEL",
            product_name="原子回滚测试",
        )
        self.mold = MoldAsset.objects.create(
            asset_code="START-ROLLBACK-001",
            mold_model=model,
            status=MoldAsset.Status.IN_STOCK,
            current_slot=self.slot,
        )
        self.run = ProductionRun.objects.create(
            station=ProductionStation.objects.get(code="1"),
            order_no="START-ROLLBACK-ORDER",
            specification="原子回滚测试",
            mold=self.mold,
            order_quantity=6,
            cavities=6,
            planned_mold_count=1,
            status=ProductionRun.Status.PLANNED,
            created_by=self.user,
        )

    def test_run_save_failure_rolls_back_mold_transition_and_history(self):
        with patch(
            "production.services.ProductionRun.save",
            side_effect=IntegrityError("forced start failure"),
        ):
            with self.assertRaises(IntegrityError):
                start_production_run(self.run, self.user)

        self.run.refresh_from_db()
        self.mold.refresh_from_db()
        self.assertEqual(self.run.status, ProductionRun.Status.PLANNED)
        self.assertIsNone(self.run.loaded_at)
        self.assertEqual(self.mold.status, MoldAsset.Status.IN_STOCK)
        self.assertEqual(self.mold.current_slot_id, self.slot.pk)
        self.assertIsNone(self.mold.current_machine_id)
        self.assertFalse(MoldMovement.objects.filter(mold=self.mold).exists())


class ProductionStationSeedTests(TestCase):
    def test_default_station_seed_is_idempotent(self):
        seed_default_stations()
        seed_default_stations()
        self.assertEqual(ProductionStation.objects.count(), 6)
        self.assertEqual(
            list(
                ProductionStation.objects.values_list(
                    "group", "position_no", "code", "machine__code"
                )
            ),
            [
                ("A", 1, "1", "1"),
                ("A", 2, "2", "2"),
                ("B", 1, "3", "3"),
                ("B", 2, "4", "4"),
                ("C", 1, "5", "5"),
                ("C", 2, "6", "6"),
            ],
        )
        self.assertEqual(Machine.objects.filter(code__in=list("123456")).count(), 6)
        self.assertTrue(
            ProductionStation.objects.filter(code="1", machine__code="1").exists()
        )

    def test_seed_reuses_valid_legacy_rows_and_preserves_custom_stations(self):
        legacy_machine = Machine.objects.create(
            code="A01", name="A组1号机台", is_active=True
        )
        ProductionStation.objects.bulk_create(
            [
                ProductionStation(
                    code="A01",
                    group="A",
                    position_no=1,
                    machine=legacy_machine,
                    is_active=True,
                )
            ]
        )
        custom_machine = Machine.objects.create(
            code="D01", name="D组1号机台", is_active=True
        )
        custom_station = ProductionStation.objects.create(
            code="D01",
            group="D",
            position_no=1,
            machine=custom_machine,
            is_active=True,
        )

        seed_default_stations()

        canonical = ProductionStation.objects.get(group="A", position_no=1)
        self.assertEqual(canonical.code, "1")
        self.assertEqual(canonical.machine_id, legacy_machine.pk)
        legacy_machine.refresh_from_db()
        self.assertEqual(legacy_machine.code, "1")
        custom_station.refresh_from_db()
        custom_machine.refresh_from_db()
        self.assertTrue(custom_station.is_active)
        self.assertTrue(custom_machine.is_active)
        self.assertEqual(custom_station.code, "D01")
        self.assertEqual(custom_station.machine_id, custom_machine.pk)
        self.assertEqual(ProductionStation.objects.count(), 7)

    def test_seed_keeps_referenced_extra_position_active(self):
        machine = Machine.objects.create(
            code="A03", name="A组扩展3号机台", is_active=True
        )
        station = ProductionStation.objects.create(
            code="A03",
            group="A",
            position_no=3,
            machine=machine,
            is_active=True,
        )
        now = timezone.now()
        run = ProductionRun.objects.create(
            station=station,
            order_no="LEGACY-A03-HISTORY",
            specification="历史订单",
            order_quantity=6,
            cavities=6,
            planned_mold_count=1,
            loaded_at=now - timedelta(hours=1),
            unloaded_at=now,
            status=ProductionRun.Status.COMPLETED,
            created_by=get_user_model().objects.create_user(username="history-user"),
        )

        seed_default_stations()

        station.refresh_from_db()
        machine.refresh_from_db()
        run.refresh_from_db()
        self.assertTrue(station.is_active)
        self.assertTrue(machine.is_active)
        self.assertEqual(run.station_id, station.pk)

    def test_only_six_current_and_legacy_alias_codes_are_normalized(self):
        self.assertEqual(normalize_production_station_code("01"), "1")
        self.assertEqual(normalize_production_station_code("B01"), "3")
        self.assertEqual(normalize_production_station_code("c2"), "6")
        self.assertEqual(normalize_production_station_code("A03"), "A03")
        self.assertEqual(normalize_production_station_code("7"), "7")
