from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from molds.models import (
    Machine,
    MoldAsset,
    MoldModel,
    MoldMovement,
    Processor,
    Rack,
    RackSlot,
    RackZone,
)
from molds.services import (
    DEFAULT_RACK_LAYOUT_VERSION,
    ConfirmationRequired,
    seed_default_racks,
    switch_zone_capacity,
    switch_zone_stacking,
    transition_mold,
)
from production.models import ProductionRun, ProductionStation

from .helpers import SeededRackMixin


class DefaultRackInitializationTests(SeededRackMixin, TestCase):
    def test_seed_creates_expected_racks_and_slot_counts(self):
        self.assertEqual(list(Rack.objects.values_list("code", flat=True)), [f"J{i:02d}" for i in range(1, 8)])
        self.assertEqual(RackSlot.objects.count(), 814)
        self.assertEqual(sum(slot.is_enabled for slot in RackSlot.objects.select_related("zone__level__rack")), 138)

        expected = {
            "J01": (6, 120, 24, True),
            "J02": (8, 160, 32, True),
            "J03": (6, 60, 12, True),
            "J04": (6, 60, 12, True),
            "J05": (4, 144, 16, True),
            "J06": (9, 270, 42, True),
            "J07": (0, 0, 0, False),
        }
        for rack_code, (levels, slots, enabled_slots, configured) in expected.items():
            rack = Rack.objects.get(code=rack_code)
            self.assertEqual(rack.levels.count(), levels, rack_code)
            rack_slots = RackSlot.objects.filter(zone__level__rack=rack).select_related(
                "zone__level__rack"
            )
            self.assertEqual(rack_slots.count(), slots, rack_code)
            self.assertEqual(sum(slot.is_enabled for slot in rack_slots), enabled_slots, rack_code)
            self.assertEqual(rack.is_configured, configured, rack_code)
            self.assertEqual(rack.structure_locked, configured, rack_code)
            self.assertEqual(rack.layout_version, DEFAULT_RACK_LAYOUT_VERSION, rack_code)

    def test_seed_is_idempotent(self):
        before = dict(RackSlot.objects.values_list("technical_code", "pk"))
        seed_default_racks()
        seed_default_racks()
        self.assertEqual(Rack.objects.count(), 7)
        self.assertEqual(RackSlot.objects.count(), 814)
        self.assertEqual(dict(RackSlot.objects.values_list("technical_code", "pk")), before)

    def test_all_configured_racks_use_variable_capacity_and_optional_stacking(self):
        expected = {
            "J01": ({"A", "B"}, [2, 3]),
            "J02": ({"A", "B"}, [2, 3]),
            "J03": ({"F"}, [2, 3]),
            "J04": ({"F"}, [2, 3]),
            "J05": ({"A", "B"}, [2, 3, 4]),
            "J06": ({"A", "B", "C"}, [2, 3]),
        }
        for rack_code, (zone_codes, capacities) in expected.items():
            with self.subTest(rack=rack_code):
                zones = RackZone.objects.filter(level__rack__code=rack_code)
                self.assertEqual(set(zones.values_list("code", flat=True)), zone_codes)
                self.assertTrue(all(zone.allowed_capacities == capacities for zone in zones))
                self.assertTrue(all(zone.capacity_mode == 2 for zone in zones))
                self.assertTrue(all(zone.supports_stacking for zone in zones))
                self.assertFalse(zones.filter(stacking_enabled=True).exists())
                self.assertTrue(
                    RackSlot.objects.filter(
                        zone__level__rack__code=rack_code,
                        stack_level=2,
                    ).exists()
                )

    def test_seed_does_not_destroy_legacy_layout_when_a_mold_is_present(self):
        rack = Rack.objects.get(code="J01")
        slot = self.slot("J01", 1, zone="A", position=1)
        mold = self.create_mold("LEGACY-LAYOUT-001", slot)
        rack.layout_version = 1
        rack.save(update_fields=["layout_version"])
        slot_ids = list(
            RackSlot.objects.filter(zone__level__rack=rack)
            .order_by("pk")
            .values_list("pk", flat=True)
        )

        warnings = seed_default_racks()

        rack.refresh_from_db()
        mold.refresh_from_db()
        self.assertEqual(rack.layout_version, 1)
        self.assertEqual(mold.current_slot_id, slot.pk)
        self.assertEqual(
            list(
                RackSlot.objects.filter(zone__level__rack=rack)
                .order_by("pk")
                .values_list("pk", flat=True)
            ),
            slot_ids,
        )
        self.assertTrue(any("J01" in warning and "仍有在库模具" in warning for warning in warnings))


