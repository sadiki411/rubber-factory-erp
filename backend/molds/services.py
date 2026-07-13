import logging

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import (
    Machine,
    MoldAsset,
    MoldMovement,
    Rack,
    RackLevel,
    RackSlot,
    RackZone,
)


logger = logging.getLogger(__name__)

DEFAULT_RACK_LAYOUT_VERSION = 2


class ConfirmationRequired(Exception):
    def __init__(self, warnings):
        self.warnings = warnings
        super().__init__("该操作需要二次确认。")


def _as_bool(value):
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
    return bool(value)


def normalize_rack_config(config):
    try:
        layout_version = int(
            config.get("layout_version", DEFAULT_RACK_LAYOUT_VERSION)
        )
    except (TypeError, ValueError):
        raise ValidationError({"layout_version": "布局版本必须是整数。"})
    if layout_version < 1:
        raise ValidationError({"layout_version": "布局版本必须大于等于1。"})

    try:
        level_count = int(config.get("level_count", config.get("levels")))
    except (TypeError, ValueError):
        raise ValidationError({"level_count": "层数必须是整数。"})
    if not 1 <= level_count <= 30:
        raise ValidationError({"level_count": "层数必须在1到30之间。"})

    raw_zones = config.get("zones") or []
    if not 1 <= len(raw_zones) <= 4:
        raise ValidationError({"zones": "每层必须配置1到4个区域。"})
    normalized_zones = []
    seen_codes = set()
    for index, raw in enumerate(raw_zones):
        code = str(raw.get("code") or ("F" if len(raw_zones) == 1 else chr(65 + index))).strip().upper()
        if not code or len(code) > 10 or code in seen_codes:
            raise ValidationError({"zones": f"区域编码 {code or '(空)'} 无效或重复。"})
        seen_codes.add(code)
        try:
            capacities = sorted({int(value) for value in raw.get("allowed_capacities", [])})
            default_capacity = int(raw.get("default_capacity", raw.get("capacity_mode")))
        except (TypeError, ValueError):
            raise ValidationError({"zones": f"区域 {code} 的容量必须是整数。"})
        if not capacities or capacities[0] < 1 or capacities[-1] > 20:
            raise ValidationError({"zones": f"区域 {code} 至少需要一个1到20之间的容量。"})
        if default_capacity not in capacities:
            raise ValidationError({"zones": f"区域 {code} 的默认容量不在允许容量中。"})

        raw_inactive_levels = raw.get("inactive_levels") or []
        if isinstance(raw_inactive_levels, str):
            raw_inactive_levels = [
                item.strip()
                for item in raw_inactive_levels.replace("，", ",").split(",")
                if item.strip()
            ]
        try:
            inactive_levels = sorted({int(value) for value in raw_inactive_levels})
        except (TypeError, ValueError):
            raise ValidationError({"zones": f"区域 {code} 的停用层必须是整数。"})
        if inactive_levels and (
            inactive_levels[0] < 1 or inactive_levels[-1] > level_count
        ):
            raise ValidationError(
                {"zones": f"区域 {code} 的停用层必须在1到{level_count}之间。"}
            )

        supports_stacking = _as_bool(raw.get("supports_stacking", False))
        default_stacking_enabled = _as_bool(
            raw.get("default_stacking_enabled", False)
        )
        if default_stacking_enabled and not supports_stacking:
            raise ValidationError(
                {"zones": f"区域 {code} 不支持叠放，不能默认开启叠放。"}
            )

        label = str(
            raw.get("label")
            or ("整层" if len(raw_zones) == 1 else f"{code}区")
        ).strip()
        inactive_label = str(raw.get("inactive_label") or "不可用区").strip()
        blocking_reason = str(
            raw.get("blocking_reason") or "该区域不可放置模具。"
        ).strip()
        if not label or len(label) > 50:
            raise ValidationError({"zones": f"区域 {code} 的名称不能为空且最多50个字符。"})
        if inactive_levels and (not inactive_label or len(inactive_label) > 50):
            raise ValidationError(
                {"zones": f"区域 {code} 的停用区域名称不能为空且最多50个字符。"}
            )
        if inactive_levels and (
            not blocking_reason or len(blocking_reason) > 200
        ):
            raise ValidationError(
                {"zones": f"区域 {code} 的禁放原因不能为空且最多200个字符。"}
            )

        normalized_zones.append(
            {
                "code": code,
                "label": label,
                "allowed_capacities": capacities,
                "default_capacity": default_capacity,
                "supports_stacking": supports_stacking,
                "default_stacking_enabled": default_stacking_enabled,
                "inactive_levels": inactive_levels,
                "inactive_label": inactive_label,
                "blocking_reason": blocking_reason,
            }
        )
    return {
        "layout_version": layout_version,
        "level_count": level_count,
        "zones": normalized_zones,
    }


