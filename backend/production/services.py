from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max, Sum
from django.utils import timezone

from molds.models import Machine, MoldAsset, MoldMovement, RackSlot
from molds.services import transition_mold

from .models import (
    PRODUCTION_STATION_LAYOUT,
    ProductionRun,
    ProductionDailyLog,
    ProductionEmployee,
    ProductionRecordAudit,
    ProductionSettlementRevision,
    ProductionStation,
)


def _log_snapshot(log):
    return {
        "id": log.pk,
        "date": log.production_date.isoformat() if log.production_date else None,
        "operator": log.operator,
        "shift": log.shift,
        "sequence_no": log.sequence_no,
        "counter_segment": log.counter_segment,
        "cumulative_mold_count": log.cumulative_mold_count,
        "produced_mold_count": log.produced_mold_count,
        "cavities_snapshot": log.cavities_snapshot,
        "defective_quantity": log.defective_quantity,
        "is_cancelled": log.is_cancelled,
        "notes": log.notes,
    }


def _record_audit(run, user, action, *, log=None, before=None, after=None, reason=""):
    return ProductionRecordAudit.objects.create(
        run=run,
        daily_log=log,
        action=action,
        before=before or {},
        after=after or {},
        reason=str(reason or "").strip(),
        changed_by=user,
    )


def _recalculate_counter_segment(run, segment):
    """Recalculate deltas in stable entry order and reject ambiguous histories."""

    logs = list(
        ProductionDailyLog.objects.select_for_update()
        .filter(run=run, counter_segment=segment)
        .order_by("sequence_no", "id")
    )
    previous = 0
    changed = []
    for log in logs:
        if log.is_cancelled:
            continue
        cumulative = int(log.cumulative_mold_count or 0)
        produced = cumulative - previous
        if produced < 1:
            raise ValidationError(
                {
                    "cumulative_mold_count": (
                        f"第{log.sequence_no}条交接读数必须大于上一条有效读数"
                        f"{previous}模；如机台计数已归零，请先使用“计数已清零”。"
                    )
                }
            )
        if int(log.defective_quantity or 0) > produced * int(log.cavities_snapshot or 1):
            raise ValidationError(
                {
                    "defective_quantity": (
                        f"第{log.sequence_no}条记录的不良数量超过该段理论产量。"
                    )
                }
            )
        if log.produced_mold_count != produced:
            log.produced_mold_count = produced
            changed.append(log)
        previous = cumulative
    if changed:
        ProductionDailyLog.objects.bulk_update(changed, ["produced_mold_count", "updated_at"])
    return logs


@transaction.atomic
def create_counter_log(run, user, validated_data):
    run = ProductionRun.objects.select_for_update().get(pk=run.pk)
    if run.status in (ProductionRun.Status.COMPLETED, ProductionRun.Status.CANCELLED):
        raise ValidationError("已完成或已取消的生产任务不能新增交接读数。")
    if run.status == ProductionRun.Status.PAUSED_UNLOADED:
        raise ValidationError("该生产段已经下机，请先恢复为新的生产段。")

    data = dict(validated_data)
    assistants = data.pop("assistant_operators", [])
    data.pop("_confirmed_duplicate", None)
    employee = data.get("operator_employee")
    if employee is None:
        name = str(data.get("operator") or "").strip()
        if name:
            employee = ProductionEmployee.objects.filter(name__iexact=name).first()
            if employee is None:
                employee = ProductionEmployee.objects.create(name=name)
            data["operator_employee"] = employee
            data["operator"] = employee.name

    previous = (
        ProductionDailyLog.objects.select_for_update()
        .filter(
            run=run,
            counter_segment=run.counter_segment,
            is_cancelled=False,
        )
        .order_by("-sequence_no", "-id")
        .first()
    )
    previous_count = int(previous.cumulative_mold_count or 0) if previous else 0
    cumulative = int(data["cumulative_mold_count"])
    produced = cumulative - previous_count
    if produced < 1:
        raise ValidationError(
            {
                "cumulative_mold_count": (
                    f"累计模数必须大于上一条读数{previous_count}模；"
                    "如机台计数已归零，请先点击“计数已清零”。"
                )
            }
        )
    cavities = int(data.get("cavities_snapshot") or run.cavities)
    if int(data.get("defective_quantity") or 0) > produced * cavities:
        raise ValidationError(
            {"defective_quantity": "不良数量不能超过本次实际模数对应的理论产量。"}
        )
    sequence = (
        ProductionDailyLog.objects.filter(run=run).aggregate(value=Max("sequence_no"))[
            "value"
        ]
        or 0
    ) + 1
    log = ProductionDailyLog(
        run=run,
        sequence_no=sequence,
        counter_segment=run.counter_segment,
        produced_mold_count=produced,
        **data,
    )
    log._allow_planned_counter = True
    log.save()
    if assistants:
        log.assistant_operators.set(assistants)
    invalidate_settlement(run, user)
    _record_audit(
        run,
        user,
        ProductionRecordAudit.Action.CREATED,
        log=log,
        after=_log_snapshot(log),
    )
    return log


