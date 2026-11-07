"""Read-only workbench and order trace, derived only from explicit relations."""
from collections import Counter
from datetime import date, timedelta
from urllib.parse import urlencode

from django.db.models import Prefetch, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import NotFound

from inventory.models import InventoryContainer, InventoryOutbound
from production.models import ProductionDailyLog, ProductionRun
from quality.models import (
    ProcessCard, QualityOrder, QualityReworkCase,
    QualityReturnAllocation, QualityShipment, QualityShipmentLine, ReturnRework,
)
from quality.services import (
    PENDING_RETURN_STATUSES, delivered_quantities_by_order, shipment_line_piece_quantity,
)
from quality.tracing import attach_shipment_card_sources
from .models import MaterialReceipt
from .services import production_totals_by_order, order_production_requirement_status


SECTIONS = (
    "production", "receipts", "cards", "shipments", "piece_shipments",
    "returns", "piece_returns", "inventory",
)
KINDS = ("ORDER_SHORTAGE", "RETURN_PENDING", "INVENTORY_QUALITY", "RECEIPT_UNLINKED")


class ReadQuery(serializers.Serializer):
    q = serializers.CharField(required=False, default="", max_length=200, allow_blank=True)
    kind = serializers.ChoiceField(choices=("", *KINDS), default="", required=False)
    due_scope = serializers.ChoiceField(choices=("", "today", "overdue", "upcoming"), default="", required=False)
    section = serializers.ChoiceField(choices=SECTIONS, default="production", required=False)
    page = serializers.IntegerField(min_value=1, default=1, required=False)
    page_size = serializers.IntegerField(min_value=1, max_value=50, default=20, required=False)


def page_rows(rows, page, page_size, *, count=None):
    count = len(rows) if count is None else count
    start = (page - 1) * page_size
    if start >= count and page != 1:
        raise NotFound("该页不存在，请返回第一页重新查询。")
    return {"count": count, "page": page, "page_size": page_size,
            "results": list(rows[start:start + page_size])}


def identity(order):
    return {"id": order.pk, "order_no": order.order_no, "item_no": order.item_no,
            "specification": order.specification, "material": order.material,
            "product_name": order.product_name, "status": order.status,
            "due_date": order.due_date, "order_quantity": order.order_quantity}