def slot_codes(rack_code, level_no, zone_code, capacity, position_no, stack_level, supports_stacking):
    base = f"{rack_code}-L{level_no:02d}"
    if zone_code != "F":
        base += f"-{zone_code}"
    display = f"{base}-P{position_no:02d}"
    if supports_stacking:
        display += f"-S{stack_level}"
    technical = f"{rack_code}-L{level_no:02d}-{zone_code}-C{capacity}-P{position_no:02d}-S{stack_level}"
    return display, technical


def build_rack_preview(rack_code, config):
    normalized = normalize_rack_config(config)
    levels = []
    for level_no in range(normalized["level_count"], 0, -1):
        zones = []
        for zone_spec in normalized["zones"]:
            inactive = level_no in zone_spec["inactive_levels"]
            stacking_enabled = (
                zone_spec["default_stacking_enabled"] and not inactive
            )
            slots = []
            for position_no in range(1, zone_spec["default_capacity"] + 1):
                stack_levels = (1, 2) if zone_spec["supports_stacking"] else (1,)
                for stack_level in stack_levels:
                    display, technical = slot_codes(
                        rack_code,
                        level_no,
                        zone_spec["code"],
                        zone_spec["default_capacity"],
                        position_no,
                        stack_level,
                        zone_spec["supports_stacking"],
                    )
                    slots.append(
                        {
                            "display_code": display,
                            "technical_code": technical,
                            "position_no": position_no,
                            "stack_level": stack_level,
                            "is_blocked": inactive,
                            "blocking_reason": (
                                zone_spec["blocking_reason"] if inactive else ""
                            ),
                            "is_enabled": (
                                not inactive
                                and (stack_level == 1 or stacking_enabled)
                            ),
                        }
                    )
            zones.append(
                {
                    **zone_spec,
                    "label": (
                        zone_spec["inactive_label"]
                        if inactive
                        else zone_spec["label"]
                    ),
                    "is_active": not inactive,
                    "stacking_enabled": stacking_enabled,
                    "slots": slots,
                }
            )
        levels.append({"level_no": level_no, "zones": zones})
    return {**normalized, "rack_code": rack_code, "levels": levels}


@transaction.atomic
def configure_rack(rack, config, lock_structure=False):
    rack = Rack.objects.select_for_update().get(pk=rack.pk)
    if rack.structure_locked:
        raise ValidationError("该货架已投入使用，不能修改结构。")
    if MoldAsset.objects.filter(current_slot__zone__level__rack=rack).exists():
        raise ValidationError("该货架仍有模具，不能修改结构。")
    normalized = normalize_rack_config(config)
    rack.levels.all().delete()
    for level_no in range(1, normalized["level_count"] + 1):
        level = RackLevel.objects.create(rack=rack, level_no=level_no)
        for zone_spec in normalized["zones"]:
            inactive = level_no in zone_spec["inactive_levels"]
            zone = RackZone(
                level=level,
                code=zone_spec["code"],
                label=(
                    zone_spec["inactive_label"]
                    if inactive
                    else zone_spec["label"]
                ),
                allowed_capacities=zone_spec["allowed_capacities"],
                capacity_mode=zone_spec["default_capacity"],
                supports_stacking=zone_spec["supports_stacking"],
                stacking_enabled=(
                    zone_spec["default_stacking_enabled"] and not inactive
                ),
                is_active=not inactive,
            )
            zone.full_clean()
            zone.save()
            slots = []
            for capacity in zone.allowed_capacities:
                for position_no in range(1, capacity + 1):
                    stack_levels = (1, 2) if zone.supports_stacking else (1,)
                    for stack_level in stack_levels:
                        display, technical = slot_codes(
                            rack.code,
                            level_no,
                            zone.code,
                            capacity,
                            position_no,
                            stack_level,
                            zone.supports_stacking,
                        )
                        slots.append(
                            RackSlot(
                                zone=zone,
                                capacity_mode=capacity,
                                position_no=position_no,
                                stack_level=stack_level,
                                display_code=display,
                                technical_code=technical,
                                is_blocked=inactive,
                                blocking_reason=(
                                    zone_spec["blocking_reason"] if inactive else ""
                                ),
                            )
                        )
            RackSlot.objects.bulk_create(slots)
    rack.is_configured = True
    rack.structure_locked = lock_structure
    rack.layout_version = normalized["layout_version"]
    rack.save(
        update_fields=[
            "is_configured",
            "structure_locked",
            "layout_version",
            "updated_at",
        ]
    )
    return rack