@transaction.atomic
def update_counter_log(log, user, validated_data):
    run = ProductionRun.objects.select_for_update().get(pk=log.run_id)
    log = ProductionDailyLog.objects.select_for_update().get(pk=log.pk, run=run)
    if log.is_cancelled:
        raise ValidationError("已取消的交接记录不能修改。")
    before = _log_snapshot(log)
    data = dict(validated_data)
    assistants_marker = object()
    assistants = data.pop("assistant_operators", assistants_marker)
    data.pop("_confirmed_duplicate", None)
    for field, value in data.items():
        setattr(log, field, value)
    if log.operator_employee_id:
        log.operator = log.operator_employee.name
    log._allow_planned_counter = True
    log.full_clean(exclude=["produced_mold_count"])
    ProductionDailyLog.objects.filter(pk=log.pk).update(
        production_date=log.production_date,
        operator=log.operator,
        operator_employee=log.operator_employee,
        shift=log.shift,
        cumulative_mold_count=log.cumulative_mold_count,
        cavities_snapshot=log.cavities_snapshot,
        defective_quantity=log.defective_quantity,
        notes=log.notes,
        updated_at=timezone.now(),
    )
    if assistants is not assistants_marker:
        log.assistant_operators.set(assistants)
    _recalculate_counter_segment(run, log.counter_segment)
    invalidate_settlement(run, user)
    log.refresh_from_db()
    _record_audit(
        run,
        user,
        ProductionRecordAudit.Action.UPDATED,
        log=log,
        before=before,
        after=_log_snapshot(log),
    )
    return log


@transaction.atomic
def cancel_counter_log(log, user, reason):
    run = ProductionRun.objects.select_for_update().get(pk=log.run_id)
    log = ProductionDailyLog.objects.select_for_update().get(pk=log.pk, run=run)
    if log.is_cancelled:
        return log
    before = _log_snapshot(log)
    log.is_cancelled = True
    log.cancelled_at = timezone.now()
    log.cancelled_by = user
    log._allow_planned_counter = True
    log.save(update_fields=["is_cancelled", "cancelled_at", "cancelled_by", "updated_at"])
    _recalculate_counter_segment(run, log.counter_segment)
    invalidate_settlement(run, user)
    log.refresh_from_db()
    _record_audit(
        run,
        user,
        ProductionRecordAudit.Action.CANCELLED,
        log=log,
        before=before,
        after=_log_snapshot(log),
        reason=reason,
    )
    return log


@transaction.atomic
def reset_production_counter(run, user, note=""):
    run = ProductionRun.objects.select_for_update().get(pk=run.pk)
    if run.status in (ProductionRun.Status.COMPLETED, ProductionRun.Status.CANCELLED):
        raise ValidationError("已结束任务不能创建新的计数器分段。")
    before = {"counter_segment": run.counter_segment}
    run.counter_segment += 1
    run.save(update_fields=["counter_segment", "updated_at"])
    _record_audit(
        run,
        user,
        ProductionRecordAudit.Action.COUNTER_RESET,
        before=before,
        after={"counter_segment": run.counter_segment},
        reason=note,
    )
    return run


