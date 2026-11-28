"""Explicit Xiangbang unordered physical shipments on the existing ledger."""
import hashlib
import json
import re
import uuid
from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.db import transaction
from django.db.models import F, Max, Q, Sum
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from orders.customer_inspection import get_order_snapshots
from orders.identities import order_customer_key
from orders.models import ProductSpecification

from .models import (
    ProcessCard, QualityEmployee, QualityOrder, QualityReturnAllocation,
    QualityReworkCase, QualityShipmentBatch, QualityShipmentLine,
    QualityShipmentOrderAllocation, ShipmentSupplement, ShipmentSupplementEvent,
    ShipmentSupplementDeliveryAdjustment, ShipmentSupplementIssue, ShipmentSupplementOrigin,
)
from .services import (
    delivered_quantities_by_order, effective_supplement_adjustments, shipment_inspectors, shipment_return_groups,
    shipment_unit_allocations,
    sync_order_status_from_delivery,
)
from .tracing import batch_ids_for_cards, shipment_card_sources


def enabled():
    return settings.QUALITY_SUPPLEMENTS_ENABLED


def can_reconcile(user):
    return user.is_staff or user.has_perm("quality.change_qualityshipmentorderallocation")


def card_key(text):
    from .services import normalize_process_card_code
    return normalize_process_card_code(text)


def failure(message):
    raise ValidationError({"detail": message})


def row(query, identity, label):
    item = query.filter(pk=identity).first()
    if item is None:
        raise NotFound(f"{label}不存在或不可用。")
    return item


def identity(value):
    return (value or "").strip().casefold()


class CommandSerializer(serializers.Serializer):
    request_key = serializers.UUIDField()
    issue_id = serializers.IntegerField(required=False, min_value=1)
    kind = serializers.ChoiceField(QualityShipmentBatch.SupplementKind.choices, required=False)
    product_id = serializers.IntegerField(required=False, min_value=1)
    customer_code = serializers.CharField(required=False, allow_blank=True, max_length=150)
    project_no = serializers.CharField(required=False, allow_blank=True, max_length=150)
    reason = serializers.CharField(required=False, max_length=300)
    requested_quantity = serializers.IntegerField(required=False, allow_null=True, min_value=1, max_value=100_000_000)
    shipment_date = serializers.DateField(required=False)
    quantity = serializers.IntegerField(required=False, min_value=1, max_value=100_000_000)
    weight_kg = serializers.DecimalField(required=False, max_digits=14, decimal_places=3, min_value=Decimal("0.001"))
    unit_weight_g = serializers.DecimalField(required=False, max_digits=14, decimal_places=5, min_value=Decimal("0.00001"))
    method = serializers.ChoiceField(["COUNT", "WEIGHT"], required=False)
    inspector_id = serializers.IntegerField(required=False, min_value=1)
    customer_card_no = serializers.CharField(required=False, max_length=150, allow_blank=True)
    reused_card_no = serializers.CharField(required=False, max_length=150, allow_blank=True)
    internal_card_id = serializers.UUIDField(required=False)
    source_note = serializers.CharField(required=False, max_length=500, allow_blank=True)
    delivery_info = serializers.CharField(required=False, max_length=300, allow_blank=True)
    backfill_reason = serializers.CharField(required=False, max_length=300, allow_blank=True)
    origins = serializers.ListField(child=serializers.DictField(), required=False, max_length=20)
    overage_confirmed = serializers.BooleanField(required=False)
    order_id = serializers.IntegerField(required=False, min_value=1)
    allocation_id = serializers.IntegerField(required=False, min_value=1)
    basis = serializers.ChoiceField(["REPLACEMENT_AFTER_RETURN", "REPLACEMENT_WITHOUT_RETURN", "INDEPENDENT", "RECONCILED_SINGLE"], required=False)
    origin_id = serializers.IntegerField(required=False, min_value=1)
    expected_released = serializers.IntegerField(required=False, min_value=0)
    original_delivery_excluded = serializers.BooleanField(required=False)
    note = serializers.CharField(required=False, max_length=500, allow_blank=True)
    action_date = serializers.DateField(required=False)
    retest_note = serializers.CharField(required=False, max_length=500, allow_blank=True)
    disputed_quantity = serializers.IntegerField(required=False, allow_null=True, min_value=1, max_value=100_000_000)
    dispute_note = serializers.CharField(required=False, max_length=500, allow_blank=True)
    expected_packages = serializers.IntegerField(required=False, min_value=0)
    expected_claimed = serializers.IntegerField(required=False, min_value=0)
    expected_allocations = serializers.ListField(child=serializers.IntegerField(min_value=1), max_length=10000, required=False)


def _atomic_operation(action, data, user, worker, target=None):
    # Serialize before reading balances, including operations with different
    # request keys. A SQLite read transaction cannot be upgraded reliably.
    with transaction.atomic():
        ShipmentSupplementEvent.objects.filter(pk=0).update(digest=F("digest"))
        key = data["request_key"]
        digest = hashlib.sha256(json.dumps([action, target, data], default=str, sort_keys=True).encode()).hexdigest()
        old = ShipmentSupplementEvent.objects.filter(request_key=key).first()
        if old:
            if old.digest != digest or old.actor_id != user.pk:
                failure("同一操作编号不能用于其他内容或账号。")
            return {"issue_id": old.issue_id, "batch_id": old.batch_id, **old.details, "replayed": True}
        issue, batch, details = worker()
        ShipmentSupplementEvent.objects.create(request_key=key, digest=digest, action=action, issue=issue,
                                                batch=batch, details=details, actor=user)
        return {"issue_id": issue.pk, "batch_id": batch.pk if batch else None, **details, "replayed": False}


