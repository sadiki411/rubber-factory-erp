from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from production.models import ProductionRun, ProductionStation

from .helpers import ProductionTestMixin


class ProductionMaterialChangeTests(ProductionTestMixin, TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.stations = list(ProductionStation.objects.order_by("code"))

    def run_values(self, station, **overrides):
        values = {
            "station": station,
            "order_no": f"ORD-MATERIAL-{station.code}",
            "specification": "密封圈",
            "material": "NBR",
            "order_quantity": 100,
            "cavities": 2,
            "planned_mold_count": 50,
            "curing_seconds": 60,
            "estimated_hours": 1,
            "created_by": self.user,
        }
        values.update(overrides)
        return values

    def test_material_change_validation_and_board_response(self):
        now = timezone.now()
        planned = ProductionRun.objects.create(**self.run_values(self.stations[0]))
        planned_response = self.client.patch(
            f"/api/production/runs/{planned.pk}/",
            {"material_changed_at": now.isoformat()},
            format="json",
        )
        self.assertEqual(planned_response.status_code, 400, planned_response.content)

        loaded_at = now - timedelta(hours=2)
        running_mold = self.create_mold(
            asset_code="MATERIAL-CHANGE-MOLD",
            machine_code=self.stations[1].machine.code,
        )
        running = ProductionRun.objects.create(
            **self.run_values(
                self.stations[1],
                mold=running_mold,
                loaded_at=loaded_at,
                status=ProductionRun.Status.RUNNING,
            )
        )
        before_loaded = self.client.patch(
            f"/api/production/runs/{running.pk}/",
            {"material_changed_at": (loaded_at - timedelta(minutes=1)).isoformat()},
            format="json",
        )
        self.assertEqual(before_loaded.status_code, 400, before_loaded.content)

        valid_time = loaded_at + timedelta(hours=1)
        valid = self.client.patch(
            f"/api/production/runs/{running.pk}/",
            {"material_changed_at": valid_time.isoformat()},
            format="json",
        )
        self.assertEqual(valid.status_code, 200, valid.content)
        self.assertIsNotNone(valid.json()["material_changed_at"])

        board = self.client.get("/api/production/board/")
        self.assertEqual(board.status_code, 200, board.content)
        board_runs = [
            station["run"]
            for group in board.json()["groups"]
            for station in group["stations"]
            if station["run"]
        ]
        board_run = next(item for item in board_runs if item["id"] == running.pk)
        self.assertIsNotNone(board_run["material_changed_at"])

        completed = ProductionRun.objects.create(
            **self.run_values(
                self.stations[2],
                loaded_at=loaded_at,
                unloaded_at=now,
                status=ProductionRun.Status.COMPLETED,
            )
        )
        after_stop = self.client.patch(
            f"/api/production/runs/{completed.pk}/",
            {"material_changed_at": (now + timedelta(minutes=1)).isoformat()},
            format="json",
        )
        self.assertEqual(after_stop.status_code, 400, after_stop.content)