@transaction.atomic
def complete_ledger_task(run, user, *, note="", confirm_below_target=False):
    run = ProductionRun.objects.select_for_update().get(pk=run.pk)
    if not run.is_ledger_only:
        raise ValidationError("实时上机任务请使用正常下机/结束操作。")
    if run.status == ProductionRun.Status.COMPLETED:
        return run
    if run.status == ProductionRun.Status.CANCELLED:
        raise ValidationError("已取消任务不能确认完成。")
    if not run.target_reached and not confirm_below_target:
        raise ValidationError(
            {
                "warnings": [
                    f"当前累计{run.produced_mold_count}模，尚未达到目标"
                    f"{run.planned_mold_count}模；如确需关闭，请填写说明并提交"
                    "confirm_below_target=true。"
                ]
            }
        )
    if not run.target_reached and not str(note or "").strip():
        raise ValidationError({"note": "未达到目标时关闭任务必须填写原因。"})
    before = {"status": run.status, "target_reached": run.target_reached}
    run.status = ProductionRun.Status.COMPLETED
    run.pause_note = str(note or "").strip()
    run.save(update_fields=["status", "pause_note", "updated_at"])
    _record_audit(
        run,
        user,
        ProductionRecordAudit.Action.UPDATED,
        before=before,
        after={"status": run.status, "target_reached": run.target_reached},
        reason=note,
    )
    return run


def _order_qualified_quantity(run):
    runs = (
        ProductionRun.objects.filter(order_id=run.order_id)
        if run.order_id
        else ProductionRun.objects.filter(order_no=run.order_no)
    ).exclude(status=ProductionRun.Status.CANCELLED)
    total = 0
    for sibling in runs.prefetch_related("daily_logs"):
        total += sibling.qualified_production_quantity
    return total


@transaction.atomic
def pause_production_run(
    run,
    user,
    *,
    mode,
    slot=None,
    paused_at=None,
    note="",
    confirm_warnings=False,
):
    run = ProductionRun.objects.select_for_update().select_related(
        "station__machine", "mold"
    ).get(pk=run.pk)
    if run.status != ProductionRun.Status.RUNNING:
        raise ValidationError("只有生产中的任务可以暂停。")
    at = paused_at or timezone.now()
    if at < run.loaded_at:
        raise ValidationError({"paused_at": "暂停时间不能早于上机时间。"})
    before = {"status": run.status, "counter_segment": run.counter_segment}
    if mode == "ON_MACHINE":
        run.status = ProductionRun.Status.PAUSED_ON_MACHINE
        run.paused_at = at
        run.pause_note = str(note or "").strip()
        run.save(update_fields=["status", "paused_at", "pause_note", "updated_at"])
    elif mode == "UNLOADED":
        if run.mold_id:
            if slot is None:
                raise ValidationError({"slot_id": "暂停并下机时必须选择模具归位库位。"})
            run = complete_and_putaway_production_run(
                run,
                user,
                slot=slot,
                unloaded_at=at,
                note=note or f"生产订单 {run.order_no} 暂停并下机",
                confirm_warnings=confirm_warnings,
            )
            run = ProductionRun.objects.select_for_update().get(pk=run.pk)
            run.status = ProductionRun.Status.PAUSED_UNLOADED
            run.paused_at = at
            run.pause_note = str(note or "").strip()
            run.save(update_fields=["status", "paused_at", "pause_note", "updated_at"])
        else:
            run.status = ProductionRun.Status.PAUSED_UNLOADED
            run.paused_at = at
            run.unloaded_at = at
            run.pause_note = str(note or "").strip()
            run.save(
                update_fields=[
                    "status",
                    "paused_at",
                    "unloaded_at",
                    "pause_note",
                    "updated_at",
                ]
            )
    else:
        raise ValidationError({"mode": "无效的暂停方式。"})
    _record_audit(
        run,
        user,
        ProductionRecordAudit.Action.PAUSED,
        before=before,
        after={"status": run.status, "paused_at": at.isoformat()},
        reason=note,
    )
    return run


