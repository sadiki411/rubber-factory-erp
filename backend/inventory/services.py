from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils import timezone

from orders.models import ProductSpecification
from quality.unit_weights import remember_confirmed_product_unit_weight

from .models import (
    InventoryBatch,
    InventoryContainer,
    InventoryLocation,
    InventoryOutbound,
    InventoryOutboundLine,
    InventoryProduct,
    InventoryTransaction,
)


def bootstrap_inventory_locations():
    """Create the fixed physical positions requested for the nine shelves."""
    created = 0
    for rack_number in range(1, 10):
        rack_code = f"K{rack_number:02d}"
        if rack_number <= 6:
            rack_type = InventoryLocation.RackType.LARGE
            level_count, position_count = 4, 5
            allows_basket, allows_bag = True, True
        else:
            rack_type = InventoryLocation.RackType.SMALL
            level_count, position_count = 5, 2
            allows_basket, allows_bag = False, True
        for level_no in range(1, level_count + 1):
            for position_no in range(1, position_count + 1):
                code = f"{rack_code}-L{level_no:02d}-P{position_no:02d}"
                _, was_created = InventoryLocation.objects.get_or_create(
                    code=code,
                    defaults={
                        "rack_code": rack_code,
                        "rack_type": rack_type,
                        "level_no": level_no,
                        "position_no": position_no,
                        "label": f"{rack_code} 第{level_no}层 第{position_no}位",
                        "allows_basket": allows_basket,
                        "allows_bag": allows_bag,
                    },
                )
                created += int(was_created)
    InventoryLocation.objects.get_or_create(
        code="F01-FRIDGE",
        defaults={
            "rack_code": "F01",
            "rack_type": InventoryLocation.RackType.FRIDGE,
            "label": "冰箱",
            "allows_basket": False,
            "allows_bag": False,
        },
    )
    return created


def _make_batch_no():
    prefix = timezone.localdate().strftime("INV-%Y%m%d")
    last = InventoryBatch.objects.filter(batch_no__startswith=prefix).order_by("-batch_no").values_list("batch_no", flat=True).first()
    sequence = int(last.rsplit("-", 1)[-1]) + 1 if last else 1
    return f"{prefix}-{sequence:04d}"


def _make_container_code():
    prefix = timezone.localdate().strftime("CT-%Y%m%d")
    last = InventoryContainer.objects.filter(container_code__startswith=prefix).order_by("-container_code").values_list("container_code", flat=True).first()
    sequence = int(last.rsplit("-", 1)[-1]) + 1 if last else 1
    return f"{prefix}-{sequence:04d}"


def _matching_product_specification(product):
    specification = str(product.specification or "").strip()
    material = str(product.material or "").strip()
    if not specification or not material:
        return None
    queryset = ProductSpecification.objects.filter(
        is_active=True,
        specification__iexact=specification,
        material__iexact=material,
    )
    matches = list(queryset[:2])
    return matches[0] if len(matches) == 1 else None


def _product_from_payload(data):
    product_id = data.get("product_id")
    if product_id:
        product = InventoryProduct.objects.get(pk=product_id)
    else:
        product_data = data.get("product") or {}
        product = InventoryProduct.objects.create(
            product_code=product_data.get("product_code", ""),
            product_name=product_data.get("product_name", ""),
            specification=product_data.get("specification", ""),
            material=product_data.get("material", ""),
            unit_weight_g=product_data.get("unit_weight_g"),
            product_specification_id=product_data.get("product_specification"),
            notes=product_data.get("notes", ""),
        )

    fields = []
    if not product.product_specification_id:
        matched = _matching_product_specification(product)
        if matched:
            product.product_specification = matched
            fields.append("product_specification")
    unit_weight_g = data.get("unit_weight_g")
    if unit_weight_g is None:
        unit_weight_g = (data.get("product") or {}).get("unit_weight_g")
    if unit_weight_g is not None and product.unit_weight_g != unit_weight_g:
        product.unit_weight_g = unit_weight_g
        fields.append("unit_weight_g")
    if fields:
        product.save(update_fields=[*fields, "updated_at"])
    return product, unit_weight_g


