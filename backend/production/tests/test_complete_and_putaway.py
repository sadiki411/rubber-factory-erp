from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from drf_spectacular.generators import SchemaGenerator
from rest_framework.test import APIClient

from molds.models import MoldAsset, MoldModel, MoldMovement, RackSlot
from molds.services import seed_default_racks, switch_zone_stacking
from production.models import ProductionRun, ProductionStation
from production.services import seed_default_stations


class CompleteAndPutawayProductionRunApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="complete-putaway-user",
            password="complete-putaway-password",
        )
        seed_default_racks()
        seed_default_stations()

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    @staticmethod
    def slot(rack="J01", level=1, zone="A", position=1, stack=1):
        return RackSlot.objects.select_related("zone__level__rack").get(
            zone__level__rack__code=rack,
            zone__level__level_no=level,
            zone__code=zone,
            capacity_mode=2,
            position_no=position,
            stack_level=stack,
        )

    @staticmethod
    def create_mold(asset_code, *, status, machine=None, slot=None):
        model = MoldModel.objects.create(
            code=f"MODEL-{asset_code}",
            product_name=f"产品 {asset_code}",
        )
        return MoldAsset.objects.create(
            asset_code=asset_code,
            mold_model=model,
            status=status,
            current_machine=machine,
            current_slot=slot,
        )

    def create_running_run(
        self,
        *,
        order_no="ATOMIC-PUTAWAY-001",
        station_code="1",
        with_mold=True,
    ):
        station = ProductionStation.objects.select_related("machine").get(
            code=station_code
        )
        mold = None
        if with_mold:
            mold = self.create_mold(
                f"MOLD-{order_no}",
                status=MoldAsset.Status.ON_MACHINE,
                machine=station.machine,
            )
        loaded_at = timezone.now() - timedelta(hours=2)
        create_status = (
            ProductionRun.Status.RUNNING
            if with_mold
            else ProductionRun.Status.PLANNED
        )
        run = ProductionRun.objects.create(
            station=station,
            mold=mold,
            order_no=order_no,
            specification="原子下机测试",
            material="NBR",
            order_quantity=100,
            cavities=2,
            planned_mold_count=50,
            estimated_hours="4.00",
            loaded_at=loaded_at if with_mold else None,
            status=create_status,
            created_by=self.user,
        )
        if not with_mold:
            # Simulate a legacy inconsistent row created before RUNNING began
            # requiring a mold. The endpoint must reject without mutating it.
            ProductionRun.objects.filter(pk=run.pk).update(
                loaded_at=loaded_at,
                status=ProductionRun.Status.RUNNING,
            )
            run.refresh_from_db()
        return run, mold

    def assert_still_running_and_mounted(self, run, mold):
        run.refresh_from_db()
        mold.refresh_from_db()
        self.assertEqual(run.status, ProductionRun.Status.RUNNING)
        self.assertIsNone(run.unloaded_at)
        self.assertEqual(mold.status, MoldAsset.Status.ON_MACHINE)
        self.assertEqual(mold.current_machine_id, run.station.machine_id)
        self.assertIsNone(mold.current_slot_id)
        self.assertFalse(
            MoldMovement.objects.filter(
                mold=mold,
                action=MoldMovement.Action.PUTAWAY,
            ).exists()
        )

    def test_success_atomically_completes_run_and_puts_mold_away(self):
        run, mold = self.create_running_run()
        target = self.slot(level=6, position=2)
        unloaded_at = timezone.now()

        response = self.client.post(
            f"/api/production/runs/{run.pk}/complete-and-putaway/",
            {
                "slot_id": target.pk,
                "unloaded_at": unloaded_at.isoformat(),
                "note": "订单结束后直接归位",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["status"], ProductionRun.Status.COMPLETED)
        run.refresh_from_db()
        mold.refresh_from_db()
        self.assertEqual(run.status, ProductionRun.Status.COMPLETED)
        self.assertEqual(run.unloaded_at, unloaded_at)
        self.assertEqual(mold.status, MoldAsset.Status.IN_STOCK)
        self.assertEqual(mold.current_slot_id, target.pk)
        self.assertIsNone(mold.current_machine_id)
        movement = MoldMovement.objects.get(
            mold=mold,
            action=MoldMovement.Action.PUTAWAY,
        )
        self.assertEqual(movement.from_machine_id, run.station.machine_id)
        self.assertEqual(movement.to_slot_id, target.pk)
        self.assertEqual(movement.note, "订单结束后直接归位")
        self.assertEqual(movement.operator_id, self.user.pk)

    def test_stacking_confirmation_409_rolls_back_then_retry_succeeds(self):
        run, mold = self.create_running_run(order_no="ATOMIC-WARNING")
        upper = self.slot(rack="J05", level=4, position=1, stack=2)
        switch_zone_stacking(upper.zone, True)
        upper.refresh_from_db()

        warning = self.client.post(
            f"/api/production/runs/{run.pk}/complete-and-putaway/",
            {"slot_id": upper.pk},
            format="json",
        )

        self.assertEqual(warning.status_code, 409, warning.content)
        self.assertTrue(warning.json()["requires_confirmation"])
        self.assert_still_running_and_mounted(run, mold)

        confirmed = self.client.post(
            f"/api/production/runs/{run.pk}/complete-and-putaway/",
            {"slot_id": upper.pk, "confirm_warnings": True},
            format="json",
        )

        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        run.refresh_from_db()
        mold.refresh_from_db()
        self.assertEqual(run.status, ProductionRun.Status.COMPLETED)
        self.assertEqual(mold.status, MoldAsset.Status.IN_STOCK)
        self.assertEqual(mold.current_slot_id, upper.pk)

    def test_occupied_target_returns_400_and_rolls_back_everything(self):
        run, mold = self.create_running_run(order_no="ATOMIC-OCCUPIED")
        target = self.slot(level=5, position=2)
        self.create_mold(
            "OCCUPANT-001",
            status=MoldAsset.Status.IN_STOCK,
            slot=target,
        )

        response = self.client.post(
            f"/api/production/runs/{run.pk}/complete-and-putaway/",
            {"slot_id": target.pk},
            format="json",
        )

        self.assertEqual(response.status_code, 400, response.content)
        self.assert_still_running_and_mounted(run, mold)

    def test_running_order_without_mold_is_rejected_without_state_change(self):
        run, _mold = self.create_running_run(
            order_no="ATOMIC-NO-MOLD",
            with_mold=False,
        )

        response = self.client.post(
            f"/api/production/runs/{run.pk}/complete-and-putaway/",
            {"slot_id": self.slot(level=4, position=2).pk},
            format="json",
        )

        self.assertEqual(response.status_code, 400, response.content)
        run.refresh_from_db()
        self.assertEqual(run.status, ProductionRun.Status.RUNNING)
        self.assertIsNone(run.unloaded_at)

    def test_mold_not_on_station_machine_is_rejected_and_rolled_back(self):
        run, mold = self.create_running_run(order_no="ATOMIC-WRONG-MACHINE")
        other_station = ProductionStation.objects.select_related("machine").get(code="2")
        MoldAsset.objects.filter(pk=mold.pk).update(
            current_machine=other_station.machine,
        )

        response = self.client.post(
            f"/api/production/runs/{run.pk}/complete-and-putaway/",
            {"slot_id": self.slot(level=3, position=2).pk},
            format="json",
        )

        self.assertEqual(response.status_code, 400, response.content)
        run.refresh_from_db()
        mold.refresh_from_db()
        self.assertEqual(run.status, ProductionRun.Status.RUNNING)
        self.assertIsNone(run.unloaded_at)
        self.assertEqual(mold.status, MoldAsset.Status.ON_MACHINE)
        self.assertEqual(mold.current_machine_id, other_station.machine_id)

    def test_authentication_is_required(self):
        run, mold = self.create_running_run(order_no="ATOMIC-AUTH")
        anonymous = APIClient()

        response = anonymous.post(
            f"/api/production/runs/{run.pk}/complete-and-putaway/",
            {"slot_id": self.slot(level=2, position=2).pk},
            format="json",
        )

        self.assertIn(response.status_code, (401, 403), response.content)
        self.assert_still_running_and_mounted(run, mold)

    def test_running_order_creation_requires_a_mold(self):
        station = ProductionStation.objects.get(code="1")
        response = self.client.post(
            "/api/production/runs/",
            {
                "station_id": station.pk,
                "order_no": "RUNNING-NO-MOLD",
                "specification": "试模生产",
                "material": "NBR",
                "order_quantity": 10,
                "cavities": 1,
                "planned_mold_count": 10,
                "loaded_at": timezone.now().isoformat(),
                "status": ProductionRun.Status.RUNNING,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("mold_id", response.json())

    def test_started_or_finished_order_cannot_rebind_or_return_to_planned(self):
        run, mold = self.create_running_run(order_no="STATE-GUARD")
        other_station = ProductionStation.objects.select_related("machine").get(
            code="2"
        )
        other_mold = self.create_mold(
            "STATE-GUARD-OTHER",
            status=MoldAsset.Status.ON_MACHINE,
            machine=other_station.machine,
        )

        blocked_payloads = [
            {"station_id": other_station.pk},
            {"mold_id": other_mold.pk},
            {"status": ProductionRun.Status.PLANNED, "loaded_at": None},
        ]
        for payload in blocked_payloads:
            with self.subTest(payload=payload):
                response = self.client.patch(
                    f"/api/production/runs/{run.pk}/", payload, format="json"
                )
                self.assertEqual(response.status_code, 400, response.content)

        run.refresh_from_db()
        self.assertEqual(run.status, ProductionRun.Status.RUNNING)
        self.assertEqual(run.station.code, "1")
        self.assertEqual(run.mold_id, mold.pk)

        completed = self.client.post(
            f"/api/production/runs/{run.pk}/complete/", {}, format="json"
        )
        self.assertEqual(completed.status_code, 200, completed.content)
        after_completion = self.client.patch(
            f"/api/production/runs/{run.pk}/",
            {"station_id": other_station.pk, "mold_id": other_mold.pk},
            format="json",
        )
        self.assertEqual(after_completion.status_code, 400, after_completion.content)
        run.refresh_from_db()
        self.assertEqual(run.status, ProductionRun.Status.COMPLETED)
        self.assertEqual(run.station.code, "1")
        self.assertEqual(run.mold_id, mold.pk)


class CompleteAndPutawayProductionRunSchemaTests(TestCase):
    def test_action_has_dedicated_request_and_production_run_response(self):
        schema = SchemaGenerator().get_schema(request=None, public=True)
        operation = schema["paths"][
            "/api/production/runs/{id}/complete-and-putaway/"
        ]["post"]

        self.assertEqual(
            operation["requestBody"]["content"]["application/json"]["schema"],
            {"$ref": "#/components/schemas/CompleteAndPutawayProductionRun"},
        )
        self.assertEqual(
            operation["responses"]["200"]["content"]["application/json"][
                "schema"
            ],
            {"$ref": "#/components/schemas/ProductionRun"},
        )
        request_schema = schema["components"]["schemas"][
            "CompleteAndPutawayProductionRun"
        ]
        self.assertIn("slot_id", request_schema["required"])
