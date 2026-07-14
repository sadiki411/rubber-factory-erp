from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from molds.models import Machine, MoldAsset, MoldMovement
from molds.services import transition_mold

from .models import (
    PRODUCTION_STATION_LAYOUT,
    ProductionRun,
    ProductionSettlementRevision,
    ProductionStation,
)


LEGACY_DEFAULT_MACHINE_CODES = {
    f"{group}{position_no:02d}"
    for group in ProductionStation.Group.values
    for position_no in range(1, 7)
}


@transaction.atomic
def start_production_run(
    run,
    user,
    *,
    loaded_at=None,
    note="",
    confirm_warnings=False,
):
    """Atomically mount the planned mold and start production.

    A repeated request is idempotent once the same mold is already running on
    the station's linked machine.  Mold stacking warnings deliberately bubble
    up as ``ConfirmationRequired`` so the API can reuse the existing 409
    confirmation contract.
    """

    run = (
        ProductionRun.objects.select_for_update()
        .select_related("station__machine", "mold__mold_model")
        .get(pk=run.pk)
    )
    station = run.station
    machine = station.machine
    mold = run.mold

    if run.status == ProductionRun.Status.RUNNING:
        if (
            run.loaded_at
            and machine is not None
            and mold is not None
            and mold.is_active
            and mold.status == MoldAsset.Status.ON_MACHINE
            and mold.current_machine_id == machine.pk
        ):
            return run
        raise ValidationError("生产订单已是生产中，但模具与机台状态不一致，请先检查台账。")

    if run.status != ProductionRun.Status.PLANNED:
        raise ValidationError("只有待上机订单可以执行确认上机。")
    if not station.is_active:
        raise ValidationError("该生产机台已停用，不能确认上机。")
    if machine is None:
        raise ValidationError("该生产机台尚未关联模具台账机台，不能确认上机。")
    if not machine.is_active:
        raise ValidationError("该生产机台关联的模具台账机台已停用，不能确认上机。")
    if mold is None:
        raise ValidationError("确认上机前必须为生产订单选择模具。")
    if not mold.is_active:
        raise ValidationError("所选模具已删除，不能确认上机。")

    already_mounted = (
        mold.status == MoldAsset.Status.ON_MACHINE
        and mold.current_machine_id == machine.pk
    )
    if not already_mounted:
        if mold.status != MoldAsset.Status.IN_STOCK:
            raise ValidationError("确认上机时模具必须在库，或已经位于该订单机台。")
        movement_note = str(note or "").strip() or f"生产订单 {run.order_no} 确认上机"
        mold, _warnings = transition_mold(
            mold,
            MoldMovement.Action.LOAD_MACHINE,
            user,
            machine=machine,
            note=movement_note,
            confirm_warnings=confirm_warnings,
        )
        run.mold = mold

    run.status = ProductionRun.Status.RUNNING
    run.loaded_at = loaded_at or timezone.now()
    run.unloaded_at = None
    run.expected_change_at = None
    run.save(
        update_fields=[
            "status",
            "loaded_at",
            "unloaded_at",
            "expected_change_at",
            "updated_at",
        ]
    )
    return run


def _next_settlement_revision(run):
    current = run.settlement_revisions.aggregate(value=Max("revision_no"))["value"]
    return (current or 0) + 1


def record_settlement_revision(run, user, action):
    return ProductionSettlementRevision.objects.create(
        run=run,
        revision_no=_next_settlement_revision(run),
        action=action,
        cavities=run.cavities,
        produced_mold_count=run.produced_mold_count,
        unit_price=run.unit_price,
        material_unit_price=run.material_unit_price,
        actual_good_quantity=run.actual_good_quantity,
        actual_defective_quantity=run.actual_defective_quantity,
        total_material_kg=run.total_material_kg,
        labor_cost=run.labor_cost,
        energy_cost=run.energy_cost,
        other_cost=run.other_cost,
        settlement_notes=run.settlement_notes,
        changed_by=user,
    )


def invalidate_settlement(run, user):
    """Preserve an audit snapshot and reopen accounting after mold totals change."""

    if not run.settled_at:
        return False
    record_settlement_revision(
        run, user, ProductionSettlementRevision.Action.INVALIDATED
    )
    ProductionRun.objects.filter(pk=run.pk, settled_at__isnull=False).update(
        settled_at=None,
        settled_by=None,
        updated_at=timezone.now(),
    )
    run.settled_at = None
    run.settled_by = None
    return True


@transaction.atomic
def seed_default_stations():
    """Ensure the physical 1-6 machine layout and retire obsolete defaults safely."""

    def retire_default_machine(machine_id):
        if not machine_id:
            return
        machine = Machine.objects.filter(pk=machine_id).first()
        if machine is None or machine.code not in LEGACY_DEFAULT_MACHINE_CODES:
            return
        referenced = (
            ProductionStation.objects.filter(machine_id=machine.pk).exists()
            or machine.current_molds.exists()
            or machine.movements_from.exists()
            or machine.movements_to.exists()
        )
        if referenced:
            if machine.is_active:
                Machine.objects.filter(pk=machine.pk).update(is_active=False)
        else:
            machine.delete()

    legacy_machine_ids = []
    obsolete_stations = ProductionStation.objects.filter(
        group__in=ProductionStation.Group.values,
        position_no__gt=2,
    ).select_related("machine")
    for station in obsolete_stations:
        if station.runs.exists():
            if station.is_active:
                ProductionStation.objects.filter(pk=station.pk).update(is_active=False)
            if station.machine_id:
                Machine.objects.filter(pk=station.machine_id).update(is_active=False)
        else:
            legacy_machine_ids.append(station.machine_id)
            station.delete()

    stations = []
    for group, position_no, code, legacy_code in PRODUCTION_STATION_LAYOUT:
        station = ProductionStation.objects.filter(
            group=group, position_no=position_no
        ).first()
        previous_machine_id = station.machine_id if station else None

        machine = Machine.objects.filter(code=code).first()
        if machine is None:
            legacy_machine = Machine.objects.filter(code=legacy_code).first()
            if (
                station
                and station.machine_id
                and station.machine.code == legacy_code
            ):
                legacy_machine = station.machine
            if legacy_machine:
                legacy_machine.code = code
                legacy_machine.name = f"{code}号机台"
                legacy_machine.is_active = True
                legacy_machine.save(update_fields=["code", "name", "is_active", "updated_at"])
                machine = legacy_machine
            else:
                machine = Machine.objects.create(
                    code=code, name=f"{code}号机台", is_active=True
                )
        elif not machine.is_active:
            Machine.objects.filter(pk=machine.pk).update(is_active=True)
            machine.is_active = True

        if station is None:
            station = ProductionStation.objects.create(
                group=group,
                position_no=position_no,
                code=code,
                machine=machine,
                is_active=True,
            )
        else:
            ProductionStation.objects.filter(machine_id=machine.pk).exclude(
                pk=station.pk
            ).update(machine=None, is_active=False)
            station.code = code
            station.machine = machine
            station.is_active = True
            station.save(
                update_fields=["code", "machine", "is_active", "updated_at"]
            )
        stations.append(station)

        if previous_machine_id and previous_machine_id != machine.pk:
            legacy_machine_ids.append(previous_machine_id)

    for machine_id in legacy_machine_ids:
        retire_default_machine(machine_id)
    for machine in list(Machine.objects.filter(code__in=LEGACY_DEFAULT_MACHINE_CODES)):
        retire_default_machine(machine.pk)
    return stations