class J06RackLayoutTests(SeededRackMixin, TestCase):
    def test_three_column_groups_and_middle_extension_are_seeded(self):
        for level_no in range(1, 10):
            zones = RackZone.objects.filter(
                level__rack__code="J06", level__level_no=level_no
            ).order_by("code")
            self.assertEqual(list(zones.values_list("code", flat=True)), ["A", "B", "C"])
            for zone in zones:
                self.assertEqual(zone.allowed_capacities, [2, 3])
                self.assertEqual(zone.capacity_mode, 2)

        for level_no in (7, 8, 9):
            for zone_code in ("A", "C"):
                zone = RackZone.objects.get(
                    level__rack__code="J06",
                    level__level_no=level_no,
                    code=zone_code,
                )
                self.assertFalse(zone.is_active)
                self.assertEqual(zone.label, "杂物区")
                self.assertTrue(zone.slots.exists())
                self.assertTrue(all(slot.is_blocked for slot in zone.slots.all()))
                self.assertFalse(any(slot.is_enabled for slot in zone.slots.all()))

            middle = RackZone.objects.get(
                level__rack__code="J06", level__level_no=level_no, code="B"
            )
            self.assertTrue(middle.is_active)
            self.assertEqual(
                sum(slot.is_enabled for slot in middle.slots.select_related("zone__level__rack")),
                2,
            )

    def test_default_valid_positions_preserve_three_independent_pillar_sections(self):
        level = 1
        for zone_code in ("A", "B", "C"):
            enabled = [
                slot
                for slot in RackSlot.objects.filter(
                    zone__level__rack__code="J06",
                    zone__level__level_no=level,
                    zone__code=zone_code,
                ).select_related("zone__level__rack")
                if slot.is_enabled
            ]
            self.assertEqual([slot.position_no for slot in enabled], [1, 2])
            self.assertTrue(
                all(f"-{zone_code}-" in slot.display_code for slot in enabled),
                zone_code,
            )

        upper_enabled = [
            slot
            for slot in RackSlot.objects.filter(
                zone__level__rack__code="J06",
                zone__level__level_no__in=[7, 8, 9],
            ).select_related("zone__level__rack")
            if slot.is_enabled
        ]
        self.assertEqual(len(upper_enabled), 6)
        self.assertEqual({slot.zone.code for slot in upper_enabled}, {"B"})


class MoldLocationConstraintTests(SeededRackMixin, TestCase):
    def test_same_model_can_have_multiple_unique_physical_assets(self):
        model = MoldModel.objects.create(code="ABC-100", product_name="密封圈")
        first = self.create_mold("ABC-100-01", self.slot("J01", 1, position=1), model=model)
        second = self.create_mold("ABC-100-02", self.slot("J01", 1, position=2), model=model)

        assets = list(MoldAsset.objects.filter(mold_model__code__icontains="abc-100").order_by("asset_code"))
        self.assertEqual(assets, [first, second])
        self.assertNotEqual(assets[0].current_slot_id, assets[1].current_slot_id)

    def test_database_prevents_two_molds_occupying_one_slot(self):
        slot = self.slot("J01", 1, position=1)
        self.create_mold("MOLD-001", slot)
        second_model = MoldModel.objects.create(code="MODEL-002", product_name="产品 2")

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                MoldAsset.objects.create(
                    asset_code="MOLD-002",
                    mold_model=second_model,
                    status=MoldAsset.Status.IN_STOCK,
                    current_slot=slot,
                )

    def test_status_and_location_database_constraint(self):
        model = MoldModel.objects.create(code="BAD-STATE", product_name="错误状态")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                MoldAsset.objects.create(
                    asset_code="BAD-STATE-01",
                    mold_model=model,
                    status=MoldAsset.Status.ON_MACHINE,
                    current_slot=self.slot("J01", 1),
                )

        returned = MoldAsset.objects.create(
            asset_code="RETURNED-VALID-01",
            mold_model=model,
            status=MoldAsset.Status.OUTSOURCED,
        )
        self.assertIsNone(returned.current_slot)
        self.assertIsNone(returned.current_machine)
        self.assertIsNone(returned.current_processor)

        processor = Processor.objects.create(code="OLD-OUT", name="旧加工方")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                MoldAsset.objects.create(
                    asset_code="RETURNED-INVALID-01",
                    mold_model=model,
                    status=MoldAsset.Status.OUTSOURCED,
                    current_processor=processor,
                )