def default_rack_configs():
    def stackable_zone(
        code,
        label,
        capacities,
        *,
        inactive_levels=None,
        inactive_label="不可用区",
        blocking_reason="该区域不可放置模具。",
    ):
        return {
            "code": code,
            "label": label,
            "allowed_capacities": capacities,
            "default_capacity": capacities[0],
            "supports_stacking": True,
            "default_stacking_enabled": False,
            "inactive_levels": inactive_levels or [],
            "inactive_label": inactive_label,
            "blocking_reason": blocking_reason,
        }

    return {
        "J01": {
            "layout_version": DEFAULT_RACK_LAYOUT_VERSION,
            "level_count": 6,
            "zones": [
                stackable_zone("A", "左区", [2, 3]),
                stackable_zone("B", "右区", [2, 3]),
            ],
        },
        "J02": {
            "layout_version": DEFAULT_RACK_LAYOUT_VERSION,
            "level_count": 8,
            "zones": [
                stackable_zone("A", "左区", [2, 3]),
                stackable_zone("B", "右区", [2, 3]),
            ],
        },
        "J03": {
            "layout_version": DEFAULT_RACK_LAYOUT_VERSION,
            "level_count": 6,
            "zones": [stackable_zone("F", "整层", [2, 3])],
        },
        "J04": {
            "layout_version": DEFAULT_RACK_LAYOUT_VERSION,
            "level_count": 6,
            "zones": [stackable_zone("F", "整层", [2, 3])],
        },
        "J05": {
            "layout_version": DEFAULT_RACK_LAYOUT_VERSION,
            "level_count": 4,
            "zones": [
                stackable_zone("A", "左区", [2, 3, 4]),
                stackable_zone("B", "右区", [2, 3, 4]),
            ],
        },
        "J06": {
            "layout_version": DEFAULT_RACK_LAYOUT_VERSION,
            "level_count": 9,
            "zones": [
                stackable_zone(
                    "A",
                    "左区",
                    [2, 3],
                    inactive_levels=[7, 8, 9],
                    inactive_label="杂物区",
                    blocking_reason="该区域用于堆放杂物，不能放置模具。",
                ),
                stackable_zone("B", "中区", [2, 3]),
                stackable_zone(
                    "C",
                    "右区",
                    [2, 3],
                    inactive_levels=[7, 8, 9],
                    inactive_label="杂物区",
                    blocking_reason="该区域用于堆放杂物，不能放置模具。",
                ),
            ],
        },
    }