@transaction.atomic
def resume_production_run(
    run,
    user,
    *,
    station=None,
    mold=None,
    cavities=None,
    planned_mold_count=None,
    save_cavities_as_mold_default=False,
    loaded_at=None,
    note="",
    confirm_warnings=False,
):
    run = ProductionRun.objects.select_for_update().select_related("station", "mold", "order").get(pk=run.pk)
    if run.status not in (
        ProductionRun.Status.PAUSED_ON_MACHINE,
        ProductionRun.Status.PAUSED_UNLOADED,
    ):
        raise ValidationError("只有暂停中的任务可以恢复。")

    if run.status == ProductionRun.Status.PAUSED_ON_MACHINE:
        if station is not None and station.pk != run.station_id:
            raise ValidationError("模具仍在机台时不能更换机台；请先执行暂停并下机。")
        if mold is not None and mold.pk != run.mold_id:
            raise ValidationError("模具仍在机台时不能更换模具；请先执行暂停并下机。")
        before = {"status": run.status}
        run.status = ProductionRun.Status.RUNNING
        run.paused_at = None
        run.pause_note = str(note or "").strip()
        run.save(update_fields=["status", "paused_at", "pause_note", "updated_at"])
        _record_audit(
            run,
            user,
            ProductionRecordAudit.Action.RESUMED,
            before=before,
            after={"status": run.status},
            reason=note,
        )
        return run

    selected_station = station if station is not None else run.station
    selected_mold = mold
    selected_cavities = int(
        cavities
        or (selected_mold.default_cavities if selected_mold and selected_mold.default_cavities else 0)
        or run.cavities
    )
    completed_quantity = _order_qualified_quantity(run)
    remaining_pieces = max(int(run.order_quantity) - completed_quantity, 0)
    suggested_target = max(
        (remaining_pieces + selected_cavities - 1) // selected_cavities,
        1,
    )
    siblings = ProductionRun.objects.filter(
        order_id=run.order_id
    ) if run.order_id else ProductionRun.objects.filter(order_no=run.order_no)
    next_segment = (siblings.aggregate(value=Max("segment_no"))["value"] or 0) + 1
    resumed = ProductionRun(
        station=selected_station,
        order=run.order,
        product_specification=run.product_specification,
        order_no=run.order_no,
        specification=run.specification,
        material=run.material,
        mold=selected_mold,
        order_quantity=run.order_quantity,
        cavities=selected_cavities,
        estimated_defect_rate=run.estimated_defect_rate,
        estimated_defect_mode=run.estimated_defect_mode,
        estimated_defect_quantity=run.estimated_defect_quantity,
        planned_mold_count=planned_mold_count or suggested_target,
        compound_size=run.compound_size,
        strip_weight_kg=run.strip_weight_kg,
        strips_per_batch=run.strips_per_batch,
        curing_seconds=run.curing_seconds,
        estimated_hours=run.estimated_hours,
        status=ProductionRun.Status.PLANNED,
        operator=run.operator,
        unit_price=run.unit_price,
        material_unit_price=run.material_unit_price,
        notes=run.notes,
        continuation_of=run,
        segment_no=next_segment,
        pause_note=str(note or "").strip(),
        created_by=user,
    )
    resumed.save()
    if selected_mold and save_cavities_as_mold_default:
        MoldAsset.objects.filter(pk=selected_mold.pk).update(
            default_cavities=selected_cavities, updated_at=timezone.now()
        )
    if selected_station and selected_mold:
        resumed = start_production_run(
            resumed,
            user,
            loaded_at=loaded_at,
            note=note or f"恢复订单 {run.order_no} 的生产",
            confirm_warnings=confirm_warnings,
        )
    # Historical/manual ledger tasks may omit machine or mold.  They stay
    # planned in the real-time board while still accepting counter entries;
    # this avoids pretending that a physical mold is mounted.
    _record_audit(
        resumed,
        user,
        ProductionRecordAudit.Action.RESUMED,
        before={"previous_run_id": run.pk, "completed_quantity": completed_quantity},
        after={
            "run_id": resumed.pk,
            "planned_mold_count": resumed.planned_mold_count,
            "remaining_pieces": remaining_pieces,
        },
        reason=note,
    )
    return resumed


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