class MoldTransitionTests(SeededRackMixin, TestCase):
    def setUp(self):
        self.source = self.slot("J01", 1, position=1)
        self.destination = self.slot("J01", 1, position=2)
        self.mold = self.create_mold("FLOW-001", self.source)
        self.machine = Machine.objects.create(code="MC-01", name="一号机")
        self.production_machine = Machine.objects.create(code="1", name="1号机台")
        self.production_station = ProductionStation.objects.create(
            code="1",
            group=ProductionStation.Group.A,
            position_no=1,
            machine=self.production_machine,
        )
        self.processor = Processor.objects.create(code="OUT-01", name="外协一厂")

    def create_production_run(self, status, *, mold=None):
        mold = mold or self.mold
        now = timezone.now()
        values = {
            "station": self.production_station,
            "order_no": f"ORDER-{mold.asset_code}-{status}",
            "specification": "测试制品",
            "mold": mold,
            "order_quantity": 60,
            "cavities": 6,
            "planned_mold_count": 10,
            "status": status,
            "created_by": self.user,
        }
        if status in (ProductionRun.Status.RUNNING, ProductionRun.Status.COMPLETED):
            values["loaded_at"] = now
        if status == ProductionRun.Status.COMPLETED:
            values["unloaded_at"] = now
        return ProductionRun.objects.create(**values)

    def test_transitions_release_previous_location_and_append_complete_history(self):
        moved, warnings = transition_mold(
            self.mold,
            MoldMovement.Action.LOAD_MACHINE,
            self.user,
            machine=self.machine,
            note="生产任务",
        )
        self.assertEqual(warnings, [])
        self.assertEqual(moved.status, MoldAsset.Status.ON_MACHINE)
        self.assertEqual(moved.current_machine, self.machine)
        self.assertIsNone(moved.current_slot)
        self.assertFalse(MoldAsset.objects.filter(current_slot=self.source).exists())

        putaway, _ = transition_mold(
            moved,
            MoldMovement.Action.PUTAWAY,
            self.user,
            slot=self.destination,
            note="下机归位",
        )
        self.assertEqual(putaway.status, MoldAsset.Status.IN_STOCK)
        self.assertEqual(putaway.current_slot, self.destination)
        self.assertIsNone(putaway.current_machine)

        outsourced, _ = transition_mold(
            putaway,
            MoldMovement.Action.SEND_OUT,
            self.user,
            note="客户收回",
        )
        self.assertEqual(outsourced.status, MoldAsset.Status.OUTSOURCED)
        self.assertIsNone(outsourced.current_processor)
        self.assertIsNone(outsourced.current_slot)
        self.assertFalse(MoldAsset.objects.filter(current_slot=self.destination).exists())

        history = list(MoldMovement.objects.filter(mold=self.mold).order_by("id"))
        self.assertEqual([item.action for item in history], ["LOAD_MACHINE", "PUTAWAY", "SEND_OUT"])
        self.assertEqual(history[0].from_slot, self.source)
        self.assertEqual(history[0].to_machine, self.machine)
        self.assertEqual(history[1].from_machine, self.machine)
        self.assertEqual(history[1].to_slot, self.destination)
        self.assertEqual(history[2].from_slot, self.destination)
        self.assertIsNone(history[2].to_processor)
        self.assertTrue(all(item.operator == self.user for item in history))

    def test_move_requires_in_stock_and_a_different_free_slot(self):
        with self.assertRaisesMessage(ValidationError, "目标库位与当前库位相同"):
            transition_mold(self.mold, MoldMovement.Action.MOVE, self.user, slot=self.source)

        occupied = self.create_mold("FLOW-002", self.destination)
        with self.assertRaisesMessage(ValidationError, "目标库位已被占用"):
            transition_mold(self.mold, MoldMovement.Action.MOVE, self.user, slot=occupied.current_slot)

        on_machine, _ = transition_mold(
            self.mold, MoldMovement.Action.LOAD_MACHINE, self.user, machine=self.machine
        )
        with self.assertRaisesMessage(ValidationError, "只有在库模具可以移库"):
            transition_mold(on_machine, MoldMovement.Action.MOVE, self.user, slot=self.source)

    def test_failed_transition_does_not_change_state_or_create_history(self):
        inactive = Machine.objects.create(code="MC-OFF", name="停用机", is_active=False)
        before_changed_at = self.mold.status_changed_at
        with self.assertRaisesMessage(ValidationError, "所选机台已停用"):
            transition_mold(self.mold, MoldMovement.Action.LOAD_MACHINE, self.user, machine=inactive)

        self.mold.refresh_from_db()
        self.assertEqual(self.mold.status, MoldAsset.Status.IN_STOCK)
        self.assertEqual(self.mold.current_slot, self.source)
        self.assertEqual(self.mold.status_changed_at, before_changed_at)
        self.assertFalse(MoldMovement.objects.filter(mold=self.mold).exists())

    def test_planned_production_blocks_inconsistent_movements(self):
        self.create_production_run(ProductionRun.Status.PLANNED)

        with self.assertRaisesMessage(ValidationError, "已关联活动生产订单"):
            transition_mold(
                self.mold,
                MoldMovement.Action.MOVE,
                self.user,
                slot=self.destination,
            )
        with self.assertRaisesMessage(ValidationError, "不能移库、归位、标记客户收回"):
            transition_mold(
                self.mold,
                MoldMovement.Action.SEND_OUT,
                self.user,
                processor=self.processor,
            )
        with self.assertRaisesMessage(ValidationError, "只能上机到 1"):
            transition_mold(
                self.mold,
                MoldMovement.Action.LOAD_MACHINE,
                self.user,
                machine=self.machine,
            )

        loaded, _ = transition_mold(
            self.mold,
            MoldMovement.Action.LOAD_MACHINE,
            self.user,
            machine=self.production_machine,
        )
        self.assertEqual(loaded.status, MoldAsset.Status.ON_MACHINE)
        self.assertEqual(loaded.current_machine, self.production_machine)
        with self.assertRaisesMessage(ValidationError, "不能重复上机"):
            transition_mold(
                loaded,
                MoldMovement.Action.LOAD_MACHINE,
                self.user,
                machine=self.production_machine,
            )
        self.assertEqual(MoldMovement.objects.filter(mold=self.mold).count(), 1)

    def test_machine_reserved_by_another_active_run_rejects_loading(self):
        self.create_production_run(ProductionRun.Status.PLANNED)
        other = self.create_mold(
            "FLOW-RESERVED",
            self.slot("J01", 2, zone="A", position=1),
        )

        with self.assertRaisesMessage(ValidationError, "预留给模具 FLOW-001"):
            transition_mold(
                other,
                MoldMovement.Action.LOAD_MACHINE,
                self.user,
                machine=self.production_machine,
            )

        other.refresh_from_db()
        self.assertEqual(other.status, MoldAsset.Status.IN_STOCK)
        self.assertIsNotNone(other.current_slot_id)
        self.assertFalse(MoldMovement.objects.filter(mold=other).exists())

    def test_running_production_blocks_putaway_and_outsourcing(self):
        on_machine, _ = transition_mold(
            self.mold,
            MoldMovement.Action.LOAD_MACHINE,
            self.user,
            machine=self.production_machine,
        )
        self.create_production_run(ProductionRun.Status.RUNNING)

        with self.assertRaisesMessage(ValidationError, "已关联活动生产订单"):
            transition_mold(
                on_machine,
                MoldMovement.Action.PUTAWAY,
                self.user,
                slot=self.destination,
            )
        with self.assertRaisesMessage(ValidationError, "已关联活动生产订单"):
            transition_mold(
                on_machine,
                MoldMovement.Action.SEND_OUT,
                self.user,
                processor=self.processor,
            )
        with self.assertRaisesMessage(ValidationError, "不能进行任何流转操作"):
            transition_mold(
                on_machine,
                MoldMovement.Action.LOAD_MACHINE,
                self.user,
                machine=self.production_machine,
            )

        on_machine.refresh_from_db()
        self.assertEqual(on_machine.status, MoldAsset.Status.ON_MACHINE)
        self.assertEqual(on_machine.current_machine, self.production_machine)

    def test_completed_and_cancelled_runs_do_not_block_movement(self):
        for index, run_status in enumerate(
            (ProductionRun.Status.COMPLETED, ProductionRun.Status.CANCELLED),
            start=1,
        ):
            with self.subTest(status=run_status):
                mold = self.create_mold(
                    f"HISTORY-{index}",
                    self.slot("J01", 2, zone="A", position=index),
                )
                self.create_production_run(run_status, mold=mold)

                moved, _ = transition_mold(
                    mold,
                    MoldMovement.Action.MOVE,
                    self.user,
                    slot=self.slot("J01", 2, zone="B", position=index),
                )

                self.assertEqual(moved.status, MoldAsset.Status.IN_STOCK)
                self.assertEqual(moved.current_slot.zone.code, "B")
                self.assertEqual(moved.current_slot.position_no, index)

    def test_movement_instances_are_immutable(self):
        transition_mold(self.mold, MoldMovement.Action.LOAD_MACHINE, self.user, machine=self.machine)
        movement = MoldMovement.objects.get(mold=self.mold)
        movement.note = "篡改"
        with self.assertRaises(ValidationError):
            movement.save()
        with self.assertRaises(ValidationError):
            movement.delete()