def build_workbench(*, q="", kind="", due_scope="", page=1, page_size=20, **_):
    tasks = []
    orders = list(QualityOrder.objects.filter(status="OPEN").only(
        "id", "order_no", "item_no", "specification", "material", "product_name",
        "order_quantity", "due_date", "status",
    ))
    delivered = delivered_quantities_by_order([order.pk for order in orders])
    for order in orders:
        remaining = max(order.order_quantity - delivered[order.pk], 0)
        if remaining:
            tasks.append({
                "id": f"order:{order.pk}", "kind": "ORDER_SHORTAGE", "title": "订单欠交",
                "document_no": f"{order.order_no} / {order.item_no or '未登记项次'}",
                "specification": order.specification, "material": order.material,
                "quantity": remaining, "unit": "件", "due_date": order.due_date,
                "order_id": order.pk, "owner": None,
                "reason": "需求量减净有效交付；不是生产欠数，也不是已过交期判定。",
                "source": "QualityOrder＋有效交付", "url": f"/order-overview/{order.pk}",
            })
    for case in QualityReworkCase.objects.filter(
        Q(status__in=PENDING_RETURN_STATUSES)
        | Q(status=QualityReworkCase.Status.COMPLETED, reshipment_batch__isnull=True)
    ).select_related(
        "process_card__order", "shipment_batch", "shipment_line__order",
    ):
        order = case.process_card.order if case.process_card_id else (
            case.shipment_line.order if case.shipment_line_id else None
        )
        tasks.append({
            "id": f"return:{case.pk}", "kind": "RETURN_PENDING", "title": "退回/返工待处理",
            "document_no": case.case_no, "specification": order.specification if order else (case.shipment_batch.specification_snapshot if case.shipment_batch_id else ""),
            "material": order.material if order else (case.shipment_batch.material_snapshot if case.shipment_batch_id else ""), "quantity": case.affected_quantity,
            "unit": "件", "due_date": None, "order_id": order.pk if order else None,
            "owner": None, "reason": f"{case.get_status_display()}；" + ("尚未登记再发；" if case.status == QualityReworkCase.Status.COMPLETED else "") + "责任品检员不自动作为处理负责人。",
            "source": "QualityReworkCase", "url": "/quality?" + urlencode({"tab": "reworks", "q": case.case_no, "rework_case_id": case.pk}),
        })
    for container in InventoryContainer.objects.filter(status="ACTIVE").exclude(
        batch__quality_status="PASSED"
    ).select_related("batch__product", "location"):
        product = container.batch.product
        tasks.append({
            "id": f"inventory:{container.pk}", "kind": "INVENTORY_QUALITY", "title": "库存待质量处理",
            "document_no": container.container_code, "specification": product.specification,
            "material": product.material, "quantity": container.quantity, "unit": "件",
            "due_date": None, "order_id": None, "owner": None,
            "reason": f"{container.batch.get_quality_status_display()}；位置：{container.location.code if container.location_id else '未登记'}",
            "source": "InventoryContainer＋Batch", "url": "/inventory?" + urlencode({"location_code": container.location.code}) if container.location_id else "/inventory",
        })
    for receipt in MaterialReceipt.objects.filter(order__isnull=True):
        tasks.append({
            "id": f"receipt:{receipt.pk}", "kind": "RECEIPT_UNLINKED", "title": "发料待关联",
            "document_no": f"{receipt.order_no} / {receipt.item_no or '未登记项次'}",
            "specification": receipt.specification, "material": receipt.material,
            "quantity": receipt.weight_kg, "unit": "kg", "due_date": None,
            "order_id": None, "owner": None, "reason": "有发料记录但没有明确订单关联；不能当作到料0。",
            "source": "MaterialReceipt", "url": f"/orders?tab=receipts&linked=false&receipt_id={receipt.pk}",
        })
    term = q.casefold().strip()
    if term:
        tasks = [row for row in tasks if term in " ".join(str(row[key] or "") for key in (
            "document_no", "specification", "material", "title", "reason",
        )).casefold()]
    today = timezone.localdate()
    if due_scope:
        tasks = [row for row in tasks if row["due_date"] and (
            (due_scope == "today" and row["due_date"] == today)
            or (due_scope == "overdue" and row["due_date"] < today)
            or (due_scope == "upcoming" and today < row["due_date"] <= today + timedelta(days=7))
        )]
    counts = Counter(row["kind"] for row in tasks)
    selected = [row for row in tasks if not kind or row["kind"] == kind]
    selected.sort(key=lambda row: (row["due_date"] is None, row["due_date"] or date.max, row["kind"], row["id"]))
    return {
        **page_rows(selected, page, page_size),
        "summary": {key: counts[key] for key in KINDS},
        "summary_scope": "全部匹配搜索及交期范围的事项条数（不受当前类别和分页影响）",
        "generated_at": timezone.now(),
        "limitations": [
            "这是已有记录的只读待处理清单，不代表全厂所有在制品。",
            "各类别是事项条数，不是可相加的产品件数；没有自动指派负责人。",
            "二烤、去边、机检等缺实际工序记录时不能推断进度。",
        ],
    }