@transaction.atomic
def create_inventory_receipt(data, *, created_by):
    location = InventoryLocation.objects.select_for_update().get(pk=data["location_id"])
    if not location.is_active:
        raise ValueError("目标库位已停用。")
    container_type = data["container_type"]
    if container_type == InventoryContainer.ContainerType.BASKET and not location.allows_basket:
        raise ValueError("小货架固定为袋位，不允许放筐。")
    if container_type == InventoryContainer.ContainerType.BAG and not location.allows_bag:
        raise ValueError("该库位不允许放袋。")
    if InventoryContainer.objects.filter(location=location, status=InventoryContainer.Status.ACTIVE).exists():
        raise ValueError("目标库位已经有在用容器。")
    product, entered_unit_weight_g = _product_from_payload(data)
    received_on = data.get("received_on") or timezone.localdate()
    if entered_unit_weight_g is not None and product.product_specification_id:
        remember_confirmed_product_unit_weight(
            product_specification_id=product.product_specification_id,
            unit_weight_g=entered_unit_weight_g,
            created_by=created_by,
            measured_on=received_on,
            note="由库存入库确认自动保存的产品单重",
            backfill_reason="库存入库记录",
        )
    batch = InventoryBatch.objects.create(
        batch_no=data.get("batch_no") or _make_batch_no(),
        product=product,
        received_on=received_on,
        source_type=data.get("source_type") or InventoryBatch.SourceType.MANUAL,
        source_note=str(data.get("source_note") or "").strip(),
        quality_status=data.get("quality_status") or InventoryBatch.QualityStatus.WAITING,
        inspector_id=data.get("inspector_id"),
        inspected_on=data.get("inspected_on"),
        notes=str(data.get("notes") or "").strip(),
    )
    container = InventoryContainer.objects.create(
        container_code=data.get("container_code") or _make_container_code(),
        batch=batch,
        container_type=container_type,
        location=location,
        bag_count=data.get("bag_count") or 1,
        pieces_per_bag=data.get("pieces_per_bag"),
        quantity=data["quantity"],
        notes=str(data.get("notes") or "").strip(),
    )
    InventoryTransaction.objects.create(
        transaction_type=InventoryTransaction.TransactionType.RECEIPT,
        batch=batch,
        container=container,
        quantity=container.quantity,
        to_location=location,
        reason="库存直接入库",
        created_by=created_by,
    )
    return container


def _inventory_product_snapshot(product):
    return " / ".join(filter(None, [
        str(product.product_code or "").strip(),
        str(product.product_name or "").strip(),
        str(product.specification or "").strip(),
        str(product.material or "").strip(),
        f"{product.unit_weight_g}g" if product.unit_weight_g is not None else "",
    ])) or f"产品#{product.pk}"


@transaction.atomic
def correct_inventory_container_product(container, data, *, created_by):
    """Correct or replace the product attached to one received inventory batch."""

    container = (
        InventoryContainer.objects.select_for_update()
        .select_related("batch__product", "location")
        .get(pk=container.pk)
    )
    batch = container.batch
    previous_product = batch.product
    replacement_id = data.get("replacement_product_id")
    if replacement_id:
        try:
            product = InventoryProduct.objects.select_for_update().get(pk=replacement_id, is_active=True)
        except InventoryProduct.DoesNotExist as exc:
            raise ValueError("选择的已有产品不存在或已停用。") from exc
    else:
        product = InventoryProduct.objects.select_for_update().get(pk=previous_product.pk)

    identity_changed = False
    changed_fields = []
    for field in ("product_code", "product_name", "specification", "material"):
        if field not in data:
            continue
        value = str(data.get(field) or "").strip()
        if getattr(product, field) != value:
            setattr(product, field, value)
            changed_fields.append(field)
            identity_changed = identity_changed or field in {"specification", "material"}

    if "unit_weight_g" in data and product.unit_weight_g != data.get("unit_weight_g"):
        product.unit_weight_g = data.get("unit_weight_g")
        changed_fields.append("unit_weight_g")

    if identity_changed:
        product.product_specification = None
        matched = _matching_product_specification(product)
        if matched:
            product.product_specification = matched
        changed_fields.append("product_specification")

    if changed_fields:
        try:
            product.save(update_fields=[*dict.fromkeys(changed_fields), "updated_at"])
        except DjangoValidationError as exc:
            messages = getattr(exc, "messages", None) or [str(exc)]
            raise ValueError("；".join(messages)) from exc

    if batch.product_id != product.pk:
        batch.product = product
        batch.save(update_fields=["product", "updated_at"])

    entered_weight = data.get("unit_weight_g") if "unit_weight_g" in data else None
    if entered_weight is not None and product.product_specification_id:
        remember_confirmed_product_unit_weight(
            product_specification_id=product.product_specification_id,
            unit_weight_g=entered_weight,
            created_by=created_by,
            measured_on=batch.received_on,
            note="由库存产品资料更正自动保存的产品单重",
            backfill_reason="库存产品资料更正",
        )

    before_text = _inventory_product_snapshot(previous_product)
    after_text = _inventory_product_snapshot(product)
    operator_reason = str(data.get("reason") or "").strip()
    reason = f"库存产品资料更正：{before_text} -> {after_text}"
    if operator_reason:
        reason = f"{reason}；说明：{operator_reason}"
    InventoryTransaction.objects.create(
        transaction_type=InventoryTransaction.TransactionType.ADJUST,
        batch=batch,
        container=container,
        quantity=0,
        from_location=container.location,
        to_location=container.location,
        reason=reason[:500],
        created_by=created_by,
    )
    return container