class CapacitySwitchTests(SeededRackMixin, TestCase):
    def setUp(self):
        self.zone_a = RackZone.objects.get(level__rack__code="J02", level__level_no=1, code="A")
        self.zone_b = RackZone.objects.get(level__rack__code="J02", level__level_no=1, code="B")

    def test_left_and_right_zone_capacity_switch_independently_when_empty(self):
        switched = switch_zone_capacity(self.zone_a, 3)
        self.zone_b.refresh_from_db()
        self.assertEqual(switched.capacity_mode, 3)
        self.assertEqual(self.zone_b.capacity_mode, 2)

        old_slot = self.slot("J02", 1, zone="A", capacity=2, position=1)
        new_slot = self.slot("J02", 1, zone="A", capacity=3, position=3)
        self.assertFalse(old_slot.is_enabled)
        self.assertTrue(new_slot.is_enabled)

    def test_zone_with_any_mold_cannot_switch_capacity(self):
        slot = self.slot("J02", 1, zone="A", capacity=2, position=1)
        self.create_mold("CAP-001", slot)
        with self.assertRaisesMessage(ValidationError, "该区域仍有模具"):
            switch_zone_capacity(self.zone_a, 3)
        self.zone_a.refresh_from_db()
        self.assertEqual(self.zone_a.capacity_mode, 2)

    def test_capacity_must_be_allowed(self):
        with self.assertRaisesMessage(ValidationError, "不属于该区域允许的容量"):
            switch_zone_capacity(self.zone_a, 4)

    def test_j06_each_pillar_section_can_switch_from_two_to_three_positions(self):
        zone_b = RackZone.objects.get(
            level__rack__code="J06", level__level_no=1, code="B"
        )
        switched = switch_zone_capacity(zone_b, 3)
        self.assertEqual(switched.capacity_mode, 3)
        enabled = [
            slot
            for slot in zone_b.slots.select_related("zone__level__rack")
            if slot.is_enabled
        ]
        self.assertEqual([slot.position_no for slot in enabled], [1, 2, 3])
        self.assertTrue(all(slot.stack_level == 1 for slot in enabled))
        sibling_modes = set(
            RackZone.objects.filter(
                level__rack__code="J06",
                level__level_no=1,
                code__in=["A", "C"],
            ).values_list("capacity_mode", flat=True)
        )
        self.assertEqual(sibling_modes, {2})

    def test_inactive_storage_zone_cannot_switch_capacity(self):
        inactive = RackZone.objects.get(
            level__rack__code="J06", level__level_no=9, code="A"
        )
        with self.assertRaisesMessage(ValidationError, "停用区域不能切换容量"):
            switch_zone_capacity(inactive, 3)
        inactive.refresh_from_db()
        self.assertEqual(inactive.capacity_mode, 2)