def section_query(order, section):
    if section == "production":
        return ProductionRun.objects.filter(Q(order=order) | Q(order_links__order=order)).distinct().select_related("station").prefetch_related(
            Prefetch("daily_logs", queryset=ProductionDailyLog.objects.select_related("employee", "operator_employee").prefetch_related("assistant_employees", "assistant_operators")),
            "order_links__order",
        ).order_by("-id")
    if section == "receipts":
        return MaterialReceipt.objects.filter(order=order).order_by("-id")
    if section == "cards":
        return ProcessCard.objects.filter(order=order).order_by("-id")
    if section == "shipments":
        return QualityShipmentLine.objects.filter(
            Q(order_allocations__order=order) | Q(order=order) | Q(process_card__order=order)
        ).distinct().select_related("batch", "process_card").prefetch_related("order_allocations").order_by("-batch__shipment_date", "-id")
    if section == "piece_shipments":
        return QualityShipment.objects.filter(order=order).order_by("-shipment_date", "-id")
    if section == "returns":
        return QualityReworkCase.objects.filter(
            Q(shipment_allocations__shipment_order_allocation__order=order)
            | Q(shipment_allocations__shipment_line__order=order)
            | Q(process_card__order=order) | Q(shipment_line__order=order)
            | Q(shipment_batch__order=order)
        ).distinct().select_related("shipment_batch", "reshipment_batch").prefetch_related(
            "attempts", Prefetch("shipment_allocations", queryset=QualityReturnAllocation.objects.select_related("shipment_order_allocation", "shipment_line")),
        ).order_by("-id")
    if section == "piece_returns":
        return ReturnRework.objects.filter(shipment__order=order).select_related("shipment").order_by("-id")
    return InventoryOutbound.objects.filter(order=order).prefetch_related("lines__container__batch", "lines__container__location").order_by("-id")


def trace_row(obj, section, order, binding_cards):
    row = {"id": obj.pk, "source": obj.__class__.__name__, "document_no": str(obj.pk),
           "date": None, "status": "", "physical_quantity": None, "own_quantity": None,
           "unit": "件", "details": "", "cards": [], "url": None}
    if section == "production":
        distribution = obj.order_distribution()
        own = sum(quantity for link, quantity in distribution if link.order_id == order.pk)
        if not distribution and obj.order_id == order.pk:
            own = obj.qualified_production_quantity
        if obj.status == "CANCELLED":
            own = 0
        logs = [log for log in obj.daily_logs.all() if not log.is_cancelled]
        people = set()
        for log in logs:
            name = log.employee.name if log.employee_id else log.operator_employee.name if log.operator_employee_id else log.operator
            if name:
                people.add(name)
            people.update(person.name for person in log.assistant_employees.all())
            people.update(person.name for person in log.assistant_operators.all())
        def local_time(value):
            return timezone.localtime(value).strftime("%Y-%m-%d %H:%M") if value else "未登记"
        row.update(document_no=f"生产任务#{obj.pk}", date=obj.loaded_at, status=obj.get_status_display(),
                   physical_quantity=obj.produced_mold_count * obj.cavities, own_quantity=own,
                   details=f"机台：{obj.station.code if obj.station_id else '未登记'}；参与员工：{'、'.join(sorted(people)) or '未登记'}；上机：{local_time(obj.loaded_at)}；下机：{local_time(obj.unloaded_at)}。物理件数为模数折算；本单为交期分配进度，不是实测良品。孔数按有效日志快照。", url="/production")
        row["physical_quantity"] = sum(int(log.produced_mold_count) * int(log.cavities_snapshot or obj.cavities) for log in obj.daily_logs.all() if not log.is_cancelled) if obj.status != "CANCELLED" else 0
    elif section == "receipts":
        row.update(document_no=f"发料#{obj.pk} {obj.batch_no}", date=obj.issued_on, physical_quantity=obj.weight_kg,
                   own_quantity=obj.weight_kg, unit="kg", details="明确关联订单；不含未关联发料或重复手工量。")
    elif section == "cards":
        row.update(document_no=obj.card_no, date=obj.received_on, status=obj.get_status_display(),
                   physical_quantity=obj.quantity, cards=[obj.card_no], details="来源订单的流程卡，不代表全部已交数量。")
    elif section == "shipments":
        shares = list(obj.order_allocations.all())
        own = sum(share.piece_quantity for share in shares if share.order_id == order.pk)
        if not shares:
            own = shipment_line_piece_quantity(obj)
        cards = [obj.process_card.card_no] if obj.process_card_id else binding_cards.get(obj.batch_id, [])
        row.update(document_no=obj.batch.shipment_no, date=obj.batch.shipment_date,
                   status=obj.batch.get_status_display(), physical_quantity=shipment_line_piece_quantity(obj),
                   own_quantity=own, cards=cards, url=f"/quality?shipment_batch_id={obj.batch_id}",
                   details=f"明细#{obj.pk}，净重{obj.net_weight_kg}kg；本单为原出货归属（尚未扣本页退回）。" + (" 卡号来自系统明确关联及历史证据，详见出货单；未逐行猜配，不按分摊猜配纸卡原始订单。" if not obj.process_card_id and cards else ""))
    elif section == "piece_shipments":
        row.update(document_no=obj.shipment_no, date=obj.shipment_date, physical_quantity=obj.shipped_quantity,
                   own_quantity=obj.shipped_quantity, details="件数出货；无流程卡可能为合法采购出货，不补造卡。", url="/quality")
    elif section == "returns":
        shares = list(obj.shipment_allocations.all())
        own = sum(share.piece_quantity for share in shares if (
            share.shipment_order_allocation.order_id == order.pk if share.shipment_order_allocation_id else share.shipment_line.order_id == order.pk
        )) if shares else None
        row.update(document_no=obj.case_no, date=obj.opened_on, status=obj.get_status_display(),
                   physical_quantity=obj.affected_quantity, own_quantity=own, url=f"/quality?tab=reworks&rework_case_id={obj.pk}",
                   details=f"第{obj.return_round or '未登记'}轮；原单{obj.shipment_batch.shipment_no if obj.shipment_batch_id else '未关联'}；再发{obj.reshipment_batch.shipment_no if obj.reshipment_batch_id else '未登记'}；实际作业{len(obj.attempts.all())}条（0条表示未登记，不代表未返工）。")
    elif section == "piece_returns":
        row.update(document_no=f"退回#{obj.pk} / {obj.shipment.shipment_no}", date=obj.rework_date,
                   physical_quantity=obj.returned_quantity, own_quantity=obj.returned_quantity, details="件数退回，引用原件数出货。")
    else:
        row.update(document_no=obj.outbound_no, date=obj.created_at, status=obj.get_status_display(),
                   physical_quantity=sum(line.quantity for line in obj.lines.all()), own_quantity=None,
                   details=f"仅明确订单引用的人工出库；参考号{obj.shipment_ref or '未登记'}；不再计客户交付，不表示这些容器当前余额归本订单。", url="/inventory")
    return row


