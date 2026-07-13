from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from molds.models import Machine
from production.models import (
    ProductionRun,
    ProductionStation,
    normalize_production_station_code,
)
from production.services import seed_default_stations


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

    def test_seed_reuses_valid_legacy_rows_and_prunes_unused_obsolete_stations(self):
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
        obsolete_machine = Machine.objects.create(
            code="A03", name="错误的A组3号机台", is_active=False
        )
        obsolete_station = ProductionStation.objects.create(
            code="A03",
            group="A",
            position_no=3,
            machine=obsolete_machine,
            is_active=False,
        )

        seed_default_stations()

        canonical = ProductionStation.objects.get(group="A", position_no=1)
        self.assertEqual(canonical.code, "1")
        self.assertEqual(canonical.machine_id, legacy_machine.pk)
        legacy_machine.refresh_from_db()
        self.assertEqual(legacy_machine.code, "1")
        self.assertFalse(
            ProductionStation.objects.filter(pk=obsolete_station.pk).exists()
        )
        self.assertFalse(Machine.objects.filter(pk=obsolete_machine.pk).exists())

    def test_seed_keeps_referenced_obsolete_history_inactive(self):
        machine = Machine.objects.create(
            code="A03", name="历史A组3号机台", is_active=False
        )
        station = ProductionStation.objects.create(
            code="A03",
            group="A",
            position_no=3,
            machine=machine,
            is_active=False,
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
        self.assertFalse(station.is_active)
        self.assertFalse(machine.is_active)
        self.assertEqual(run.station_id, station.pk)

    def test_only_six_current_and_legacy_alias_codes_are_normalized(self):
        self.assertEqual(normalize_production_station_code("01"), "1")
        self.assertEqual(normalize_production_station_code("B01"), "3")
        self.assertEqual(normalize_production_station_code("c2"), "6")
        self.assertEqual(normalize_production_station_code("A03"), "A03")
        self.assertEqual(normalize_production_station_code("7"), "7")