@transaction.atomic
def seed_default_racks():
    configs = default_rack_configs()
    warnings = []
    for index in range(1, 8):
        code = f"J{index:02d}"
        rack, created = Rack.objects.get_or_create(
            code=code,
            defaults={
                "name": f"{index}号模具架",
                "layout_version": DEFAULT_RACK_LAYOUT_VERSION,
                "is_configured": False,
            },
        )
        if code not in configs:
            if (
                rack.layout_version < DEFAULT_RACK_LAYOUT_VERSION
                and not rack.is_configured
                and not rack.levels.exists()
            ):
                rack.layout_version = DEFAULT_RACK_LAYOUT_VERSION
                rack.save(update_fields=["layout_version", "updated_at"])
            continue

        needs_upgrade = rack.layout_version < DEFAULT_RACK_LAYOUT_VERSION
        needs_initial_config = not rack.is_configured and not rack.levels.exists()
        if not (created or needs_upgrade or needs_initial_config):
            continue

        current_molds_exist = MoldAsset.objects.filter(
            current_slot__zone__level__rack=rack
        ).exists()
        history_exists = (
            MoldMovement.objects.filter(from_slot__zone__level__rack=rack).exists()
            or MoldMovement.objects.filter(to_slot__zone__level__rack=rack).exists()
        )
        if needs_upgrade and (current_molds_exist or history_exists):
            reason = "仍有在库模具" if current_molds_exist else "存在模具流转历史"
            message = (
                f"{code} 当前布局版本为 {rack.layout_version}，因{reason}，"
                f"未自动升级到版本 {DEFAULT_RACK_LAYOUT_VERSION}。"
            )
            warnings.append(message)
            logger.warning(message)
            continue

        if rack.structure_locked:
            rack.structure_locked = False
            rack.save(update_fields=["structure_locked", "updated_at"])
        configure_rack(rack, configs[code], lock_structure=True)

    return warnings


def stacking_warnings(mold, target_slot=None, leaving_current=False):
    warnings = []
    if target_slot and target_slot.stack_level == 2:
        if not target_slot.zone.supports_stacking:
            raise ValidationError("该区域不支持叠放。")
        lower_slot = RackSlot.objects.filter(
            zone=target_slot.zone,
            capacity_mode=target_slot.capacity_mode,
            position_no=target_slot.position_no,
            stack_level=1,
        ).first()
        lower_mold = MoldAsset.objects.filter(current_slot=lower_slot).first() if lower_slot else None
        if not lower_mold:
            warnings.append("上叠位置下方没有模具。")
        elif not lower_mold.allows_stacking:
            warnings.append(f"下层模具 {lower_mold.asset_code} 未标记为允许叠放。")
    current = mold.current_slot
    if leaving_current and current and current.stack_level == 1 and current.zone.supports_stacking:
        upper_slot = RackSlot.objects.filter(
            zone=current.zone,
            capacity_mode=current.capacity_mode,
            position_no=current.position_no,
            stack_level=2,
        ).first()
        upper_mold = MoldAsset.objects.filter(current_slot=upper_slot).first() if upper_slot else None
        if upper_mold:
            warnings.append(f"上叠位置仍有模具 {upper_mold.asset_code}，移动下层模具可能不安全。")
    return warnings


def validate_slot(slot, mold=None):
    if not slot.is_enabled:
        raise ValidationError("目标库位未启用、容量模式不匹配或已禁放。")
    occupant = MoldAsset.objects.filter(current_slot=slot)
    if mold:
        occupant = occupant.exclude(pk=mold.pk)
    if occupant.exists():
        raise ValidationError("目标库位已被占用。")


def validate_active_production_assignment(
    mold,
    *,
    action,
    to_status,
    to_machine=None,
):
    """Keep molds reserved by active production runs on their assigned machine."""

    # Import locally so the lower-level molds app does not create an import cycle
    # while Django is loading the production models that reference MoldAsset.
    from production.models import ProductionRun

    active_run = (
        ProductionRun.objects.select_for_update()
        .select_related("station__machine")
        .filter(mold=mold, status__in=ProductionRun.ACTIVE_STATUSES)
        .first()
    )
    if not active_run:
        return

    assignment = (
        f"活动生产订单 {active_run.order_no}（站位 {active_run.station.code}）"
    )
    if active_run.status == ProductionRun.Status.RUNNING:
        raise ValidationError(
            f"模具已关联{assignment}，当前正在生产，生产结束或取消前不能进行任何流转操作。"
        )

    expected_machine = active_run.station.machine
    if (
        action == MoldMovement.Action.LOAD_MACHINE
        and to_status == MoldAsset.Status.ON_MACHINE
        and expected_machine is not None
        and to_machine is not None
        and to_machine.pk == expected_machine.pk
    ):
        if (
            mold.status == MoldAsset.Status.ON_MACHINE
            and mold.current_machine_id == expected_machine.pk
        ):
            raise ValidationError(
                f"模具已关联{assignment}且已在 {expected_machine.code}，不能重复上机。"
            )
        return

    if expected_machine is None:
        raise ValidationError(
            f"模具已关联{assignment}，但该站位未关联机台，暂不能变更模具位置。"
        )
    raise ValidationError(
        f"模具已关联{assignment}，只能上机到 {expected_machine.code}，"
        "不能移库、归位、标记客户收回或改到其他机台。"
    )