def build_order_overview(order_id, *, section="production", page=1, page_size=20, **_):
    order = get_object_or_404(QualityOrder, pk=order_id)
    delivered = delivered_quantities_by_order([order.pk])[order.pk]
    produced = production_totals_by_order([order.pk])[order.pk][0]
    queries = {key: section_query(order, key) for key in SECTIONS}
    counts = {key: queryset.count() for key, queryset in queries.items()}
    queryset = queries[section]
    start = (page - 1) * page_size
    if start >= counts[section] and page != 1:
        raise NotFound("该页不存在，请返回第一页。")
    objects = list(queryset[start:start + page_size])
    bindings = {}
    if section == "shipments":
        batches = {obj.batch_id: obj.batch for obj in objects}
        attach_shipment_card_sources(batches.values())
        bindings = {pk: list(dict.fromkeys(ref["card_no"] for ref in batch.trace_card_sources)) for pk, batch in batches.items()}
    return {
        "order": identity(order), "generated_at": timezone.now(),
        "summary": {"net_delivered": delivered, "delivery_remaining": max(order.order_quantity - delivered, 0),
                    "production_progress_quantity": produced,
                    "production_status": order_production_requirement_status(
                        status=order.status, production_required=order.production_required,
                        order_quantity=order.order_quantity, produced_quantity=produced, delivered_quantity=delivered,
                    )},
        "counts": counts, "section": section, "count": counts[section], "page": page, "page_size": page_size,
        "results": [trace_row(obj, section, order, bindings) for obj in objects],
        "limitations": [
            "只读串联明确关系，不猜配同规格历史。原出货和退回分列，净交付以顶部统一口径为准。",
            "生产→后工序→成品包缺统一身份，未找到关系不代表现场没做。",
            "库存独立管理，只列明确引用本订单的出库，不把同规格库存当本订单剩余。",
        ],
    }
