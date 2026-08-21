from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable

from django.db import IntegrityError, transaction
from django.db.models import Q

from .models import (
    ProcessCard,
    QualityOrder,
    QualityReworkCase,
    QualityReturnAllocation,
    QualityShipment,
    QualityShipmentBatch,
    QualityShipmentLine,
)


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
        values = dict(validated_data)
        values.pop("shipment_line", None)
        values.pop("process_card", None)
        values.update(
            {
                "origin": QualityReworkCase.Origin.CUSTOMER_RETURN,
                "shipment_batch": batch,
                "shipment_unit_no": int(unit_no),
                "shipment_line": representative,
                "process_card_id": process_card_id,
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
        for card_id in process_card_ids:
            ProcessCard.objects.get(pk=card_id).refresh_shipping_status()
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
