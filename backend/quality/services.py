from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable

from django.db.models import Q

from .models import (
    QualityOrder,
    QualityReworkCase,
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
        .prefetch_related("rework_cases")
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