@transaction.atomic
def transition_mold(mold, action, operator, *, slot=None, machine=None, processor=None, note="", confirm_warnings=False):
    mold = MoldAsset.objects.select_for_update().select_related(
        "current_slot__zone__level__rack", "current_machine", "current_processor"
    ).get(pk=mold.pk)
    if not mold.is_active:
        raise ValidationError("已删除模具不能再进行状态或位置操作。")
    from_values = {
        "from_status": mold.status,
        "from_slot": mold.current_slot,
        "from_machine": mold.current_machine,
        "from_processor": mold.current_processor,
    }
    warnings = []

    if action == MoldMovement.Action.PUTAWAY:
        if mold.status == MoldAsset.Status.IN_STOCK:
            raise ValidationError("模具已在库，请使用移库操作。")
        if not slot:
            raise ValidationError("归位必须选择库位。")
        slot = RackSlot.objects.select_for_update().select_related("zone__level__rack").get(pk=slot.pk)
        validate_slot(slot, mold)
        warnings.extend(stacking_warnings(mold, target_slot=slot))
        to_status = MoldAsset.Status.IN_STOCK
        to_slot, to_machine, to_processor = slot, None, None
    elif action == MoldMovement.Action.MOVE:
        if mold.status != MoldAsset.Status.IN_STOCK:
            raise ValidationError("只有在库模具可以移库。")
        if not slot:
            raise ValidationError("移库必须选择目标库位。")
        slot = RackSlot.objects.select_for_update().select_related("zone__level__rack").get(pk=slot.pk)
        if mold.current_slot_id == slot.id:
            raise ValidationError("目标库位与当前库位相同。")
        validate_slot(slot, mold)
        warnings.extend(stacking_warnings(mold, target_slot=slot, leaving_current=True))
        to_status = MoldAsset.Status.IN_STOCK
        to_slot, to_machine, to_processor = slot, None, None
    elif action == MoldMovement.Action.LOAD_MACHINE:
        if not machine:
            raise ValidationError("上机必须选择机台。")
        machine = Machine.objects.select_for_update().get(pk=machine.pk)
        if not machine.is_active:
            raise ValidationError("所选机台已停用。")
        warnings.extend(stacking_warnings(mold, leaving_current=mold.status == MoldAsset.Status.IN_STOCK))
        to_status = MoldAsset.Status.ON_MACHINE
        to_slot, to_machine, to_processor = None, machine, None
    elif action == MoldMovement.Action.SEND_OUT:
        warnings.extend(stacking_warnings(mold, leaving_current=mold.status == MoldAsset.Status.IN_STOCK))
        to_status = MoldAsset.Status.OUTSOURCED
        to_slot, to_machine, to_processor = None, None, None
    else:
        raise ValidationError("不支持的模具操作。")

    validate_active_production_assignment(
        mold,
        action=action,
        to_status=to_status,
        to_machine=to_machine,
    )

    if (
        action == MoldMovement.Action.LOAD_MACHINE
        and mold.status == MoldAsset.Status.ON_MACHINE
        and mold.current_machine_id == to_machine.pk
    ):
        raise ValidationError("模具已在所选机台，不能重复上机。")
    if (
        action == MoldMovement.Action.SEND_OUT
        and mold.status == MoldAsset.Status.OUTSOURCED
    ):
        raise ValidationError("模具已是客户收回状态，无需重复操作。")

    if warnings and not confirm_warnings:
        raise ConfirmationRequired(warnings)

    mold.status = to_status
    mold.current_slot = to_slot
    mold.current_machine = to_machine
    mold.current_processor = to_processor
    mold.status_changed_at = timezone.now()
    try:
        mold.full_clean()
        mold.save(
            update_fields=[
                "status",
                "current_slot",
                "current_machine",
                "current_processor",
                "status_changed_at",
                "updated_at",
            ]
        )
    except IntegrityError as exc:
        raise ValidationError("目标位置刚刚被其他操作占用，请刷新后重试。") from exc

    if to_slot:
        rack = to_slot.zone.level.rack
        if not rack.structure_locked:
            Rack.objects.filter(pk=rack.pk).update(structure_locked=True)

    MoldMovement.objects.create(
        mold=mold,
        action=action,
        to_status=to_status,
        to_slot=to_slot,
        to_machine=to_machine,
        to_processor=to_processor,
        note=note,
        operator=operator,
        **from_values,
    )
    return mold, warnings