def _shipment_facts(batch, unit_no):
    if batch.status != QualityShipmentBatch.Status.CONFIRMED:
        failure("原出货须已确认。")
    if batch.supplement_kind:
        if unit_no != 1:
            failure("没有这个原实物包。")
        line = batch.lines.get()
        issue = batch.supplement.issue
        if batch.supplement.customer_card_no and batch.supplement.customer_card_no != batch.supplement.customer_card_key:
            failure("原包客户卡身份不一致。")
        return line.product_specification_id, batch.supplement.customer_card_no, (
            issue.customer_code, issue.project_no,
        )
    group = next((g for g in shipment_return_groups(batch) if g["first_unit_no"] <= unit_no <= g["last_unit_no"]), None)
    if not group:
        failure("不能核实该原实物包的范围，先保留来源待核。")
    direct_ids = {line.pk for line in group["lines"]} if group["first_unit_no"] == group["last_unit_no"] else set()
    refs = [
        r for r in shipment_card_sources(batch)
        if r.get("shipment_unit_no") == unit_no
        or (r.get("shipment_unit_no") is None and r.get("line_id") in direct_ids and "LINE" in r["evidence"])
    ]
    cards = {r["card_no"] for r in refs if r.get("card_no")}
    ids = {line.product_specification_id for line in group["lines"]}
    orders = {order.pk: order for line in group["lines"]
              if (order := line.order or (line.process_card.order if line.process_card_id else None))}
    if not orders or any(order_customer_key(order) != "翔邦" for order in orders.values()):
        failure("不能确认这包是翔邦来源，请人工核对。")
    snapshots = get_order_snapshots(list(orders.values()))
    keys = {(o.customer_code or "", snapshots.get(o.pk, {}).get("project_no") or "") for o in orders.values()}
    if len(ids) != 1 or None in ids or len(keys) != 1:
        failure("原包的产品、客户或项目有歧义。")
    return ids.pop(), cards.pop() if len(cards) == 1 else "", keys.pop()


def _add_origins(issue, origins):
    if origins and issue.kind != QualityShipmentBatch.SupplementKind.REPLACEMENT:
        failure("临时要货不登记为原货换新。")
    for entry in origins:
        batch_id, unit_no = entry.get("batch_id"), entry.get("unit_no")
        if type(batch_id) is not int or type(unit_no) is not int or not 1 <= unit_no <= 10000000:
            failure("请选择已核实的原出货和物理包。")
        batch = row(QualityShipmentBatch.objects.all(), batch_id, "原出货")
        product_id, card_no, key = _shipment_facts(batch, unit_no)
        if product_id != issue.product_id:
            failure("原包不是所选产品档案。")
        if any(expected and (not supplied or identity(supplied) != identity(expected))
               for supplied, expected in zip((issue.customer_code, issue.project_no), key)):
            failure("原包的客户代号或项目与本次不一致/未核对。")
        if ShipmentSupplement.objects.filter(batch=batch, issue=issue).exists():
            failure("本事项补发的包不能反过来当作同一事项原货；请开新一轮事项。")
        if not issue.origins.filter(batch=batch, unit_no=unit_no).exists():
            booked = QualityShipmentOrderAllocation.objects.filter(shipment_line__batch__supplement__issue=issue)
            ids = booked.values_list("order_id", flat=True)
            if (batch.order_id and booked.filter(order_id=batch.order_id).exists()) or batch.lines.filter(
                Q(order_id__in=ids) | Q(process_card__order_id__in=ids) | Q(order_allocations__order_id__in=ids),
            ).exists():
                failure("新增原包与事项已认领项次重叠。请先核对并更正认领记录，不能补挂重复履约。")
        ShipmentSupplementOrigin.objects.get_or_create(
            issue=issue, batch=batch, unit_no=unit_no, defaults={"card_no_snapshot": card_no},
        )


def _issue(data, user):
    if data.get("issue_id"):
        issue = row(ShipmentSupplementIssue.objects.all(), data["issue_id"], "补货事项")
        if _moved_out(issue):
            failure("此事项的实物包已更正关联，不再作为待补事项。")
        if (data.get("product_id", issue.product_id) != issue.product_id
                or data.get("kind", issue.kind) != issue.kind
                or identity(data.get("customer_code", issue.customer_code)) != identity(issue.customer_code)
                or identity(data.get("project_no", issue.project_no)) != identity(issue.project_no)
                or data.get("requested_quantity", issue.requested_quantity) != issue.requested_quantity):
            failure("补货事项产品、类别、客户身份或要求量已确定；请在原事项内处理。")
    else:
        if not all(data.get(key) for key in ("kind", "product_id", "reason")):
            failure("请选择补货原因、产品档案，并填写原因。")
        product = row(ProductSpecification.objects.filter(is_active=True), data["product_id"], "产品")
        if not product.specification.strip() or not product.material.strip():
            failure("产品档案缺明确的规格或材质。")
        if data.get("reused_card_no"):
            for origin in data.get("origins", []):
                batch_id, unit_no = origin.get("batch_id"), origin.get("unit_no")
                if type(batch_id) is int and type(unit_no) is int and ShipmentSupplementOrigin.objects.filter(
                    batch_id=batch_id, unit_no=unit_no,
                ).exists():
                    failure("此原包已有补货事项，请从旧卡查询结果打开原事项继续，先核对本包是否已登记。")
        issue = ShipmentSupplementIssue.objects.create(
            kind=data["kind"], product=product, customer_code=data.get("customer_code", ""),
            project_no=data.get("project_no", ""), reason=data["reason"],
            requested_quantity=data.get("requested_quantity"), created_by=user,
        )
    _add_origins(issue, data.get("origins", []))
    return issue


