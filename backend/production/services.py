from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from molds.models import Machine, MoldAsset, MoldMovement, RackSlot
from molds.services import transition_mold

from .models import (
    PRODUCTION_STATION_LAYOUT,
    ProductionRun,
    ProductionSettlementRevision,
    ProductionStation,
)


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


@transaction.atomic
def complete_and_putaway_production_run(
    run,
    user,
    *,
    slot,
    unloaded_at=None,
    note="",
    confirm_warnings=False,
):
    """Atomically finish a running production order and return its mold.

    The mold, production order and destination slot are locked in that order,
    matching the lower-level mold transition lock order.
    Mold validation and confirmable stacking warnings are delegated to the
    regular mold transition service.  Any failure therefore rolls the whole
    operation back, leaving both the order and mold in their original states.
    """

    expected_mold_id = run.mold_id
    mold = None
    if expected_mold_id is not None:
        mold = (
            MoldAsset.objects.select_for_update()
            .select_related("current_machine", "mold_model")
            .get(pk=expected_mold_id)
        )

    run = (
        ProductionRun.objects.select_for_update()
        .select_related("station__machine", "mold__mold_model")
        .get(pk=run.pk)
    )
    if run.status != ProductionRun.Status.RUNNING or not run.loaded_at:
        raise ValidationError("只有生产中的订单可以结束并归位模具。")
    if run.mold_id is None:
        raise ValidationError("该生产订单没有关联模具，不能执行结束并归位。")
    if mold is None or mold.pk != run.mold_id:
        raise ValidationError("生产订单关联的模具状态已变化，请刷新后重试。")
    machine = run.station.machine
    if machine is None:
        raise ValidationError("该生产站位未关联模具台账机台，不能执行结束并归位。")
    if not mold.is_active:
        raise ValidationError("该生产订单关联的模具已删除，不能执行结束并归位。")
    if (
        mold.status != MoldAsset.Status.ON_MACHINE
        or mold.current_machine_id != machine.pk
    ):
        raise ValidationError(
            f"模具 {mold.asset_code} 当前不在生产站位关联的机台 {machine.code}，"
            "请刷新并检查模具台账后重试。"
        )

    locked_slot = (
        RackSlot.objects.select_for_update()
        .select_related("zone__level__rack")
        .get(pk=slot.pk)
    )
    completed_at = unloaded_at or timezone.now()
    if completed_at < run.loaded_at:
        raise ValidationError({"unloaded_at": "下机时间不能早于上模时间。"})

    run.status = ProductionRun.Status.COMPLETED
    run.unloaded_at = completed_at
    run.save(update_fields=["status", "unloaded_at", "updated_at"])

    movement_note = str(note or "").strip() or f"生产订单 {run.order_no} 结束并归位"
    mold, _warnings = transition_mold(
        mold,
        MoldMovement.Action.PUTAWAY,
        user,
        slot=locked_slot,
        note=movement_note,
        confirm_warnings=confirm_warnings,
    )
    run.mold = mold
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
    """Ensure the default three groups/six stations without touching custom rows.

    The built-in layout remains the out-of-box configuration.  Additional groups,
    positions and linked machines are user data and are deliberately left active
    and unchanged on every idempotent initialization run.
    """

    stations = []
    for group, position_no, code, legacy_code in PRODUCTION_STATION_LAYOUT:
        station = (
            ProductionStation.objects.select_related("machine")
            .filter(group=group, position_no=position_no)
            .first()
        )
        code_owner = ProductionStation.objects.filter(code=code).exclude(
            pk=station.pk if station else None
        ).first()
        if code_owner is not None:
            raise ValidationError(
                f"默认机台编号{code}已被{code_owner.group}组第"
                f"{code_owner.position_no}台占用，请先修正机台资料。"
            )

        machine = Machine.objects.filter(code=code).first()
        if machine is None:
            legacy_machine = None
            if station and station.machine_id and station.machine.code == legacy_code:
                legacy_machine = station.machine
            else:
                candidate = Machine.objects.filter(code=legacy_code).first()
                if candidate and not ProductionStation.objects.filter(
                    machine=candidate
                ).exists():
                    legacy_machine = candidate
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

        machine_owner = ProductionStation.objects.filter(machine=machine).exclude(
            pk=station.pk if station else None
        ).first()
        if machine_owner is not None:
            raise ValidationError(
                f"默认机台{code}关联的标准机台已被{machine_owner.code}占用，"
                "请先修正机台资料。"
            )

        if station is None:
            station = ProductionStation.objects.create(
                group=group,
                position_no=position_no,
                code=code,
                machine=machine,
                is_active=True,
            )
        else:
            station.code = code
            station.machine = machine
            station.is_active = True
            station.save(
                update_fields=["code", "machine", "is_active", "updated_at"]
            )
        stations.append(station)
    return stations
