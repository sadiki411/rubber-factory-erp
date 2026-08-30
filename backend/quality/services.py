from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable

from django.db import IntegrityError, transaction
from django.db.models import Max, Q, Sum
from django.utils import timezone

from .models import (
    DefectReason,
    ProcessCard,
    ProcessCardUnitBinding,
    QualityEmployee,
    QualityOrder,
    QualityReworkCase,
    QualityReturnAllocation,
    QualityShipment,
    QualityShipmentBatch,
    QualityShipmentLine,
)


PENDING_RETURN_STATUSES = (
    QualityReworkCase.Status.OPEN,
    QualityReworkCase.Status.PROCESSING,
    QualityReworkCase.Status.WAITING_REINSPECTION,
    QualityReworkCase.Status.WAITING_REWORK,
)


def legacy_reason_category(reason: DefectReason | None) -> str:
    if reason is None:
        return "OTHER"
    return {
        "STICKING": "STICKING",
        "DIMENSION": "DIMENSION",
        "MATERIAL": "MATERIAL",
        "MIXED": "MIXED",
        "PACKAGING": "PACKAGING",
    }.get(reason.code, "APPEARANCE" if reason.code != "OTHER" else "OTHER")


def shipment_line_piece_quantity(line: QualityShipmentLine) -> int:
    """Return the immutable piece count, deriving legacy rows from weight."""

    quantity = line.piece_quantity
    unit = line.unit_weight_g_snapshot or (
        line.process_card.unit_weight_g if line.process_card_id else None
    )
    if quantity is None and unit and line.net_weight_kg:
        quantity = int(
            (
                Decimal(line.net_weight_kg)
                * Decimal("1000")
                / Decimal(unit)
            ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        )
    return max(0, int(quantity or 0))


def delivered_quantities_by_order(
    order_ids: Iterable[int], *, exclude_batch_id: int | None = None
) -> dict[int, int]:
    """Use one delivery balance for candidates, orders and auto-allocation.

    Confirmed weighted rows and legacy quantity shipments both consume an
    order.  Valid customer returns release the corresponding quantity again;
    draft/void weighted batches and internal rework never affect the balance.
    """

    ids = {int(value) for value in order_ids if value is not None}
    delivered = {order_id: 0 for order_id in ids}
    if not ids:
        return delivered

    legacy_shipments = QualityShipment.objects.filter(
        order_id__in=ids
    ).prefetch_related("reworks")
    for shipment in legacy_shipments:
        returned = sum(
            int(rework.returned_quantity or 0)
            for rework in shipment.reworks.all()
        )
        delivered[shipment.order_id] += max(
            0, int(shipment.shipped_quantity or 0) - returned
        )

    weighted_lines = (
        QualityShipmentLine.objects.filter(
            batch__status=QualityShipmentBatch.Status.CONFIRMED
        )
        .filter(Q(order_id__in=ids) | Q(process_card__order_id__in=ids))
        .select_related("order", "process_card__order")
        .prefetch_related("rework_cases__shipment_allocations", "rework_allocations__case")
        .distinct()
    )
    if exclude_batch_id is not None:
        weighted_lines = weighted_lines.exclude(batch_id=exclude_batch_id)

    for line in weighted_lines:
        order_id = line.order_id or (
            line.process_card.order_id if line.process_card_id else None
        )
        if order_id not in delivered:
            continue
        returned = 0
        unit = line.unit_weight_g_snapshot or (
            line.process_card.unit_weight_g if line.process_card_id else None
        )
        for case in line.rework_cases.all():
            if (
                case.origin != QualityReworkCase.Origin.CUSTOMER_RETURN
                or case.status == QualityReworkCase.Status.CANCELLED
                or any(
                    allocation.shipment_line_id == line.pk
                    for allocation in case.shipment_allocations.all()
                )
            ):
                continue
            if case.affected_quantity is not None:
                returned += int(case.affected_quantity)
            elif case.affected_weight_kg is not None and unit:
                returned += int(
                    (
                        Decimal(case.affected_weight_kg)
                        * Decimal("1000")
                        / Decimal(unit)
                    ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
                )
        returned += sum(
            int(allocation.piece_quantity or 0)
            for allocation in line.rework_allocations.all()
            if (
                allocation.case.origin == QualityReworkCase.Origin.CUSTOMER_RETURN
                and allocation.case.status != QualityReworkCase.Status.CANCELLED
            )
        )
        delivered[order_id] += max(
            0, shipment_line_piece_quantity(line) - returned
        )
    return delivered


def _allocation_order_key(order: QualityOrder):
    return (
        order.due_date is None,
        order.due_date or date.max,
        order.order_date is None,
        order.order_date or date.max,
        order.pk,
    )


def build_order_allocation_plan(
    source_order: QualityOrder,
    requested_quantity: int,
    *,
    lock: bool = False,
) -> dict:
    """Allocate one direct-order shipment without changing the database.

    The source order is filled first.  Excess pieces fill open orders with the
    exact same specification and material.  After every matching order is
    full, any residue returns to the source order and is explicitly allowed to
    exceed its ordered quantity.
    """

    requested = int(requested_quantity or 0)
    if requested < 1:
        raise ValueError("出货数量必须大于0。")
    if source_order.status != QualityOrder.Status.OPEN:
        raise ValueError("所选订单已完成或已取消，不能再确认出货。")

    specification = str(source_order.specification or "").strip()
    material = str(source_order.material or "").strip()
    matching_query = QualityOrder.objects.none()
    # Empty values are not a product identity.  Treating every blank-material
    # or blank-specification order as interchangeable can cross-allocate
    # unrelated products, so those shipments may only overflow the source.
    if specification and material:
        matching_query = QualityOrder.objects.filter(
            status=QualityOrder.Status.OPEN,
            specification__iexact=specification,
            material__iexact=material,
        ).exclude(pk=source_order.pk)
    matching_ids = list(matching_query.values_list("pk", flat=True))
    all_ids = sorted({source_order.pk, *matching_ids})
    locked_query = QualityOrder.objects.filter(pk__in=all_ids).select_related(
        "product_specification"
    )
    if lock:
        locked_query = locked_query.select_for_update()
    # Always acquire locks in primary-key order.  Allocation order is applied
    # afterwards, preventing two simultaneous confirmations from deadlocking.
    orders_by_id = {
        order.pk: order for order in locked_query.order_by("pk")
    }
    source = orders_by_id[source_order.pk]
    if source.status != QualityOrder.Status.OPEN:
        raise ValueError("所选订单已完成或已取消，不能再确认出货。")
    locked_specification = str(source.specification or "").strip().casefold()
    locked_material = str(source.material or "").strip().casefold()
    # ``matching_ids`` is intentionally read before acquiring the stable lock
    # set.  Re-check every business predicate on the locked rows: an operator
    # may complete/cancel or edit a candidate while this confirmation waits.
    # A stale row must never receive an automatic allocation.
    candidates = sorted(
        (
            orders_by_id[order_id]
            for order_id in matching_ids
            if locked_specification
            and locked_material
            and order_id in orders_by_id
            and orders_by_id[order_id].status == QualityOrder.Status.OPEN
            and str(orders_by_id[order_id].specification or "").strip().casefold()
            == locked_specification
            and str(orders_by_id[order_id].material or "").strip().casefold()
            == locked_material
        ),
        key=_allocation_order_key,
    )
    delivered = delivered_quantities_by_order(all_ids)

    allocations: list[dict] = []
    source_remaining = max(
        0, int(source.order_quantity or 0) - delivered.get(source.pk, 0)
    )
    source_base = min(requested, source_remaining)
    if source_base:
        allocations.append(
            {
                "order": source,
                "remaining_before": source_remaining,
                "allocated_quantity": source_base,
                "remaining_after": source_remaining - source_base,
                "is_source": True,
                "is_overflow": False,
            }
        )

    unallocated = requested - source_base
    matching_allocated = 0
    for order in candidates:
        if unallocated <= 0:
            break
        remaining = max(
            0, int(order.order_quantity or 0) - delivered.get(order.pk, 0)
        )
        if remaining <= 0:
            continue
        quantity = min(unallocated, remaining)
        allocations.append(
            {
                "order": order,
                "remaining_before": remaining,
                "allocated_quantity": quantity,
                "remaining_after": remaining - quantity,
                "is_source": False,
                "is_overflow": False,
            }
        )
        matching_allocated += quantity
        unallocated -= quantity

    overflow_quantity = unallocated
    if overflow_quantity:
        source_item = next(
            (item for item in allocations if item["order"].pk == source.pk),
            None,
        )
        if source_item is None:
            allocations.insert(
                0,
                {
                    "order": source,
                    "remaining_before": source_remaining,
                    "allocated_quantity": overflow_quantity,
                    "remaining_after": 0,
                    "is_source": True,
                    "is_overflow": True,
                },
            )
        else:
            source_item["allocated_quantity"] += overflow_quantity
            source_item["remaining_after"] = 0
            source_item["is_overflow"] = True

    return {
        "source_order": source,
        "requested_quantity": requested,
        "specification": source.specification,
        "material": source.material,
        "allocations": allocations,
        "matching_allocated_quantity": matching_allocated,
        "overflow_quantity": overflow_quantity,
        "total_allocated_quantity": sum(
            item["allocated_quantity"] for item in allocations
        ),
    }


def serialize_order_allocation_plan(plan: dict) -> dict:
    """Return the public allocation-preview representation."""

    source = plan["source_order"]
    return {
        "source_order_id": source.pk,
        "requested_quantity": plan["requested_quantity"],
        "specification": plan["specification"],
        "material": plan["material"],
        "allocations": [
            {
                "order_id": item["order"].pk,
                "order_no": item["order"].order_no,
                "item_no": item["order"].item_no,
                "due_date": item["order"].due_date,
                "remaining_before": item["remaining_before"],
                "allocated_quantity": item["allocated_quantity"],
                "remaining_after": item["remaining_after"],
                "is_source": item["is_source"],
                "is_overflow": item["is_overflow"],
            }
            for item in plan["allocations"]
        ],
        "matching_allocated_quantity": plan["matching_allocated_quantity"],
        "overflow_quantity": plan["overflow_quantity"],
        "total_allocated_quantity": plan["total_allocated_quantity"],
    }


def _unique(values) -> list:
    result = []
    seen = set()
    for value in values:
        if value in (None, "") or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _return_group(
    batch: QualityShipmentBatch,
    lines: list[QualityShipmentLine],
    *,
    first_unit_no: int,
    repeat_count: int,
    single_weight: Decimal,
    pieces_per_batch: int,
) -> dict:
    return {
        "batch": batch,
        "lines": lines,
        "first_unit_no": first_unit_no,
        "last_unit_no": first_unit_no + repeat_count - 1,
        "total_batches": repeat_count,
        "single_batch_net_weight_kg": Decimal(single_weight).quantize(
            Decimal("0.001"), rounding=ROUND_HALF_UP
        ),
        "pieces_per_batch": int(pieces_per_batch),
    }


def shipment_return_groups(
    batch: QualityShipmentBatch,
    *,
    lines: list[QualityShipmentLine] | None = None,
) -> list[dict]:
    """Describe the physical equal-weight units represented by a shipment.

    A direct-order shipment may have been split into several logical order
    lines during automatic allocation.  Batch-header repeat facts remain the
    authoritative description of its physical scale readings, so those lines
    stay in one group.  Other multi-line documents are represented line by
    line with globally unique unit numbers inside the shipment.
    """

    if lines is None:
        lines = list(
            batch.lines.select_related(
                "order", "process_card__order", "product_specification"
            ).order_by("id")
        )
    else:
        lines = sorted(lines, key=lambda item: item.pk)
    if not lines:
        return []

    header_repeat = int(batch.product_batch_count or 0)
    header_weight = batch.single_batch_net_weight_kg
    header_is_physical_group = bool(
        header_repeat > 0
        and header_weight
        and (
            len(lines) == 1
            or all(not line.process_card_id for line in lines)
        )
    )
    if header_is_physical_group:
        total_pieces = sum(shipment_line_piece_quantity(line) for line in lines)
        pieces = int(batch.pieces_per_batch or 0)
        if pieces < 1 and total_pieces and total_pieces % header_repeat == 0:
            pieces = total_pieces // header_repeat
        if pieces < 1 and batch.unit_weight_g:
            pieces = int(
                (
                    Decimal(header_weight)
                    * Decimal("1000")
                    / Decimal(batch.unit_weight_g)
                ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            )
        if pieces > 0 and total_pieces == pieces * header_repeat:
            return [
                _return_group(
                    batch,
                    lines,
                    first_unit_no=1,
                    repeat_count=header_repeat,
                    single_weight=Decimal(header_weight),
                    pieces_per_batch=pieces,
                )
            ]

    groups = []
    next_unit = 1
    for line in lines:
        repeat_count = int(line.product_batch_count or 1)
        total_pieces = shipment_line_piece_quantity(line)
        pieces = int(line.pieces_per_batch or 0)
        if pieces < 1 and total_pieces and total_pieces % repeat_count == 0:
            pieces = total_pieces // repeat_count
        single_weight = line.single_batch_net_weight_kg
        if single_weight is None and repeat_count == 1:
            single_weight = line.net_weight_kg
        if single_weight is None and repeat_count > 0:
            single_weight = (
                Decimal(line.net_weight_kg) / Decimal(repeat_count)
            ).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
        if pieces < 1 or not single_weight or Decimal(single_weight) <= 0:
            continue
        if total_pieces != pieces * repeat_count:
            # Historical rows without reliable repeat snapshots are exposed as
            # one whole-line unit instead of inventing physical sub-batches.
            repeat_count = 1
            pieces = total_pieces
            single_weight = line.net_weight_kg
        if pieces < 1:
            continue
        groups.append(
            _return_group(
                batch,
                [line],
                first_unit_no=next_unit,
                repeat_count=repeat_count,
                single_weight=Decimal(single_weight),
                pieces_per_batch=pieces,
            )
        )
        next_unit += repeat_count
    return groups


def _reserved_return_units(
    batch: QualityShipmentBatch,
    groups: list[dict],
    *,
    cases: list[QualityReworkCase] | None = None,
) -> dict[int, QualityReworkCase]:
    """Reserve explicit units and safely infer legacy whole-batch returns.

    Pre-upgrade cases have no physical unit number.  If their quantity or
    weight is an exact multiple of a group's one-batch facts, reserve that many
    lowest free slots.  Ambiguous history is fail-safe: reserve the entire
    source group so it cannot be selected and returned twice after upgrade.
    """

    valid_units = [
        value
        for group in groups
        for value in range(group["first_unit_no"], group["last_unit_no"] + 1)
    ]
    if cases is None:
        cases = list(
            batch.rework_cases.filter(
                origin=QualityReworkCase.Origin.CUSTOMER_RETURN
            )
            .exclude(status=QualityReworkCase.Status.CANCELLED)
            .order_by("id")
        )
    else:
        cases = sorted(
            (
                case
                for case in cases
                if case.origin == QualityReworkCase.Origin.CUSTOMER_RETURN
                and case.status != QualityReworkCase.Status.CANCELLED
            ),
            key=lambda case: case.pk,
        )
    reserved: dict[int, QualityReworkCase] = {}
    historical = []
    valid_set = set(valid_units)
    for case in cases:
        unit_no = case.shipment_unit_no
        if unit_no is not None and unit_no in valid_set and unit_no not in reserved:
            reserved[unit_no] = case
        elif unit_no is None:
            historical.append(case)
    for case in historical:
        source_group = next(
            (
                group
                for group in groups
                if case.shipment_line_id
                and any(
                    line.pk == case.shipment_line_id for line in group["lines"]
                )
            ),
            groups[0] if len(groups) == 1 else None,
        )
        if source_group is None:
            # With no reliable group identity, fail closed for all units.
            target_units = valid_units
        else:
            group_units = list(
                range(
                    source_group["first_unit_no"],
                    source_group["last_unit_no"] + 1,
                )
            )
            inferred_counts = []
            affected_quantity = case.affected_quantity
            per_batch_quantity = int(source_group["pieces_per_batch"] or 0)
            if affected_quantity is not None and per_batch_quantity > 0:
                quotient, remainder = divmod(
                    int(affected_quantity), per_batch_quantity
                )
                if quotient > 0 and remainder == 0:
                    inferred_counts.append(quotient)
                else:
                    inferred_counts.append(None)
            affected_weight = case.affected_weight_kg
            per_batch_weight = Decimal(source_group["single_batch_net_weight_kg"])
            if affected_weight is not None and per_batch_weight > 0:
                ratio = Decimal(affected_weight) / per_batch_weight
                nearest = int(ratio.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
                # Both stored values have three-decimal precision.  One gram
                # tolerance covers harmless historical rounding but never a
                # genuinely partial returned batch.
                if nearest > 0 and abs(
                    Decimal(affected_weight) - per_batch_weight * nearest
                ) <= Decimal("0.001"):
                    inferred_counts.append(nearest)
                else:
                    inferred_counts.append(None)
            reliable = (
                bool(inferred_counts)
                and None not in inferred_counts
                and len(set(inferred_counts)) == 1
                and 1 <= inferred_counts[0] <= len(group_units)
            )
            if reliable:
                target_units = [
                    value for value in group_units if value not in reserved
                ][: inferred_counts[0]]
            else:
                target_units = group_units
        for value in target_units:
            if value not in reserved:
                reserved[value] = case
    return reserved


def _group_orders(group: dict) -> list[QualityOrder]:
    orders = []
    seen = set()
    for line in group["lines"]:
        order = line.order or (
            line.process_card.order if line.process_card_id else None
        )
        if order is not None and order.pk not in seen:
            seen.add(order.pk)
            orders.append(order)
    return orders


def serialize_returnable_group(
    group: dict,
    *,
    reserved: dict[int, QualityReworkCase],
) -> dict | None:
    batch = group["batch"]
    available = [
        value
        for value in range(group["first_unit_no"], group["last_unit_no"] + 1)
        if value not in reserved
    ]
    if not available:
        return None
    lines = group["lines"]
    orders = _group_orders(group)
    inspectors = list(batch.inspectors.all())
    if not inspectors and batch.inspector_id:
        inspectors = [batch.inspector]
    product_names = _unique(
        (line.order.product_name if line.order_id else "")
        or (
            line.process_card.product_name_snapshot
            if line.process_card_id
            else ""
        )
        or batch.product_name_snapshot
        for line in lines
    )
    specifications = _unique(
        line.specification_snapshot
        or (line.order.specification if line.order_id else "")
        or (
            line.process_card.specification_snapshot
            if line.process_card_id
            else ""
        )
        or batch.specification_snapshot
        for line in lines
    )
    materials = _unique(
        line.material_snapshot
        or (line.order.material if line.order_id else "")
        or (
            line.process_card.material_snapshot
            if line.process_card_id
            else ""
        )
        or batch.material_snapshot
        for line in lines
    )
    line_rows = []
    for line in lines:
        order = line.order or (
            line.process_card.order if line.process_card_id else None
        )
        line_rows.append(
            {
                "id": line.pk,
                "shipment_line_id": line.pk,
                "order_id": order.pk if order else None,
                "order_no": order.order_no if order else "",
                "item_no": order.item_no if order else "",
                "process_card_id": line.process_card_id,
                "process_card_no": (
                    line.process_card.card_no if line.process_card_id else ""
                ),
                "card_no": (
                    line.process_card.card_no if line.process_card_id else ""
                ),
                "piece_quantity": shipment_line_piece_quantity(line),
                "net_weight_kg": format(Decimal(line.net_weight_kg), ".3f"),
            }
        )
    returned_in_group = [
        value
        for value in range(group["first_unit_no"], group["last_unit_no"] + 1)
        if value in reserved
    ]
    group_cases = {
        reserved[value].pk: reserved[value]
        for value in returned_in_group
    }.values()
    rework_count = 0
    for case in group_cases:
        prefetched_attempts = getattr(case, "_prefetched_objects_cache", {}).get(
            "attempts"
        )
        attempt_count = (
            len(prefetched_attempts)
            if prefetched_attempts is not None
            else case.attempts.count()
        )
        rework_count += max(1, attempt_count)
    order_nos = _unique(order.order_no for order in orders)
    item_nos = _unique(order.item_no for order in orders)
    return {
        "key": f"WEIGHTED:{batch.pk}:{group['first_unit_no']}",
        "source_type": "WEIGHTED",
        "shipment_batch_id": batch.pk,
        "shipment_line_id": lines[0].pk if lines else None,
        "shipment_no": batch.shipment_no,
        "shipment_date": (
            batch.shipment_date.isoformat() if batch.shipment_date else None
        ),
        "order_ids": [order.pk for order in orders],
        "order_no": " / ".join(order_nos),
        "order_nos": order_nos,
        "item_no": " / ".join(item_nos),
        "item_nos": item_nos,
        "product_name": " / ".join(product_names),
        "product_names": product_names,
        "specification": " / ".join(specifications),
        "specifications": specifications,
        "material": " / ".join(materials),
        "materials": materials,
        "single_batch_net_weight_kg": format(
            group["single_batch_net_weight_kg"], ".3f"
        ),
        "pieces_per_batch": group["pieces_per_batch"],
        "total_batches": group["total_batches"],
        "available_batches": len(available),
        "available_batch_numbers": available,
        "returned_batches": len(returned_in_group),
        "returned_batch_numbers": returned_in_group,
        "rework_count": rework_count,
        "next_return_no": available[0],
        "inspectors": [
            {
                "id": inspector.pk,
                "employee_no": inspector.employee_no,
                "name": inspector.name,
            }
            for inspector in inspectors
        ],
        "lines": line_rows,
    }


def returnable_groups_for_batch(
    batch: QualityShipmentBatch,
    *,
    lines: list[QualityShipmentLine] | None = None,
    cases: list[QualityReworkCase] | None = None,
) -> list[dict]:
    groups = shipment_return_groups(batch, lines=lines)
    reserved = _reserved_return_units(batch, groups, cases=cases)
    result = []
    for group in groups:
        row = serialize_returnable_group(group, reserved=reserved)
        if row is not None:
            result.append(row)
    return result


def _unit_allocations(group: dict, unit_no: int) -> list[dict]:
    if not (group["first_unit_no"] <= unit_no <= group["last_unit_no"]):
        raise ValueError("所选整批序号不属于该出货记录。")
    local_index = unit_no - group["first_unit_no"]
    pieces = int(group["pieces_per_batch"])
    start = local_index * pieces
    end = start + pieces
    cursor = 0
    allocations = []
    for line in group["lines"]:
        line_pieces = shipment_line_piece_quantity(line)
        line_start = cursor
        line_end = cursor + line_pieces
        overlap = max(0, min(end, line_end) - max(start, line_start))
        if overlap:
            allocations.append(
                {"shipment_line": line, "piece_quantity": int(overlap)}
            )
        cursor = line_end
    if sum(item["piece_quantity"] for item in allocations) != pieces:
        raise ValueError("原出货明细无法还原该整批件数，请先补录正确的出货记录。")
    single_weight = Decimal(group["single_batch_net_weight_kg"])
    remaining_weight = single_weight
    for index, item in enumerate(allocations):
        if index == len(allocations) - 1:
            weight = remaining_weight
        else:
            weight = (
                single_weight
                * Decimal(item["piece_quantity"])
                / Decimal(pieces)
            ).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
            weight = min(max(weight, Decimal("0")), remaining_weight)
        item["net_weight_kg"] = weight
        remaining_weight -= weight
    return allocations


def create_whole_batch_return_case(validated_data: dict) -> QualityReworkCase:
    """Create one return case from a locked, confirmed physical shipment unit."""

    supplied_batch = validated_data.get("shipment_batch")
    unit_no = validated_data.get("shipment_unit_no")
    if supplied_batch is None or unit_no is None:
        raise ValueError("请选择原出货记录和要退回的整批序号。")
    with transaction.atomic():
        batch = (
            QualityShipmentBatch.objects.select_for_update()
            .select_related("inspector")
            .prefetch_related("inspectors")
            .get(pk=supplied_batch.pk)
        )
        if batch.status != QualityShipmentBatch.Status.CONFIRMED:
            raise ValueError("退货只能选择已确认的出货记录。")
        lines = list(
            QualityShipmentLine.objects.select_for_update()
            .select_related(
                "order", "process_card__order", "product_specification"
            )
            .filter(batch=batch)
            .order_by("id")
        )
        active_cases = list(
            QualityReworkCase.objects.select_for_update()
            .filter(
                shipment_batch=batch,
                origin=QualityReworkCase.Origin.CUSTOMER_RETURN,
            )
            .exclude(status=QualityReworkCase.Status.CANCELLED)
            .order_by("id")
        )
        groups = shipment_return_groups(batch, lines=lines)
        group = next(
            (
                item
                for item in groups
                if item["first_unit_no"] <= int(unit_no) <= item["last_unit_no"]
            ),
            None,
        )
        if group is None:
            raise ValueError("所选整批序号不存在，请刷新后重新选择。")
        reserved = _reserved_return_units(batch, groups, cases=active_cases)
        if int(unit_no) in reserved:
            raise ValueError("该整批已经登记退货，请选择其他可退批次。")
        allocations = _unit_allocations(group, int(unit_no))
        representative = allocations[0]["shipment_line"]
        process_card_ids = {
            item["shipment_line"].process_card_id
            for item in allocations
            if item["shipment_line"].process_card_id
        }
        process_card_id = (
            next(iter(process_card_ids)) if len(process_card_ids) == 1 else None
        )
        supplied_process_card = validated_data.get("process_card")
        values = dict(validated_data)
        values.pop("shipment_line", None)
        values.pop("process_card", None)
        values.update(
            {
                "origin": QualityReworkCase.Origin.CUSTOMER_RETURN,
                "shipment_batch": batch,
                "shipment_unit_no": int(unit_no),
                "shipment_line": representative,
                # A card scanned for an originally unbound quick-entry unit
                # is authoritative even though the aggregate shipment line
                # itself has no process_card FK.
                "process_card_id": (
                    supplied_process_card.pk
                    if supplied_process_card is not None
                    else process_card_id
                ),
                "affected_quantity": group["pieces_per_batch"],
                "affected_weight_kg": group["single_batch_net_weight_kg"],
            }
        )
        if values.get("responsible_inspector") is None:
            values["responsible_inspector"] = batch.inspector or next(
                iter(batch.inspectors.all()), None
            )
        try:
            case = QualityReworkCase.objects.create(**values)
            for item in allocations:
                QualityReturnAllocation.objects.create(
                    case=case,
                    shipment_line=item["shipment_line"],
                    piece_quantity=item["piece_quantity"],
                    net_weight_kg=item["net_weight_kg"],
                )
        except IntegrityError as exc:
            raise ValueError("该整批已被其他登记占用，请刷新后重新选择。") from exc
        if supplied_process_card is not None:
            process_card_ids.add(supplied_process_card.pk)
        for card_id in process_card_ids:
            ProcessCard.objects.get(pk=card_id).refresh_shipping_status()
        affected_order_ids = {
            item["shipment_line"].order_id
            or (
                item["shipment_line"].process_card.order_id
                if item["shipment_line"].process_card_id
                else None
            )
            for item in allocations
        }
        sync_order_status_from_delivery(
            affected_order_ids,
            source="CUSTOMER_RETURN",
            operator=values.get("created_by"),
            reason_prefix=f"登记客户退货 {case.case_no}。",
        )
        return case


def serialize_rework_source(case: QualityReworkCase) -> dict | None:
    batch = case.shipment_batch or (
        case.shipment_line.batch if case.shipment_line_id else None
    )
    if batch is None:
        return None
    prefetched_lines = getattr(batch, "_prefetched_objects_cache", {}).get("lines")
    if prefetched_lines is not None:
        lines = sorted(prefetched_lines, key=lambda line: line.pk)
    else:
        lines = list(
            batch.lines.select_related(
                "order", "process_card__order", "product_specification"
            ).order_by("id")
        )
    groups = shipment_return_groups(batch, lines=lines)
    group = next(
        (
            item
            for item in groups
            if case.shipment_unit_no is not None
            and item["first_unit_no"]
            <= case.shipment_unit_no
            <= item["last_unit_no"]
        ),
        None,
    )
    if group is None:
        source_lines = [case.shipment_line] if case.shipment_line_id else lines
        source_lines = [line for line in source_lines if line is not None]
        if not source_lines:
            return None
        group = _return_group(
            batch,
            source_lines,
            first_unit_no=1,
            repeat_count=1,
            single_weight=Decimal(case.affected_weight_kg or source_lines[0].net_weight_kg),
            pieces_per_batch=int(
                case.affected_quantity
                or shipment_line_piece_quantity(source_lines[0])
            ),
        )
    prefetched_cases = getattr(batch, "_prefetched_objects_cache", {}).get(
        "rework_cases"
    )
    reserved = _reserved_return_units(
        batch,
        groups or [group],
        cases=list(prefetched_cases) if prefetched_cases is not None else None,
    )
    row = serialize_returnable_group(group, reserved={})
    if row is None:
        return None
    row["shipment_unit_no"] = case.shipment_unit_no
    row["returned_batches"] = len(
        [
            value
            for value in range(group["first_unit_no"], group["last_unit_no"] + 1)
            if value in reserved
        ]
    )
    return row


# ---------------------------------------------------------------------------
# Scanned process-card / physical return workflow
# ---------------------------------------------------------------------------


def normalize_process_card_code(value: str) -> str:
    """Normalize the literal QR payload used by the customer's process card."""

    return str(value or "").strip().upper()


def active_process_card(card: ProcessCard) -> ProcessCard:
    """Follow an audited replacement chain to its current printable card."""

    seen = set()
    current = card
    while current.pk not in seen:
        seen.add(current.pk)
        replacement = (
            ProcessCard.objects.filter(replaces_id=current.pk)
            .order_by("id")
            .first()
        )
        if replacement is None:
            return current
        current = replacement
    raise ValueError("流程卡补卡关系存在循环，请联系管理员检查数据。")


def find_process_card(code: str, *, lock: bool = False) -> tuple[ProcessCard, ProcessCard]:
    """Return the scanned historical card and its current replacement."""

    normalized = normalize_process_card_code(code)
    if not normalized:
        raise ValueError("请扫描或输入流程卡单号。")
    queryset = ProcessCard.objects.select_related("order", "replaces")
    if lock:
        queryset = queryset.select_for_update()
    card = queryset.filter(card_no__iexact=normalized).first()
    if card is None:
        raise ValueError("该流程卡尚未登记。")
    current = active_process_card(card)
    if current.status == ProcessCard.Status.CANCELLED:
        raise ValueError("该流程卡已作废，且没有可用补卡。")
    return card, current


def _group_for_unit(batch, unit_no, *, lines=None):
    groups = shipment_return_groups(batch, lines=lines)
    group = next(
        (
            value
            for value in groups
            if value["first_unit_no"] <= int(unit_no) <= value["last_unit_no"]
        ),
        None,
    )
    if group is None:
        raise ValueError("所选物理批号不属于该出货记录。")
    return group


def _unit_order_and_weight(group, unit_no, *, requested_order_id=None):
    allocations = _unit_allocations(group, int(unit_no))
    orders = {}
    for item in allocations:
        line = item["shipment_line"]
        order = line.order or (line.process_card.order if line.process_card_id else None)
        if order is not None:
            orders[order.pk] = order
    if requested_order_id is not None:
        try:
            order = orders[int(requested_order_id)]
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("所选订单不属于该物理出货批次。") from exc
    elif len(orders) == 1:
        order = next(iter(orders.values()))
    elif not orders:
        raise ValueError("原出货批次没有可识别订单，请先补录订单。")
    else:
        raise ValueError("该物理批次跨多个订单，请明确选择流程卡所属订单。")
    unit_weight = None
    for item in allocations:
        line = item["shipment_line"]
        if line.unit_weight_g_snapshot:
            unit_weight = line.unit_weight_g_snapshot
            break
        if line.process_card_id and line.process_card.unit_weight_g:
            unit_weight = line.process_card.unit_weight_g
            break
    if unit_weight is None and group["pieces_per_batch"]:
        unit_weight = (
            Decimal(group["single_batch_net_weight_kg"])
            * Decimal("1000")
            / Decimal(group["pieces_per_batch"])
        ).quantize(Decimal("0.00001"), rounding=ROUND_HALF_UP)
    return order, unit_weight


def bind_process_cards_to_batch(
    batch: QualityShipmentBatch,
    card_entries: list[dict],
    *,
    created_by,
) -> list[ProcessCardUnitBinding]:
    """Bind selected physical units while leaving all omitted units unbound.

    The operation is idempotent for the same card/unit pair and transactional
    for continuous mobile scans.  It never guesses a unit number.
    """

    if not isinstance(card_entries, list) or not card_entries:
        raise ValueError("请提交至少一张流程卡。")
    with transaction.atomic():
        locked_batch = (
            QualityShipmentBatch.objects.select_for_update()
            .get(pk=batch.pk)
        )
        if locked_batch.status != QualityShipmentBatch.Status.CONFIRMED:
            raise ValueError("流程卡只能绑定已确认的出货记录。")
        lines = list(
            QualityShipmentLine.objects.select_for_update()
            .select_related("order", "process_card__order")
            .filter(batch=locked_batch)
            .order_by("id")
        )
        normalized_entries = []
        seen_codes = set()
        seen_units = set()
        for raw in card_entries:
            if isinstance(raw, str):
                raise ValueError("每张流程卡都必须明确填写 shipment_unit_no。")
            code = normalize_process_card_code(raw.get("card_no") or raw.get("code"))
            try:
                unit_no = int(raw.get("shipment_unit_no"))
            except (TypeError, ValueError) as exc:
                raise ValueError("每张流程卡都必须明确填写有效物理批号。") from exc
            if not code:
                raise ValueError("流程卡单号不能为空。")
            if code in seen_codes:
                raise ValueError(f"流程卡 {code} 在本次扫描中重复。")
            if unit_no in seen_units:
                raise ValueError(f"物理批号 {unit_no} 在本次扫描中重复。")
            seen_codes.add(code)
            seen_units.add(unit_no)
            group = _group_for_unit(locked_batch, unit_no, lines=lines)
            order, unit_weight = _unit_order_and_weight(
                group,
                unit_no,
                requested_order_id=raw.get("order_id"),
            )
            normalized_entries.append((raw, code, unit_no, group, order, unit_weight))

        results = []
        for raw, code, unit_no, group, order, unit_weight in normalized_entries:
            card = (
                ProcessCard.objects.select_for_update()
                .select_related("order")
                .filter(card_no__iexact=code)
                .first()
            )
            if card is not None:
                card = active_process_card(card)
                if card.card_no != code:
                    raise ValueError(
                        f"旧流程卡 {code} 已由 {card.card_no} 替代，请扫描新卡。"
                    )
                if card.status == ProcessCard.Status.CANCELLED:
                    raise ValueError(f"流程卡 {code} 已作废。")
                if card.order_id != order.pk:
                    raise ValueError(f"流程卡 {code} 已属于其他订单，不能跨订单绑定。")
            else:
                card = ProcessCard.objects.create(
                    card_no=code,
                    qr_text=code,
                    order=order,
                    source_order_no=order.order_no,
                    source_item_no=order.item_no,
                    product_specification_id=order.product_specification_id,
                    product_name_snapshot=order.product_name,
                    product_code_snapshot=order.product_code,
                    specification_snapshot=order.specification,
                    material_snapshot=order.material,
                    quantity=int(group["pieces_per_batch"]),
                    unit_weight_g=unit_weight,
                    received_on=timezone.localdate(),
                    created_by=created_by,
                )
            existing_for_card = (
                ProcessCardUnitBinding.objects.select_for_update()
                .filter(process_card=card)
                .first()
            )
            existing_for_unit = (
                ProcessCardUnitBinding.objects.select_for_update()
                .filter(shipment_batch=locked_batch, shipment_unit_no=unit_no)
                .first()
            )
            if existing_for_card or existing_for_unit:
                if (
                    existing_for_card is not None
                    and existing_for_unit is not None
                    and existing_for_card.pk == existing_for_unit.pk
                ):
                    results.append(existing_for_card)
                    continue
                if existing_for_card:
                    raise ValueError(f"流程卡 {code} 已绑定其他物理批次。")
                raise ValueError(
                    f"物理批号 {unit_no} 已绑定流程卡 {existing_for_unit.process_card.card_no}。"
                )
            try:
                binding = ProcessCardUnitBinding.objects.create(
                        process_card=card,
                        shipment_batch=locked_batch,
                        shipment_unit_no=unit_no,
                        piece_quantity=int(group["pieces_per_batch"]),
                        net_weight_kg=Decimal(group["single_batch_net_weight_kg"]),
                        created_by=created_by,
                    )
                results.append(binding)
                card.refresh_shipping_status()
            except IntegrityError as exc:
                raise ValueError("流程卡或物理批次刚被其他操作绑定，请刷新后重试。") from exc
        return results


def replace_process_card(card: ProcessCard, new_card_no: str, *, created_by, notes=""):
    """Replace a lost card while keeping one physical tracking history."""

    code = normalize_process_card_code(new_card_no)
    if not code:
        raise ValueError("新流程卡单号不能为空。")
    with transaction.atomic():
        cards = list(
            ProcessCard.objects.select_for_update()
            .filter(tracking_id=card.tracking_id)
            .select_related("order")
            .order_by("id")
        )
        if not cards:
            raise ValueError("原流程卡不存在。")
        current = active_process_card(cards[0])
        if current.pk != card.pk:
            raise ValueError(f"该流程卡已由 {current.card_no} 替代，请从当前卡继续补卡。")
        if current.status == ProcessCard.Status.CANCELLED:
            raise ValueError("已作废流程卡不能补卡。")
        if ProcessCard.objects.filter(card_no__iexact=code).exists():
            raise ValueError("新流程卡单号已存在。")
        replacement = ProcessCard.objects.create(
            card_no=code,
            qr_text=code,
            tracking_id=current.tracking_id,
            replaces=current,
            order=current.order,
            source_item_no=current.source_item_no,
            source_order_no=current.source_order_no,
            product_specification=current.product_specification,
            product_name_snapshot=current.product_name_snapshot,
            product_code_snapshot=current.product_code_snapshot,
            formula_code_snapshot=current.formula_code_snapshot,
            specification_snapshot=current.specification_snapshot,
            material_snapshot=current.material_snapshot,
            customer_snapshot=current.customer_snapshot,
            department_snapshot=current.department_snapshot,
            special_requirements=current.special_requirements,
            material_issue_weight_kg=current.material_issue_weight_kg,
            reprint_count=current.reprint_count + 1,
            demand_date=current.demand_date,
            quantity=current.quantity,
            unit_weight_config=current.unit_weight_config,
            unit_weight_g=current.unit_weight_g,
            sample_count_snapshot=current.sample_count_snapshot,
            sample_total_weight_g_snapshot=current.sample_total_weight_g_snapshot,
            measured_on_snapshot=current.measured_on_snapshot,
            mold_model_code_snapshot=current.mold_model_code_snapshot,
            received_on=timezone.localdate(),
            notes="\n".join(value for value in (current.notes, str(notes or "").strip()) if value),
            raw_data={**(current.raw_data or {}), "replacement_of": current.card_no},
            created_by=created_by,
        )
        ProcessCard.objects.filter(pk=current.pk).update(
            status=ProcessCard.Status.REPLACED,
            updated_at=timezone.now(),
        )
        binding = (
            ProcessCardUnitBinding.objects.select_for_update()
            .filter(process_card=current)
            .first()
        )
        if binding:
            binding.process_card = replacement
            binding.save(update_fields=["process_card", "updated_at"])
        replacement.refresh_shipping_status()
        return replacement


def create_scanned_return(
    *,
    card_no: str,
    created_by,
    shipment_batch_id=None,
    shipment_unit_no=None,
    order_id=None,
    **case_values,
) -> QualityReworkCase:
    """Scan one card and create the next return of that physical batch."""

    code = normalize_process_card_code(card_no)
    if not code:
        raise ValueError("请扫描流程卡二维码。")
    with transaction.atomic():
        card = (
            ProcessCard.objects.select_for_update()
            .filter(card_no__iexact=code)
            .first()
        )
        if card is None:
            if shipment_batch_id in (None, "") or shipment_unit_no in (None, ""):
                raise ValueError("该流程卡尚未绑定，请先选择一次原出货记录和物理批号。")
            try:
                batch = QualityShipmentBatch.objects.get(pk=int(shipment_batch_id))
            except (QualityShipmentBatch.DoesNotExist, TypeError, ValueError) as exc:
                raise ValueError("所选原出货记录不存在。") from exc
            bindings = bind_process_cards_to_batch(
                batch,
                [{
                    "card_no": code,
                    "shipment_unit_no": shipment_unit_no,
                    "order_id": order_id,
                }],
                created_by=created_by,
            )
            card = bindings[0].process_card
        else:
            card = active_process_card(card)
            if card.card_no != code:
                raise ValueError(f"旧流程卡已作废，请改扫补卡 {card.card_no}。")
        binding = (
            ProcessCardUnitBinding.objects.select_for_update()
            .select_related("shipment_batch")
            .filter(process_card=card)
            .first()
        )
        if binding is None:
            if shipment_batch_id in (None, "") or shipment_unit_no in (None, ""):
                raise ValueError("该流程卡尚未绑定，请先选择一次原出货记录和物理批号。")
            batch = QualityShipmentBatch.objects.get(pk=int(shipment_batch_id))
            binding = bind_process_cards_to_batch(
                batch,
                [{
                    "card_no": code,
                    "shipment_unit_no": shipment_unit_no,
                    "order_id": order_id,
                }],
                created_by=created_by,
            )[0]
        pending = (
            QualityReworkCase.objects.select_for_update()
            .filter(
                process_card__tracking_id=card.tracking_id,
                origin=QualityReworkCase.Origin.CUSTOMER_RETURN,
                status__in=PENDING_RETURN_STATUSES,
            )
            .first()
        )
        if pending:
            raise ValueError(
                f"该流程卡已有待处理退货（第{pending.return_round or 1}次），不能重复扫码。"
            )
        previous_round = (
            QualityReworkCase.objects.select_for_update()
            .filter(
                process_card__tracking_id=card.tracking_id,
                origin=QualityReworkCase.Origin.CUSTOMER_RETURN,
            )
            .exclude(status=QualityReworkCase.Status.CANCELLED)
            .aggregate(value=Max("return_round"))["value"]
            or 0
        )
        QualityReworkCase.objects.filter(
            process_card__tracking_id=card.tracking_id,
            origin=QualityReworkCase.Origin.CUSTOMER_RETURN,
            is_current_return=True,
        ).update(is_current_return=False, updated_at=timezone.now())
        values = {
            "origin": QualityReworkCase.Origin.CUSTOMER_RETURN,
            "process_card": card,
            "shipment_batch": binding.shipment_batch,
            "shipment_unit_no": binding.shipment_unit_no,
            "status": QualityReworkCase.Status.WAITING_REWORK,
            "return_round": int(previous_round) + 1,
            "is_current_return": True,
            "created_by": created_by,
            **case_values,
        }
        primary_reason = values.pop("primary_reason", None)
        secondary_reasons = values.pop("secondary_reasons", [])
        inspectors = values.pop("inspectors", [])
        if primary_reason is None:
            requested_category = values.get("reason_category")
            reason_code = {
                "STICKING": "STICKING",
                "DIMENSION": "DIMENSION",
                "MATERIAL": "MATERIAL",
                "MIXED": "MIXED",
                "PACKAGING": "PACKAGING",
            }.get(requested_category, "OTHER")
            primary_reason = DefectReason.objects.filter(
                code=reason_code, is_active=True
            ).first()
        if primary_reason and any(
            item.pk == primary_reason.pk for item in secondary_reasons
        ):
            raise ValueError("主要原因不能同时作为次要问题标签。")
        if primary_reason is not None and "reason_category" not in values:
            values["reason_category"] = legacy_reason_category(primary_reason)
        try:
            case = create_whole_batch_return_case(values)
        except IntegrityError as exc:
            raise ValueError("该流程卡刚被其他操作登记退货，请刷新后重试。") from exc
        if primary_reason is not None:
            case.primary_reason = primary_reason
            case.save(update_fields=["primary_reason", "updated_at"])
        if secondary_reasons:
            case.secondary_reasons.set(secondary_reasons)
        if inspectors:
            case.inspectors.set(inspectors)
            if case.responsible_inspector_id is None:
                case.responsible_inspector = inspectors[0]
                case.save(update_fields=["responsible_inspector", "updated_at"])
        return case


def bind_existing_return_to_card(case, card_no, *, created_by, order_id=None):
    """Explicitly bind preserved no-card history; never infer a source unit."""

    with transaction.atomic():
        locked = QualityReworkCase.objects.select_for_update().get(pk=case.pk)
        if locked.process_card_id:
            scanned, current = find_process_card(card_no, lock=True)
            if current.tracking_id != locked.process_card.tracking_id:
                raise ValueError("该退货记录已经绑定其他流程卡。")
            return locked
        if not locked.shipment_batch_id or not locked.shipment_unit_no:
            raise ValueError("该历史记录缺少明确物理批号，不能自动猜测绑定。")
        bindings = bind_process_cards_to_batch(
            locked.shipment_batch,
            [{
                "card_no": card_no,
                "shipment_unit_no": locked.shipment_unit_no,
                "order_id": order_id,
            }],
            created_by=created_by,
        )
        card = bindings[0].process_card
        prior = list(
            QualityReworkCase.objects.select_for_update()
            .filter(
                process_card__tracking_id=card.tracking_id,
                origin=QualityReworkCase.Origin.CUSTOMER_RETURN,
            )
            .exclude(status=QualityReworkCase.Status.CANCELLED)
            .order_by("opened_on", "id")
        )
        locked.process_card = card
        locked.return_round = len(prior) + 1
        QualityReworkCase.objects.filter(
            process_card__tracking_id=card.tracking_id,
            is_current_return=True,
        ).update(is_current_return=False, updated_at=timezone.now())
        locked.is_current_return = True
        locked.save(update_fields=["process_card", "return_round", "is_current_return", "updated_at"])
        return locked


def reship_return_case(
    case: QualityReworkCase,
    *,
    created_by,
    shipment_date=None,
    net_weight_kg=None,
    piece_quantity=None,
    inspectors=None,
    notes="",
) -> QualityShipmentBatch:
    """Re-ship one returned card using its previous immutable facts by default."""

    inspectors = list(inspectors or [])
    with transaction.atomic():
        locked = (
            QualityReworkCase.objects.select_for_update()
            .select_related("process_card__order", "shipment_line")
            .get(pk=case.pk)
        )
        if locked.status != QualityReworkCase.Status.WAITING_REWORK:
            raise ValueError("只有待返工记录可以重新出货。")
        if not locked.process_card_id:
            raise ValueError("请先为该退货记录绑定流程卡。")
        card = active_process_card(locked.process_card)
        if card.status == ProcessCard.Status.CANCELLED:
            raise ValueError("当前流程卡已作废，不能重新出货。")
        binding = (
            ProcessCardUnitBinding.objects.select_for_update()
            .filter(process_card=card)
            .first()
        )
        if binding is None:
            raise ValueError("流程卡缺少物理批次绑定，请先补齐。")
        quantity = int(piece_quantity or locked.affected_quantity or binding.piece_quantity)
        weight = Decimal(net_weight_kg or locked.affected_weight_kg or binding.net_weight_kg)
        if quantity < 1 or weight <= 0:
            raise ValueError("重新出货数量和净重必须大于0。")
        actual_date = shipment_date if shipment_date is not None else timezone.localdate()
        backfill_reason = str(notes or "").strip() if actual_date and actual_date < timezone.localdate() else ""
        batch = QualityShipmentBatch.objects.create(
            shipment_date=actual_date,
            order=card.order,
            product_specification=card.product_specification,
            product_name_snapshot=card.product_name_snapshot or card.order.product_name,
            specification_snapshot=card.specification_snapshot or card.order.specification,
            material_snapshot=card.material_snapshot or card.order.material,
            unit_weight_g=card.unit_weight_g,
            single_batch_net_weight_kg=weight,
            process_card_shipment_quantity=quantity,
            product_batch_count=1,
            pieces_per_batch=quantity,
            backfill_reason=backfill_reason,
            notes=str(notes or "").strip(),
            created_by=created_by,
        )
        source_allocations = list(
            locked.shipment_allocations.select_related(
                "shipment_line__order", "shipment_line__process_card__order"
            ).order_by("shipment_line_id")
        )
        if source_allocations:
            source_rows = []
            for allocation in source_allocations:
                source_line = allocation.shipment_line
                source_order = source_line.order or (
                    source_line.process_card.order
                    if source_line.process_card_id
                    else None
                )
                if source_order is not None:
                    source_rows.append((source_order, int(allocation.piece_quantity)))
        else:
            source_rows = [(card.order, int(locked.affected_quantity or quantity))]
        if not source_rows:
            source_rows = [(card.order, quantity)]
        if quantity < len(source_rows):
            raise ValueError("重新出货数量过小，无法保持原订单分配。")
        original_total = sum(value for _, value in source_rows)
        remaining_quantity = quantity
        allocated_quantities = []
        for index, (_, source_quantity) in enumerate(source_rows):
            if index == len(source_rows) - 1:
                allocated = remaining_quantity
            else:
                allocated = max(
                    1,
                    int(
                        (
                            Decimal(quantity)
                            * Decimal(source_quantity)
                            / Decimal(original_total)
                        ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
                    ),
                )
                allocated = min(
                    allocated,
                    remaining_quantity - (len(source_rows) - index - 1),
                )
            allocated_quantities.append(allocated)
            remaining_quantity -= allocated
        remaining_weight = weight
        allocated_weights = []
        for index, allocated in enumerate(allocated_quantities):
            if index == len(allocated_quantities) - 1:
                allocated_weight = remaining_weight
            else:
                allocated_weight = (
                    weight * Decimal(allocated) / Decimal(quantity)
                ).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
                allocated_weight = min(
                    max(allocated_weight, Decimal("0.001")),
                    remaining_weight
                    - Decimal("0.001") * (len(allocated_quantities) - index - 1),
                )
            allocated_weights.append(allocated_weight)
            remaining_weight -= allocated_weight
        unit_weight = card.unit_weight_g or (
            weight * Decimal("1000") / Decimal(quantity)
        ).quantize(Decimal("0.00001"), rounding=ROUND_HALF_UP)
        reship_lines = []
        for index, ((target_order, _), allocated, allocated_weight) in enumerate(
            zip(source_rows, allocated_quantities, allocated_weights)
        ):
            # A single-order return can keep the direct process-card FK.  For
            # an old auto-allocation boundary the binding itself remains the
            # physical identity while logical order shares stay separate.
            linked_card = card if len(source_rows) == 1 else None
            created_line = QualityShipmentLine.objects.create(
                    batch=batch,
                    process_card=linked_card,
                    order=target_order,
                    product_specification=(
                        target_order.product_specification
                        or card.product_specification
                    ),
                    specification_snapshot=target_order.specification,
                    material_snapshot=target_order.material,
                    net_weight_kg=allocated_weight,
                    piece_quantity=allocated,
                    unit_weight_g_snapshot=unit_weight,
                    single_batch_net_weight_kg=(
                        weight if len(source_rows) == 1 else None
                    ),
                    process_card_shipment_quantity=(
                        quantity if len(source_rows) == 1 else None
                    ),
                    product_batch_count=(1 if len(source_rows) == 1 else None),
                    pieces_per_batch=(quantity if len(source_rows) == 1 else None),
                    notes=f"退货返工 {locked.case_no} 第{locked.return_round or 1}次重新出货",
                )
            if len(source_rows) > 1 and created_line.piece_quantity != allocated:
                QualityShipmentLine.objects.filter(pk=created_line.pk).update(
                    piece_quantity=allocated, updated_at=timezone.now()
                )
                created_line.piece_quantity = allocated
            reship_lines.append(created_line)
        QualityShipmentBatch.objects.filter(pk=batch.pk).update(
            status=QualityShipmentBatch.Status.CONFIRMED,
            updated_at=timezone.now(),
        )
        batch.status = QualityShipmentBatch.Status.CONFIRMED
        if inspectors:
            batch.inspectors.set(inspectors)
            batch.inspector = inspectors[0]
            batch.save(update_fields=["inspector", "updated_at"])
        binding.shipment_batch = batch
        binding.shipment_unit_no = 1
        binding.piece_quantity = quantity
        binding.net_weight_kg = weight
        binding.save(
            update_fields=[
                "shipment_batch", "shipment_unit_no", "piece_quantity",
                "net_weight_kg", "updated_at",
            ]
        )
        locked.status = QualityReworkCase.Status.RESHIPPED
        locked.closed_on = actual_date
        locked.save(update_fields=["status", "closed_on", "updated_at"])
        card.refresh_shipping_status()
        sync_order_status_from_delivery(
            {line.order_id or card.order_id for line in reship_lines},
            source="SHIPMENT",
            operator=created_by,
            reason_prefix=f"退货返工 {locked.case_no} 已重新出货。",
        )
        return batch


def order_delivery_totals(order_ids: Iterable[int]) -> dict[int, dict[str, int]]:
    """Expose gross transport work and current effective delivered quantities."""

    ids = {int(value) for value in order_ids if value is not None}
    effective = delivered_quantities_by_order(ids)
    result = {
        order_id: {
            "gross_shipped_quantity": 0,
            "returned_quantity": 0,
            "effective_delivered_quantity": effective.get(order_id, 0),
        }
        for order_id in ids
    }
    lines = QualityShipmentLine.objects.filter(
        batch__status=QualityShipmentBatch.Status.CONFIRMED
    ).filter(Q(order_id__in=ids) | Q(process_card__order_id__in=ids)).select_related(
        "process_card"
    )
    for line in lines:
        order_id = line.order_id or (line.process_card.order_id if line.process_card_id else None)
        if order_id in result:
            result[order_id]["gross_shipped_quantity"] += shipment_line_piece_quantity(line)
    for shipment in QualityShipment.objects.filter(order_id__in=ids).only(
        "order_id", "shipped_quantity"
    ):
        result[shipment.order_id]["gross_shipped_quantity"] += int(
            shipment.shipped_quantity or 0
        )
    for order_id, values in result.items():
        values["returned_quantity"] = max(
            0,
            values["gross_shipped_quantity"] - values["effective_delivered_quantity"],
        )
    return result


def sync_order_status_from_delivery(
    order_ids: Iterable[int],
    *,
    source: str,
    operator=None,
    reason_prefix: str = "",
) -> dict[int, str]:
    """Synchronize completed/reopened states from the net effective delivery.

    Cancelled orders are deliberately never changed.  Every real transition
    goes through the orders audit service; unchanged states create no noise.
    """

    from orders.models import OrderStatusChange
    from orders.services import transition_order_status

    ids = sorted({int(value) for value in order_ids if value is not None})
    if not ids:
        return {}
    if source not in OrderStatusChange.Source.values:
        raise ValueError("无效的订单状态联动来源。")
    orders = {
        item.pk: item
        for item in QualityOrder.objects.select_for_update()
        .filter(pk__in=ids)
        .order_by("pk")
    }
    delivered = delivered_quantities_by_order(ids)
    result = {}
    for order_id in ids:
        order = orders.get(order_id)
        if order is None or order.status == QualityOrder.Status.CANCELLED:
            continue
        effective = int(delivered.get(order_id, 0))
        required = int(order.order_quantity or 0)
        target = (
            QualityOrder.Status.COMPLETED
            if effective >= required
            else QualityOrder.Status.OPEN
        )
        if target == QualityOrder.Status.OPEN and order.status != QualityOrder.Status.COMPLETED:
            result[order_id] = order.status
            continue
        if target == order.status:
            result[order_id] = order.status
            continue
        prefix = str(reason_prefix or "").strip()
        detail = (
            f"有效出货 {effective} 件，订单数量 {required} 件，"
            + ("已达到订单数量。" if target == QualityOrder.Status.COMPLETED else "因退货回落，订单重新进入进行中。")
        )
        updated = transition_order_status(
            order,
            target,
            source=source,
            reason=" ".join(value for value in (prefix, detail) if value),
            operator=operator,
        )
        result[order_id] = updated.status
    return result