@transaction.atomic
def move_inventory_container(container, target_location, *, created_by, reason=""):
    container = InventoryContainer.objects.select_for_update().select_related("batch", "location").get(pk=container.pk)
    target_location = InventoryLocation.objects.select_for_update().get(pk=target_location.pk)
    if container.status != InventoryContainer.Status.ACTIVE:
        raise ValueError("已空或已关闭的容器不能移库。")
    if not target_location.is_active:
        raise ValueError("目标库位已停用。")
    if container.location_id == target_location.pk:
        raise ValueError("目标库位与当前位置相同。")
    if container.container_type == InventoryContainer.ContainerType.BASKET and not target_location.allows_basket:
        raise ValueError("目标库位不允许放筐。")
    if container.container_type == InventoryContainer.ContainerType.BAG and not target_location.allows_bag:
        raise ValueError("目标库位不允许放袋。")
    if InventoryContainer.objects.filter(location=target_location, status=InventoryContainer.Status.ACTIVE).exclude(pk=container.pk).exists():
        raise ValueError("目标库位已经有在用容器。")
    previous = container.location
    container.location = target_location
    container.save(update_fields=["location", "updated_at"])
    InventoryTransaction.objects.create(
        transaction_type=InventoryTransaction.TransactionType.MOVE,
        batch=container.batch,
        container=container,
        quantity=0,
        from_location=previous,
        to_location=target_location,
        reason=reason or "库存移库",
        created_by=created_by,
    )
    return container


@transaction.atomic
def create_inventory_outbound(data, *, created_by):
    outbound_no = str(data.get("outbound_no") or "").strip() or f"OUT-{timezone.localdate():%Y%m%d}-{InventoryOutbound.objects.count() + 1:04d}"
    outbound = InventoryOutbound.objects.create(
        outbound_no=outbound_no,
        order_id=data.get("order_id"),
        shipment_ref=str(data.get("shipment_ref") or "").strip(),
        note=str(data.get("note") or "").strip(),
        created_by=created_by,
    )
    for row in data["lines"]:
        container = InventoryContainer.objects.select_for_update().select_related("batch", "batch__product", "location").get(pk=row["container_id"])
        quantity = int(row["quantity"])
        if container.status != InventoryContainer.Status.ACTIVE or container.batch.quality_status != InventoryBatch.QualityStatus.PASSED:
            raise ValueError(f"容器 {container.container_code} 不是可用的已检库存。")
        if quantity > container.quantity:
            raise ValueError(f"容器 {container.container_code} 可用数量只有 {container.quantity}。")
        source_location = container.location
        InventoryOutboundLine.objects.create(outbound=outbound, container=container, quantity=quantity)
        remaining = container.quantity - quantity
        if remaining == 0:
            container.quantity = 0
            container.status = InventoryContainer.Status.EMPTY
            container.location = None
        else:
            container.quantity = remaining
        container.save(update_fields=["quantity", "status", "location", "updated_at"])
        InventoryTransaction.objects.create(
            transaction_type=InventoryTransaction.TransactionType.OUTBOUND,
            batch=container.batch,
            container=container,
            quantity=quantity,
            from_location=source_location,
            outbound=outbound,
            reason="库存出库",
            created_by=created_by,
        )
    return outbound