@transaction.atomic
def archive_mold(mold, operator, *, note="", confirm_warnings=False):
    """Soft-delete an erroneous mold entry while preserving its audit trail."""

    from production.models import ProductionRun

    mold = MoldAsset.objects.select_for_update().select_related(
        "current_slot__zone__level__rack", "current_machine", "current_processor"
    ).get(pk=mold.pk)
    if not mold.is_active:
        raise ValidationError("该模具已经删除。")

    active_run = (
        ProductionRun.objects.select_for_update()
        .select_related("station")
        .filter(mold=mold, status__in=ProductionRun.ACTIVE_STATUSES)
        .first()
    )
    if active_run:
        raise ValidationError(
            f"模具已关联活动生产订单 {active_run.order_no}（站位 {active_run.station.code}），"
            "请先完成或取消生产任务后再删除。"
        )

    warnings = stacking_warnings(
        mold,
        leaving_current=mold.status == MoldAsset.Status.IN_STOCK,
    )
    if warnings and not confirm_warnings:
        raise ConfirmationRequired(warnings)

    from_values = {
        "from_status": mold.status,
        "from_slot": mold.current_slot,
        "from_machine": mold.current_machine,
        "from_processor": mold.current_processor,
    }
    mold.current_slot = None
    mold.current_machine = None
    mold.current_processor = None
    mold.is_active = False
    mold.status_changed_at = timezone.now()
    mold.full_clean()
    mold.save(
        update_fields=[
            "current_slot",
            "current_machine",
            "current_processor",
            "is_active",
            "status_changed_at",
            "updated_at",
        ]
    )

    MoldMovement.objects.create(
        mold=mold,
        action=MoldMovement.Action.DELETE,
        to_status=mold.status,
        note=str(note or "").strip() or "删除误录模具",
        operator=operator,
        **from_values,
    )
    return mold, warnings


@transaction.atomic
def switch_zone_capacity(zone, capacity):
    zone = RackZone.objects.select_for_update().get(pk=zone.pk)
    if not zone.is_active:
        raise ValidationError("停用区域不能切换容量模式。")
    try:
        capacity = int(capacity)
    except (TypeError, ValueError):
        raise ValidationError("容量必须是整数。")
    if capacity not in zone.allowed_capacities:
        raise ValidationError("目标容量不属于该区域允许的容量。")
    if capacity == zone.capacity_mode:
        return zone
    if MoldAsset.objects.filter(current_slot__zone=zone).exists():
        raise ValidationError("该区域仍有模具，不能切换容量。")
    zone.capacity_mode = capacity
    zone.save(update_fields=["capacity_mode"])
    return zone


@transaction.atomic
def switch_zone_stacking(zone, enabled):
    zone = RackZone.objects.select_for_update().get(pk=zone.pk)
    if not zone.is_active:
        raise ValidationError("停用区域不能切换叠放状态。")

    enabled = _as_bool(enabled)
    if enabled and not zone.supports_stacking:
        raise ValidationError("该区域物理结构不支持叠放。")
    if enabled == zone.stacking_enabled:
        return zone
    if not enabled and MoldAsset.objects.filter(
        current_slot__zone=zone,
        current_slot__stack_level=2,
    ).exists():
        raise ValidationError("该区域上叠位仍有模具，不能关闭叠放。")

    zone.stacking_enabled = enabled
    zone.full_clean()
    zone.save(update_fields=["stacking_enabled"])
    return zone