def _progress(issue):
    batches = issue.shipments.filter(batch__status="CONFIRMED")
    return (batches.aggregate(pieces=Sum("batch__lines__piece_quantity"))["pieces"] or 0,
            batches.filter(batch__lines__piece_quantity__isnull=True).count())


def _moved_out(issue):
    return not issue.shipments.exists() and ShipmentSupplementEvent.objects.filter(
        action="LINK_ISSUE", details__previous_issue_id=issue.pk,
    ).exists()


def _reused_card(issue, data):
    code = card_key(data.get("reused_card_no") or "")
    if code:
        sources = list(issue.origins.all()[:2])
        if (data.get("customer_card_no") or issue.kind != QualityShipmentBatch.SupplementKind.REPLACEMENT
                or len(sources) != 1 or not sources[0].card_no_snapshot or card_key(sources[0].card_no_snapshot) != code):
            failure("沿用旧号必须选择唯一核实的原出货与物理包，不能同时作为新客户卡绑定。")
    return code


def create_shipment(data, user, *, pending_only=False):
    def work():
        issue = _issue(data, user)
        reused = _reused_card(issue, data)
        if pending_only:
            if data.get("issue_id"):
                failure("这是已有事项，请在原事项中继续。")
            return issue, None, {}
        if data.get("issue_id") and data.get("expected_packages") != issue.shipments.count():
            failure("事项已有其他出货或页面已过期。请刷新，先核对是否已经登记本包。")
        product = issue.product
        if not product.is_active:
            failure("产品已停用，不能登记新实发。")
        date = data.get("shipment_date")
        if not date or date > timezone.localdate():
            failure("请选择已经实际出货的日期，尚未发出请只建待补事项。")
        if date < timezone.localdate() and not data.get("backfill_reason"):
            failure("补录历史实发时请填写原因。")
        method, qty, weight, unit = (data.get(k) for k in ("method", "quantity", "weight_kg", "unit_weight_g"))
        if method == "COUNT" and qty:
            if unit:
                failure("实点件数不能用输入单重来代替。")
        elif method == "WEIGHT" and weight and qty is None:
            if unit:
                qty = int((weight * 1000 / unit).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
                if not 1 <= qty <= 100_000_000:
                    failure("由单重得到的件数超出有效范围，请核对。")
        else:
            failure("请选择实点件数或称重，并填写对应实发量。")
        sent, _ = _progress(issue)
        if issue.requested_quantity and qty is not None and sent + qty > issue.requested_quantity:
            if not (can_reconcile(user) and data.get("overage_confirmed") and data.get("source_note")):
                failure("已知件数超出要求量；请由有权限人员写明核对依据后继续。")
        number = card_key(data.get("customer_card_no") or "")
        if number and (number.startswith(("ERPFC:", "FC-")) or "/FLOW-CARDS/" in number or len(number) > 150):
            failure("请填写本次客户新卡，内部卡须走生产来源。")
        if number and (
            ProcessCard.objects.filter(card_no__iexact=number).exists()
            or ShipmentSupplement.objects.filter(customer_card_key=number).exists()
        ):
            failure("此卡已登记，不可当新包再次登记。可从旧卡追溯原出货。")
        internal = None
        if data.get("internal_card_id"):
            from flowcards.models import InternalCard
            internal = row(InternalCard.objects.select_related("group").filter(status="ACTIVE"), data["internal_card_id"], "内部卡")
            if (internal.group.product_id != product.pk or internal.shipment_sources.exists()
                    or ShipmentSupplement.objects.filter(internal_card=internal).exists()):
                failure("内卡产品不一致或已用于另一处实发。")
        inspector = None
        if data.get("inspector_id"):
            inspector = row(QualityEmployee.objects.filter(is_active=True, role__in=["INSPECTOR", "BOTH"]), data["inspector_id"], "品检员")
        batch = QualityShipmentBatch.objects.create(
            product_specification=product, product_name_snapshot=product.product_name,
            specification_snapshot=product.specification, material_snapshot=product.material,
            customer="翔邦", supplement_kind=issue.kind, shipment_date=date, status="CONFIRMED",
            delivery_info=data.get("delivery_info", ""), inspector=inspector, unit_weight_g=unit,
            backfill_reason=data.get("backfill_reason", ""), created_by=user,
        )
        QualityShipmentLine.objects.create(
            batch=batch, product_specification=product, specification_snapshot=product.specification,
            material_snapshot=product.material, piece_quantity=qty, net_weight_kg=weight, unit_weight_g_snapshot=unit,
        )
        if inspector:
            batch.inspectors.add(inspector)
        ShipmentSupplement.objects.create(
            batch=batch, issue=issue, amount_method=method, customer_card_no=number,
            customer_card_key=number or None, reused_card_no=reused, internal_card=internal,
            source_note=data.get("source_note", ""),
        )
        return issue, batch, {"package_no": package_no(batch), "quantity": qty}
    return _atomic_operation("PENDING" if pending_only else "SHIP", data, user, work)


def package_no(batch):
    return f"{batch.shipment_no}/1"


def _return_case(batch):
    return batch.rework_cases.filter(origin="CUSTOMER_RETURN").exclude(status="CANCELLED").first()


def _active_order(order, supplement):
    issue, batch = supplement.issue, supplement.batch
    _match_order_identity(order, issue, batch)
    if order.status != QualityOrder.Status.OPEN:
        failure("该项次当前未开放；若客户确认原货不抵交付，请先在补货事项处理交付核减。")


def _match_order_identity(order, issue, batch, *, source_line=None):
    product_id = source_line.product_specification_id if source_line else batch.product_specification_id
    spec = source_line.specification_snapshot if source_line else batch.specification_snapshot
    material = source_line.material_snapshot if source_line else batch.material_snapshot
    if (order_customer_key(order) != "翔邦" or order.status == QualityOrder.Status.CANCELLED
            or order.product_specification_id != product_id or product_id != issue.product_id
            or (identity(order.specification), identity(order.material)) !=
               (identity(spec), identity(material))):
        failure("只能认领本人选定的翔邦同一产品项次，规格和材质也须一致。")
    snapshot = get_order_snapshots([order]).get(order.pk, {})
    target = (order.customer_code or "", snapshot.get("project_no") or "")
    if tuple(map(identity, target)) != tuple(map(identity, (issue.customer_code, issue.project_no))):
        failure("客户代号或项目不同，或仅一方有记录，不能猜配。")
    return target


def _release_origin_options(origin):
    batch, issue = origin.batch, origin.issue
    if (issue.kind != QualityShipmentBatch.SupplementKind.REPLACEMENT or batch.supplement_kind
            or batch.status != QualityShipmentBatch.Status.CONFIRMED or not origin.card_no_snapshot):
        return []
    try:
        _, parts = shipment_unit_allocations(batch, origin.unit_no)
    except ValueError:
        return []
    # No guessed allocations or reinterpretation of legacy returns whose
    # physical package isn't known. Later new returns carry the exact unit.
    active_returns = batch.rework_cases.filter(origin="CUSTOMER_RETURN").exclude(status="CANCELLED")
    if (active_returns.filter(Q(shipment_unit_no=origin.unit_no) | Q(shipment_unit_no__isnull=True)).exists()
            or any(not item["shipment_order_allocation"] for item in parts)):
        return []
    shares = {p["shipment_order_allocation"].pk: p["piece_quantity"] for p in parts}
    adjusted = {a.allocation_id: a for a in ShipmentSupplementDeliveryAdjustment.objects.filter(
        allocation_id__in=shares, unit_no=origin.unit_no)}
    rows = []
    for p in parts:
        alloc, order = p["shipment_order_allocation"], p["order"]
        existing = adjusted.get(alloc.pk)
        already = existing.piece_quantity if existing else 0
        if (existing and existing.origin_id != origin.pk) or already >= p["piece_quantity"]:
            continue
        try:
            _match_order_identity(order, issue, batch, source_line=p["shipment_line"])
        except ValidationError:
            continue
        rows.append({"origin_id": origin.pk, "allocation_id": alloc.pk, "order_id": order.pk,
                     "order_no": order.order_no, "item_no": order.item_no,
                     "max_quantity": p["piece_quantity"] - already, "expected_released": already,
                     "shipment_no": batch.shipment_no, "unit_no": origin.unit_no})
    return rows


def release_delivery(issue_id, data, user):
    if not can_reconcile(user):
        raise PermissionDenied("只有有订单认领权限的人员可以核减有效交付。")

    def work():
        from orders.models import OrderStatusChange

        note, quantity = data.get("note"), data.get("quantity")
        if not data.get("original_delivery_excluded") or not quantity or not note:
            failure("须确认翔邦明确原货不抵此订单，并填写件数与凭证依据。")
        origin = row(ShipmentSupplementOrigin.objects.filter(issue_id=issue_id), data.get("origin_id"), "原包")
        batch = row(QualityShipmentBatch.objects.select_for_update(), origin.batch_id, "原出货")
        origin.batch = batch
        match = next((item for item in _release_origin_options(origin) if item["allocation_id"] == data.get("allocation_id")), None)
        if not match or quantity > match["max_quantity"] or data.get("expected_released") != match["expected_released"]:
            failure("该原包的项次已处理、关联不明确，或核减超过原分摊件数，请重新核对。")
        order_id = match["order_id"]
        order = row(QualityOrder.objects.select_for_update(), order_id, "订单项次")
        alloc = QualityShipmentOrderAllocation.objects.select_related("shipment_line").get(pk=match["allocation_id"])
        _match_order_identity(order, origin.issue, batch, source_line=alloc.shipment_line)
        before = delivered_quantities_by_order([order_id])[order_id]
        entry, _ = ShipmentSupplementDeliveryAdjustment.objects.update_or_create(
            origin=origin, allocation_id=match["allocation_id"], unit_no=origin.unit_no,
            defaults={"piece_quantity": match["expected_released"] + quantity, "evidence": note},
        )
        status = sync_order_status_from_delivery([order_id], source=OrderStatusChange.Source.SYSTEM, operator=user,
            reason_prefix=f"翔邦确认原包 {batch.shipment_no}/{origin.unit_no} 有 {quantity} 件不抵原项次（未实退）。")
        return origin.issue, None, {"id": entry.pk, **match, "quantity": quantity, "note": note,
            "net_before": before, "net_after": delivered_quantities_by_order([order_id])[order_id], "order_status": status[order_id]}
    return _atomic_operation("DELIVERY_RELEASE", data, user, work, issue_id)


def _returned_entitlement(issue, order):
    shares = QualityReturnAllocation.objects.filter(
        Q(shipment_order_allocation__order=order) | Q(
            shipment_order_allocation__isnull=True, shipment_line__order=order,
        ) | Q(
            shipment_order_allocation__isnull=True, shipment_line__order__isnull=True,
            shipment_line__process_card__order=order,
        ),
        case__origin="CUSTOMER_RETURN",
    ).exclude(case__status="CANCELLED")
    origin_filter = Q(pk__in=[])
    for origin in issue.origins.all():
        origin_filter |= Q(case__shipment_batch_id=origin.batch_id, case__shipment_unit_no=origin.unit_no)
    return shares.filter(origin_filter).aggregate(total=Sum("piece_quantity"))["total"] or 0


def validate_origin_return_cancellation(case):
    """Keep cancellation from restoring original credit already used by a replacement."""
    if not case.shipment_batch_id or not case.shipment_unit_no:
        return
    origins = ShipmentSupplementOrigin.objects.filter(batch_id=case.shipment_batch_id, unit_no=case.shipment_unit_no)
    order_ids = case.shipment_allocations.exclude(shipment_order_allocation__isnull=True).values(
        "shipment_order_allocation__order_id")
    covered = QualityShipmentOrderAllocation.objects.filter(
        shipment_line__batch__supplement__issue_id__in=origins.values("issue_id"), order_id__in=order_ids,
    ).order_by().values("order_id").annotate(total=Sum("piece_quantity"))
    covered = list(covered)
    if not covered:
        return
    # On cancellation this exact unit's customer-confirmed deduction comes
    # back into force. Only that explicit evidence is safe to reuse here;
    # allocations dependent on the actual return must be corrected first.
    stable = dict(ShipmentSupplementDeliveryAdjustment.objects.filter(origin__in=origins)
                  .order_by().values("allocation__order_id").annotate(total=Sum("piece_quantity"))
                  .values_list("allocation__order_id", "total"))
    if any(item["total"] > stable.get(item["order_id"], 0) for item in covered):
        failure("已有补发依赖本次实退释放的份额，不能直接取消退货；请先核对并更正补发的订单归属。")


def update_shipment(batch_id, action, data, user):
    def work():
        supplement = ShipmentSupplement.objects.select_related("batch", "issue").filter(batch_id=batch_id).first()
        if supplement is None:
            raise NotFound("找不到此无单出货。")
        batch, issue = supplement.batch, supplement.issue
        line = batch.lines.get()
        old_return = _return_case(batch)
        if action == "RETURN":
            from orders.models import OrderStatusChange
            if old_return:
                failure("这个实物包已退回；同一次重试请保留原提交编号。")
            date, reason = data.get("action_date"), data.get("note")
            if not date or not reason or not batch.shipment_date <= date <= timezone.localdate():
                failure("请填写真实退回日期（不早于实发）及原因。")
            if not line.piece_quantity and not line.net_weight_kg:
                failure("缺少可核对的实发数量或重量。")
            shares = list(line.order_allocations.all())
            ordered_quantity = sum(s.piece_quantity for s in shares)
            locked_allocation_ids = sorted(s.pk for s in shares)
            if (data.get("expected_claimed", ordered_quantity) != ordered_quantity or
                    sorted(data.get("expected_allocations", locked_allocation_ids)) != locked_allocation_ids):
                failure("订单归属在确认前发生变化，请重新打开明细核对实退和回冲数量。")
            people = shipment_inspectors(batch)
            if not people and data.get("inspector_id"):
                people = [row(QualityEmployee.objects.filter(is_active=True, role__in=["INSPECTOR", "BOTH"]),
                              data["inspector_id"], "补录责任品检员")]
            case = QualityReworkCase.objects.create(
                shipment_batch=batch, shipment_line=line, shipment_unit_no=1, origin="CUSTOMER_RETURN",
                affected_quantity=line.piece_quantity, affected_weight_kg=line.net_weight_kg,
                reason=reason, opened_on=date, backfill_reason=reason if date < timezone.localdate() else "",
                status=QualityReworkCase.Status.WAITING_REWORK, return_round=1, is_current_return=True,
                responsible_inspector=people[0] if people else None, created_by=user,
            )
            if people:
                case.inspectors.set(people)
            QualityReturnAllocation.objects.bulk_create([
                QualityReturnAllocation(case=case, shipment_line=line, shipment_order_allocation=share,
                                        piece_quantity=share.piece_quantity, net_weight_kg=share.net_weight_kg or 0)
                for share in shares
            ])
            sync_order_status_from_delivery([s.order_id for s in shares], source=OrderStatusChange.Source.CUSTOMER_RETURN,
                                            operator=user, reason_prefix="无单实物包按已认领份额实退")
            return issue, batch, {"return_id": case.pk, "physical_return": line.piece_quantity,
                                  "order_return": ordered_quantity}
        if not can_reconcile(user):
            raise PermissionDenied("只有有订单认领权限的人员可以处理。")
        if not data.get("note"):
            failure("请填写依据或更正原因。")
        if old_return:
            failure("原包已退回，不能继续认领或改写回冲份额。")
        if action == "VERIFY_QUANTITY":
            qty = data.get("quantity")
            if line.piece_quantity is not None or not line.net_weight_kg or not qty or line.order_allocations.exists():
                failure("只能核实尚无件数、未挂单且未退回的称重包。")
            sent, _ = _progress(issue)
            if issue.requested_quantity and sent + qty > issue.requested_quantity and not data.get("overage_confirmed"):
                failure("实点数量会超出补货要求量，请再次确认。")
            line.piece_quantity = qty
            line.save(update_fields=["piece_quantity", "updated_at"])
            return issue, batch, {"before": None, "quantity": qty, "note": data["note"]}
        if action == "LINK_ISSUE":
            target = row(ShipmentSupplementIssue.objects.filter(kind="REPLACEMENT"), data.get("issue_id"), "质量补货事项")
            if target.pk == issue.pk:
                failure("已关联该事项，无需重复操作。")
            if (supplement.reused_card_no or target.product_id != issue.product_id
                    or identity(target.customer_code) != identity(issue.customer_code)
                    or identity(target.project_no) != identity(issue.project_no)):
                failure("只可关联同一确切产品、客户代号和项目。")
            if (line.order_allocations.exists() or issue.origins.exists()
                    or batch.replacement_origins.exists() or supplement.no_claim_reason
                    or issue.shipments.count() != 1):
                failure("该包已有认领、来源或其他处理记录，不能改挂事项。")
            if data.get("expected_packages") != target.shipments.count():
                failure("目标事项已变化，请重新核对。")
            known, _ = _progress(target)
            if target.requested_quantity and line.piece_quantity is not None and known + line.piece_quantity > target.requested_quantity:
                if not data.get("overage_confirmed"):
                    failure("可能超补或件数待核，请确认并填写依据。")
            supplement.issue = target
            supplement.save(update_fields=["issue"])
            batch.supplement_kind = target.kind
            batch.save(update_fields=["supplement_kind", "updated_at"])
            return target, batch, {"previous_issue_id": issue.pk, "note": data["note"]}
        if action in ("NO_CLAIM", "REOPEN"):
            supplement.no_claim_reason = data["note"] if action == "NO_CLAIM" else ""
            supplement.save(update_fields=["no_claim_reason"])
            return issue, batch, {"note": data["note"]}
        from orders.models import OrderStatusChange
        if action == "UNCLAIM":
            share = row(line.order_allocations.all(), data.get("allocation_id"), "该笔归属")
            details = {"allocation_id": share.pk, "order_id": share.order_id, "quantity": share.piece_quantity, "note": data["note"]}
            share.delete()
            sync_order_status_from_delivery([details["order_id"]], source=OrderStatusChange.Source.SHIPMENT,
                                            operator=user, reason_prefix="撤销无单出货误认领")
            return issue, batch, details
        if supplement.no_claim_reason:
            failure("已标无需挂单，请先核对后重新开启认领。")
        quantity = data.get("quantity")
        used = line.order_allocations.aggregate(total=Sum("piece_quantity"))["total"] or 0
        if not quantity or not line.piece_quantity or quantity > line.piece_quantity - used:
            failure("认领量超过未认领的实发件数，或实发件数尚未核实。")
        order = row(QualityOrder.objects.select_for_update().select_related("source_batch", "last_source_batch"), data.get("order_id"), "订单项次")
        _active_order(order, supplement)
        if quantity > order.order_quantity - delivered_quantities_by_order([order.pk])[order.pk]:
            failure("认领量超过本订单项次剩余履约数量。")
        basis = data.get("basis")
        # The replacement cannot be booked first and then silently booked a
        # second time by recognizing its original shipment afterwards.
        if QualityShipmentOrderAllocation.objects.filter(
            shipment_line__batch__supplement__issue__origins__batch=batch, order=order,
        ).exists():
            failure("该项次已认领本包的替换货；原包不可再抵同一履约义务。独立采购须是另一个明确项次。")
        if issue.kind == QualityShipmentBatch.SupplementKind.REPLACEMENT:
            if basis in ("REPLACEMENT_AFTER_RETURN", "REPLACEMENT_WITHOUT_RETURN"):
                related_origins = Q(pk__in=[])
                for origin in issue.origins.all():
                    related_origins |= Q(batch_id=origin.batch_id, unit_no=origin.unit_no)
                matching_origins = ShipmentSupplementOrigin.objects.filter(related_origins)
                related_issue_ids = matching_origins.values_list("issue_id", flat=True)
                covered = QualityShipmentOrderAllocation.objects.filter(
                    shipment_line__batch__supplement__issue_id__in=related_issue_ids, order=order,
                ).aggregate(total=Sum("piece_quantity"))["total"] or 0
                released = effective_supplement_adjustments().filter(origin__issue=issue, allocation__order=order).aggregate(
                    total=Sum("piece_quantity"))["total"] or 0
                returned = _returned_entitlement(issue, order)
                if (not (released if basis == "REPLACEMENT_WITHOUT_RETURN" else returned)
                        or covered + quantity > returned + released):
                    failure("认领量超过该事项已确认核减或实际退回释放的份额，请核对归属和依据。")
            elif basis == "RECONCILED_SINGLE":
                if not issue.origins.exists() or ShipmentSupplementOrigin.objects.filter(
                    issue=issue,
                ).filter(Q(batch__order_id=order.pk) | Q(batch__lines__order_id=order.pk)
                         | Q(batch__lines__process_card__order_id=order.pk) | Q(batch__lines__order_allocations__order_id=order.pk)).exists():
                    failure("原货未核实或已经占用该项次；不能让同一履约义务重复入账。")
            elif basis != "INDEPENDENT":
                failure("质量补发请选择实退补缺、原货未挂本单或独立采购，并填写依据。")
            elif ShipmentSupplementOrigin.objects.filter(issue=issue).filter(
                Q(batch__order_id=order.pk) | Q(batch__lines__order_id=order.pk)
                | Q(batch__lines__process_card__order_id=order.pk)
                | Q(batch__lines__order_allocations__order_id=order.pk)
            ).exists():
                failure("独立采购必须是未被这些原包占用的其他明确项次。")
        # Claims are fungible shares within this one verified package.
        # Reuse a sufficiently large free interval after a correction.
        offset = 0
        for prev in line.order_allocations.order_by("piece_start"):
            if prev.piece_start - offset >= quantity:
                break
            offset = prev.piece_end
        if offset + quantity > line.piece_quantity:
            failure("空余份额分散，请分次认领，不能覆盖原分摊区间。")
        weight = None
        if line.net_weight_kg is not None:
            q = Decimal("0.001")
            total, count = line.net_weight_kg, line.piece_quantity
            end = (total * (offset + quantity) / count).quantize(q, rounding=ROUND_HALF_UP)
            start = (total * offset / count).quantize(q, rounding=ROUND_HALF_UP)
            weight = end - start
            if not weight:
                failure("认领重量不足 0.001 kg，不能以虚假重量入账。")
        highest_seq = line.order_allocations.aggregate(seq=Max("sequence"))["seq"] or 0
        share = QualityShipmentOrderAllocation.objects.create(
            shipment_line=line, order=order, sequence=highest_seq + 1,
            piece_start=offset, piece_end=offset + quantity, piece_quantity=quantity, net_weight_kg=weight,
        )
        sync_order_status_from_delivery([order.pk], source=OrderStatusChange.Source.SHIPMENT,
                                        operator=user, reason_prefix="在原实发记录上确认归属")
        return issue, batch, {"allocation_id": share.pk, "order_id": order.pk, "quantity": quantity,
                              "basis": basis, "note": data["note"]}
    return _atomic_operation(action, data, user, work, batch_id)


def update_issue(issue_id, data, user):
    if not can_reconcile(user):
        raise PermissionDenied("只有有权人员可核对要求量、争议和客户复验。")

    def work():
        issue = row(ShipmentSupplementIssue.objects.all(), issue_id, "补货事项")
        if not data.get("note"):
            failure("请填写核对依据。")
        if data.get("disputed_quantity") and not data.get("dispute_note"):
            failure("争议量须保留依据，不自动改变履约。")
        fields = ("requested_quantity", "disputed_quantity", "dispute_note", "retest_note")
        before = {field: getattr(issue, field) for field in fields}
        for field in fields:
            if field in data:
                setattr(issue, field, data[field])
        issue.save(update_fields=[*fields, "updated_at"])
        _add_origins(issue, data.get("origins", []))
        return issue, None, {"before": before, "after": {field: getattr(issue, field) for field in fields},
                             "note": data["note"], "origins": data.get("origins", [])}
    return _atomic_operation("ISSUE_REVIEW", data, user, work, issue_id)


def item_data(supplement, *, history=False):
    batch, issue = supplement.batch, supplement.issue
    line = batch.lines.get()
    shares = list(line.order_allocations.all())
    returned = _return_case(batch)
    used = sum(share.piece_quantity for share in shares)
    pending = None if line.piece_quantity is None else line.piece_quantity - used
    status = ("RETURNED" if returned else "NO_CLAIM" if supplement.no_claim_reason else
              "QUANTITY_UNKNOWN" if pending is None else "PENDING" if pending else "CLAIMED")
    data = {
        "id": batch.pk, "issue_id": issue.pk, "kind": issue.kind, "shipment_no": batch.shipment_no,
        "package_no": package_no(batch), "date": batch.shipment_date, "recorded_at": batch.created_at,
        "customer": "翔邦", "customer_code": issue.customer_code, "project_no": issue.project_no,
        "product_id": batch.product_specification_id, "product_name": batch.product_name_snapshot,
        "specification": batch.specification_snapshot, "material": batch.material_snapshot,
        "quantity": line.piece_quantity, "weight_kg": str(line.net_weight_kg) if line.net_weight_kg is not None else None,
        "quantity_basis": ("UNKNOWN" if line.piece_quantity is None else
                           "COUNT" if supplement.amount_method == "COUNT" or line.unit_weight_g_snapshot is None
                           else "WEIGHT_CONVERTED"),
        "customer_card_no": supplement.customer_card_no, "reused_card_no": supplement.reused_card_no,
        "inspector": batch.inspector.name if batch.inspector_id else None,
        "internal_card_id": supplement.internal_card_id, "backfill_reason": batch.backfill_reason,
        "unit_weight_g": line.unit_weight_g_snapshot, "recorded_by": batch.created_by_id,
        "delivery_info": batch.delivery_info, "source_note": supplement.source_note,
        "state": status, "no_claim_reason": supplement.no_claim_reason,
        "claimed_quantity": used, "claimable": 0 if returned or supplement.no_claim_reason else pending,
        "return_id": returned.pk if returned else None, "returned_quantity": returned.affected_quantity if returned else None,
        "returned_weight_kg": returned.affected_weight_kg if returned else None,
        "claims": [{"id": s.pk, "order_id": s.order_id, "order_no": s.order_no_snapshot,
                    "item_no": s.item_no_snapshot, "quantity": s.piece_quantity} for s in shares],
    }
    if history:
        data["history"] = list(ShipmentSupplementEvent.objects.filter(batch=batch)
                               .order_by("-pk").values("action", "details", "created_at", "actor_id")[:100])
    return data


def issue_data(issue, *, include_shipments=False):
    sent, unknown = _progress(issue)
    data = {
        "id": issue.pk, "reference": f"BH-{issue.reference.hex[:12].upper()}", "kind": issue.kind,
        "product_id": issue.product_id, "product_name": issue.product.product_name,
        "specification": issue.product.specification, "material": issue.product.material,
        "customer_code": issue.customer_code, "project_no": issue.project_no,
        "reason": issue.reason, "requested_quantity": issue.requested_quantity,
        "shipped_quantity": sent, "unknown_packages": unknown, "package_count": issue.shipments.count(),
        "remaining": max(0, issue.requested_quantity - sent) if issue.requested_quantity is not None and not unknown else None,
        "retest_note": issue.retest_note, "disputed_quantity": issue.disputed_quantity, "dispute_note": issue.dispute_note,
        "moved_out": _moved_out(issue),
        "origins": list(issue.origins.values("id", "batch_id", "unit_no", "card_no_snapshot", "batch__shipment_no", "batch__supplement_kind")),
    }
    if include_shipments:
        data["shipments"] = [item_data(s) for s in issue.shipments.select_related("batch__inspector", "issue").order_by("-batch__shipment_date", "-pk")]
        data["release_options"] = [item for origin in issue.origins.select_related("issue", "batch")
                                   for item in _release_origin_options(origin)]
        active = set(effective_supplement_adjustments().filter(origin__issue=issue).values_list("pk", flat=True))
        data["delivery_releases"] = [{"id": entry.pk, "shipment_no": entry.origin.batch.shipment_no,
            "unit_no": entry.unit_no, "order_no": entry.allocation.order_no_snapshot, "item_no": entry.allocation.item_no_snapshot,
            "order_id": entry.allocation.order_id, "quantity": entry.piece_quantity, "note": entry.evidence,
            "active": entry.pk in active, "created_at": entry.created_at}
            for entry in ShipmentSupplementDeliveryAdjustment.objects.filter(origin__issue=issue)
            .select_related("origin__batch", "allocation").order_by("pk")]
        data["delivery_release_history"] = list(ShipmentSupplementEvent.objects.filter(issue=issue, action="DELIVERY_RELEASE")
            .order_by("-pk").values("id", "actor_id", "created_at", "details")[:100])
    return data


def lookup_origins(code):
    code = code.strip()
    if not code:
        failure("请扫描或输入完整卡号、出货单号、ERP 包号或送货单号。")
    normalized = card_key(code)
    exact_card = ProcessCard.objects.filter(card_no__iexact=normalized).first()
    card_ids = list(ProcessCard.objects.filter(tracking_id=exact_card.tracking_id).values_list("pk", flat=True)) if exact_card else []
    matches = batch_ids_for_cards(card_ids) if card_ids else set()
    package_match = re.fullmatch(r"(QS-[A-Z0-9-]+)/([1-9][0-9]{0,7})", code, flags=re.IGNORECASE)
    shipment_no = package_match.group(1) if package_match else code
    matches.update(QualityShipmentBatch.objects.filter(
        Q(shipment_no__iexact=shipment_no) | Q(delivery_info__iexact=code) | Q(supplement__customer_card_key=normalized)
    ).values_list("pk", flat=True))
    batches = QualityShipmentBatch.objects.filter(pk__in=matches, status="CONFIRMED").order_by("-shipment_date", "pk")
    origins = []
    truncated = set()
    for batch in batches:
        if package_match and batch.shipment_no.upper() == shipment_no.upper():
            units = [int(package_match.group(2))]
        elif batch.supplement_kind:
            units = [1]
        elif exact_card:
            refs = [ref for ref in shipment_card_sources(batch) if ref.get("card_id") in card_ids]
            explicit_units = {ref["shipment_unit_no"] for ref in refs if ref.get("shipment_unit_no")}
            direct_lines = {ref["line_id"] for ref in refs if ref.get("line_id") and "LINE" in ref["evidence"]}
            units = sorted(explicit_units | {
                g["first_unit_no"] for g in shipment_return_groups(batch)
                if g["first_unit_no"] == g["last_unit_no"] and any(line.pk in direct_lines for line in g["lines"])
            })
        else:
            groups = shipment_return_groups(batch)
            if any(g["last_unit_no"] - g["first_unit_no"] >= 100 for g in groups):
                truncated.add(batch.shipment_no)
            units = [unit for group in groups
                     for unit in range(group["first_unit_no"], min(group["last_unit_no"] + 1, group["first_unit_no"] + 100))]
        for unit in units:
            try:
                product_id, matched, (customer, project) = _shipment_facts(batch, unit)
            except ValidationError:
                continue
            case = batch.rework_cases.filter(shipment_unit_no=unit, origin="CUSTOMER_RETURN").exclude(status="CANCELLED").first()
            origins.append({"batch_id": batch.pk, "unit_no": unit, "product_id": product_id,
                            "customer_code": customer, "project_no": project, "card_no": matched,
                            "shipment_no": batch.shipment_no, "date": batch.shipment_date,
                            "specification": batch.specification_snapshot, "material": batch.material_snapshot,
                            "return_id": case.pk if case else None, "is_supplement": bool(batch.supplement_kind)})
    selection = {(origin["batch_id"], origin["unit_no"]) for origin in origins}
    related_issues = {issue_id for batch_id, unit_no, issue_id in ShipmentSupplementOrigin.objects.filter(
        batch_id__in={batch_id for batch_id, _ in selection},
    ).values_list("batch_id", "unit_no", "issue_id") if (batch_id, unit_no) in selection}
    replacements = ShipmentSupplement.objects.filter(issue_id__in=related_issues).select_related("batch__inspector", "issue")
    return {"origins": origins, "replacements": [item_data(item) for item in replacements],
             "related_issues": [issue_data(issue) for issue in ShipmentSupplementIssue.objects.filter(pk__in=related_issues).order_by("-pk")],
            "unverified_shipment_ids": sorted(matches - {o["batch_id"] for o in origins}),
            "truncated_shipments": sorted(truncated)}