class OptionalStackingTests(SeededRackMixin, TestCase):
    def setUp(self):
        self.zone = RackZone.objects.get(
            level__rack__code="J01", level__level_no=1, code="A"
        )
        self.lower = self.slot("J01", 1, zone="A", position=1, stack=1)
        self.upper = self.slot("J01", 1, zone="A", position=1, stack=2)

    def test_upper_slots_exist_but_are_disabled_until_stacking_is_enabled(self):
        self.assertTrue(self.zone.supports_stacking)
        self.assertFalse(self.zone.stacking_enabled)
        self.assertTrue(self.lower.is_enabled)
        self.assertFalse(self.upper.is_enabled)

        switch_zone_stacking(self.zone, True)
        self.zone.refresh_from_db()
        self.assertTrue(self.zone.stacking_enabled)
        self.assertTrue(self.slot("J01", 1, zone="A", position=1, stack=2).is_enabled)

        switch_zone_stacking(self.zone, False)
        self.zone.refresh_from_db()
        self.assertFalse(self.zone.stacking_enabled)
        self.assertFalse(self.slot("J01", 1, zone="A", position=1, stack=2).is_enabled)

    def test_upper_mold_prevents_disabling_stacking(self):
        switch_zone_stacking(self.zone, True)
        self.create_mold("STACK-OCCUPIED-001", self.upper)

        with self.assertRaisesMessage(ValidationError, "上叠位仍有模具"):
            switch_zone_stacking(self.zone, False)

        self.zone.refresh_from_db()
        self.assertTrue(self.zone.stacking_enabled)
        self.assertTrue(self.slot("J01", 1, zone="A", position=1, stack=2).is_enabled)

    def test_inactive_storage_zone_cannot_switch_stacking(self):
        inactive = RackZone.objects.get(
            level__rack__code="J06", level__level_no=9, code="C"
        )
        with self.assertRaisesMessage(ValidationError, "停用区域不能切换叠放状态"):
            switch_zone_stacking(inactive, True)
        inactive.refresh_from_db()
        self.assertFalse(inactive.stacking_enabled)