def product_availability(*, product_specification_id=None, product_code="", specification="", material=""):
    products = InventoryProduct.objects.filter(is_active=True)
    if product_specification_id:
        products = products.filter(product_specification_id=product_specification_id)
        if not products.exists():
            products = InventoryProduct.objects.filter(is_active=True)
            if product_code:
                products = products.filter(product_code__iexact=product_code)
            if specification:
                products = products.filter(specification__iexact=specification)
            if material:
                products = products.filter(material__iexact=material)
    else:
        if product_code:
            products = products.filter(product_code__iexact=product_code)
        if specification:
            products = products.filter(specification__iexact=specification)
        if material:
            products = products.filter(material__iexact=material)
    batches = InventoryBatch.objects.filter(product__in=products).prefetch_related("containers__location", "product")
    result = {"total_quantity": 0, "available_quantity": 0, "waiting_inspection_quantity": 0, "locations": []}
    for batch in batches:
        for container in batch.containers.all():
            if container.status != InventoryContainer.Status.ACTIVE:
                continue
            result["total_quantity"] += container.quantity
            if batch.quality_status == InventoryBatch.QualityStatus.PASSED:
                result["available_quantity"] += container.quantity
            elif batch.quality_status == InventoryBatch.QualityStatus.WAITING:
                result["waiting_inspection_quantity"] += container.quantity
            if container.location_id:
                result["locations"].append({
                    "location": container.location.code,
                    "container_code": container.container_code,
                    "quantity": container.quantity,
                    "quality_status": batch.quality_status,
                    "batch_no": batch.batch_no,
                })
    return result


def product_availability_by_orders(orders):
    """Calculate inventory availability for a page of orders in one pass.

    The matching rule intentionally mirrors ``product_availability``: an
    active product linked to the specification has priority; otherwise the
    exact non-empty product code/specification/material identity is used.
    """
    order_rows = list(orders)
    if not order_rows:
        return {}

    products = list(
        InventoryProduct.objects.filter(is_active=True).values(
            "id", "product_specification_id", "product_code", "specification", "material"
        )
    )
    by_spec = {}
    for product in products:
        if product["product_specification_id"]:
            by_spec.setdefault(product["product_specification_id"], []).append(product["id"])

    def identity_products(order):
        values = (
            str(order.product_code or "").casefold(),
            str(order.specification or "").casefold(),
            str(order.material or "").casefold(),
        )
        return [
            product["id"]
            for product in products
            if (not values[0] or str(product["product_code"] or "").casefold() == values[0])
            and (not values[1] or str(product["specification"] or "").casefold() == values[1])
            and (not values[2] or str(product["material"] or "").casefold() == values[2])
        ]

    selected = {}
    product_ids = set()
    for order in order_rows:
        product_ids_for_order = by_spec.get(order.product_specification_id) or identity_products(order)
        selected[order.pk] = set(product_ids_for_order)
        product_ids.update(product_ids_for_order)

    totals = {
        product_id: {
            "total_quantity": 0,
            "available_quantity": 0,
            "waiting_inspection_quantity": 0,
            "locations": [],
        }
        for product_id in product_ids
    }
    batches = InventoryBatch.objects.filter(product_id__in=product_ids).prefetch_related(
        "containers__location"
    )
    for batch in batches:
        aggregate = totals[batch.product_id]
        for container in batch.containers.all():
            if container.status != InventoryContainer.Status.ACTIVE:
                continue
            quantity = int(container.quantity or 0)
            aggregate["total_quantity"] += quantity
            if batch.quality_status == InventoryBatch.QualityStatus.PASSED:
                aggregate["available_quantity"] += quantity
            elif batch.quality_status == InventoryBatch.QualityStatus.WAITING:
                aggregate["waiting_inspection_quantity"] += quantity
            if container.location_id:
                aggregate["locations"].append({
                    "location": container.location.code,
                    "container_code": container.container_code,
                    "quantity": quantity,
                    "quality_status": batch.quality_status,
                    "batch_no": batch.batch_no,
                })

    result = {}
    for order in order_rows:
        value = {
            "total_quantity": 0,
            "available_quantity": 0,
            "waiting_inspection_quantity": 0,
            "locations": [],
        }
        for product_id in selected[order.pk]:
            aggregate = totals[product_id]
            for field in ("total_quantity", "available_quantity", "waiting_inspection_quantity"):
                value[field] += aggregate[field]
            value["locations"].extend(aggregate["locations"])
        result[order.pk] = value
    return result