class J05StackingWarningTests(SeededRackMixin, TestCase):
    def setUp(self):
        self.source = self.slot("J01", 1, position=1)
        self.lower = self.slot("J05", 1, zone="A", capacity=2, position=1, stack=1)
        self.upper = self.slot("J05", 1, zone="A", capacity=2, position=1, stack=2)
        switch_zone_stacking(self.upper.zone, True)
        self.machine = Machine.objects.create(code="STACK-MC", name="叠放测试机")

    def test_upper_without_lower_requires_confirmation_and_is_atomic(self):
        mold = self.create_mold("UPPER-001", self.source)
        with self.assertRaises(ConfirmationRequired) as caught:
            transition_mold(mold, MoldMovement.Action.MOVE, self.user, slot=self.upper)
        self.assertTrue(any("上叠位置下方没有模具" in warning for warning in caught.exception.warnings))
        mold.refresh_from_db()
        self.assertEqual(mold.current_slot, self.source)
        self.assertFalse(MoldMovement.objects.filter(mold=mold).exists())

        moved, warnings = transition_mold(
            mold,
            MoldMovement.Action.MOVE,
            self.user,
            slot=self.upper,
            confirm_warnings=True,
        )
        self.assertEqual(moved.current_slot, self.upper)
        self.assertTrue(any("上叠位置下方没有模具" in warning for warning in warnings))

    def test_upper_on_non_stackable_lower_requires_confirmation(self):
        self.create_mold("LOWER-001", self.lower, allows_stacking=False)
        upper_mold = self.create_mold("UPPER-002", self.source)
        with self.assertRaises(ConfirmationRequired) as caught:
            transition_mold(upper_mold, MoldMovement.Action.MOVE, self.user, slot=self.upper)
        self.assertTrue(any("未标记为允许叠放" in warning for warning in caught.exception.warnings))

    def test_moving_lower_while_upper_remains_requires_confirmation(self):
        lower_mold = self.create_mold("LOWER-002", self.lower, allows_stacking=True)
        upper_model = MoldModel.objects.create(code="UPPER-MODEL", product_name="上层模具")
        MoldAsset.objects.create(
            asset_code="UPPER-003",
            mold_model=upper_model,
            status=MoldAsset.Status.IN_STOCK,
            current_slot=self.upper,
        )

        with self.assertRaises(ConfirmationRequired) as caught:
            transition_mold(
                lower_mold,
                MoldMovement.Action.LOAD_MACHINE,
                self.user,
                machine=self.machine,
            )
        self.assertTrue(any("上叠位置仍有模具" in warning for warning in caught.exception.warnings))

        moved, _ = transition_mold(
            lower_mold,
            MoldMovement.Action.LOAD_MACHINE,
            self.user,
            machine=self.machine,
            confirm_warnings=True,
        )
        self.assertEqual(moved.status, MoldAsset.Status.ON_MACHINE)
        self.assertTrue(MoldAsset.objects.filter(current_slot=self.upper, asset_code="UPPER-003").exists())
