"""Opt-in preparation. GETs are projections; only explicit commands persist.

This module does not post production, inspection, inventory or delivery quantities.
Existing frozen groups are deliberately outside the new planning algorithm.
"""
from collections import defaultdict
from copy import deepcopy
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError

from erp.permissions import is_read_only_user
from orders.identities import order_customer_key
from orders import customer_inspection as ci
from orders.models import ProductInspectionCriterion, ProductSpecification
from production.models import ProductionRun
from quality.models import QualityOrder
from .models import CardGroup, OrderCardPreparation, StandardRevision, TraceAudit


class PreparationConflict(APIException):
    status_code = 409
    default_detail = "准备资料已被其他人修改，请重新读取并核对差异。"


def enabled():
    return settings.FLOW_CARDS_PREPARATION_ENABLED


def can_maintain(user):
    return bool(user.is_authenticated and not is_read_only_user(user)
                and (user.is_superuser or user.has_perm("flowcards.maintain_product_standard")))


def require_maintainer(user):
    if not can_maintain(user):
        raise PermissionDenied("只有产品标准维护人员可保存或生效全厂标准；操作员可保存本次准备及实测。")


def require_enabled():
    from .services import write_enabled
    write_enabled()
    if not enabled():
        raise PermissionDenied("订单自动备卡尚未启用；原卡仍可查看和补打。")


def sync_order_suggestion(order, user):
    """Called ONLY by a successful import or explicit production confirmation."""
    sync_order_suggestions([order], user)


def sync_order_suggestions(orders, user):
    """Batch projection avoids per-order delivery/production aggregation on import."""
    if not enabled():
        return
    orders = list({o.pk: o for o in orders}.values())
    if not orders:
        return
    from orders.services import production_totals_by_order, order_production_requirement_status
    from quality.services import delivered_quantities_by_order
    ids = [o.pk for o in orders]
    produced = production_totals_by_order(ids)
    delivered = delivered_quantities_by_order(ids)
    existing = {r.order_id: r for r in OrderCardPreparation.objects.filter(order_id__in=ids)}
    created, updated, now = [], [], timezone.now()
    for order in orders:
        eligible = order.production_required is True and order.status == QualityOrder.Status.OPEN
        if eligible:
            eligible = order_production_requirement_status(status=order.status, production_required=True,
                order_quantity=order.order_quantity, produced_quantity=produced.get(order.pk, (0, 0))[0], delivered_quantity=delivered.get(order.pk, 0)) == "REQUIRED"
        row = existing.get(order.pk)
        if row:
            row.state = (row.state if row.draft_id else "PENDING") if eligible else "NOT_PENDING"
            row.updated_at = now
            updated.append(row)
        elif eligible:
            created.append(OrderCardPreparation(order=order, created_by=user))
    OrderCardPreparation.objects.bulk_create(created)
    OrderCardPreparation.objects.bulk_update(updated, ["state", "updated_at"])


def run_order_ids(run):
    ids = set(run.order_links.values_list("order_id", flat=True))
    if run.order_id:
        ids.add(run.order_id)
    return sorted(ids)


def run_rows(order):
    return ProductionRun.objects.filter(Q(order=order) | Q(order_links__order=order)).distinct().order_by("id")


def is_open_run(run):
    return run.status not in (ProductionRun.Status.COMPLETED, ProductionRun.Status.CANCELLED)


def resolve(params):
    from .services import get_row
    run = get_row(ProductionRun, params["run_id"], "run_id") if params.get("run_id") else None
    order = get_row(QualityOrder, params["order_id"], "order_id") if params.get("order_id") else None
    candidates = list(run_rows(order).select_related("station", "mold")) if order else ([run] if run else [])
    if run and order and order.pk not in run_order_ids(run):
        raise ValidationError({"run_id": "订单不属于指定生产段。"})
    if order and not run and params.get("order_plan") not in (True, "1", "true"):
        active = [r for r in candidates if is_open_run(r) and r.product_specification_id == order.product_specification_id]
        if len(active) == 1:
            run = active[0]
    pid = params.get("product_id") or (run.product_specification_id if run else None) or (order.product_specification_id if order else None)
    product = get_row(ProductSpecification, pid, "product_id")
    if (run and run.product_specification_id != product.pk) or (order and order.product_specification_id != product.pk):
        raise ValidationError({"product_id": "来源未明确关联同一产品，先核对产品档案。"})
    ids = run_order_ids(run) if run else [order.pk] if order else []
    orders = list(QualityOrder.objects.filter(pk__in=ids).select_related("source_batch", "last_source_batch").order_by("id"))
    return product, run, orders, candidates


def applicable_standards(product, run, orders, inspections=None):
    if not run and not orders:
        # Product-profile inspection is not an applicability decision for a job.
        return list(StandardRevision.objects.filter(product=product).order_by("-version"))
    customers = {order_customer_key(o) for o in orders}
    customer = next(iter(customers)) if len(customers) == 1 else None
    if inspections is None:
        inspections = ci.get_order_snapshots(orders)
    codes = {s["customer_code"] for s in inspections.values()}
    customer_code = next(iter(codes)) if len(codes) == 1 and len(inspections) == len(orders) else ""
    qs = StandardRevision.objects.filter(product=product).filter(Q(mold__isnull=True) | Q(mold_id=run.mold_id if run else None))
    # Unknown customer never grants applicability to a different known customer.
    scope = Q(customer_key="") | Q(customer_key=customer or "")
    if customer_code:
        scope = (Q(customer_key="") | Q(customer_key__iexact=customer_code) |
                 Q(customer_key=customer or "", source_kind__in=("FACTORY_PROCESS", "FACTORY_EXPERIENCE")))
    qs = qs.filter(scope)
    ids = [o.pk for o in orders]
    qs = qs.filter(Q(applicable_order__isnull=True) | Q(applicable_order_id__in=ids))
    result = []
    for row in qs.order_by("-version"):
        applicability = row.snapshot.get("applicability", {})
        project_nos = {v["project_no"] for v in inspections.values() if v["project_no"]} or {product.customer_product_no}
        if applicability.get("customer_product_no") and any(ci.code(v) != ci.code(applicability["customer_product_no"]) for v in project_nos):
            continue
        if applicability.get("station_id") and (not run or applicability["station_id"] != run.station_id):
            continue
        if row.applicable_order_id and len(ids) != 1:
            continue
        result.append(row)
    return result


def require_compatible_orders(product, orders, standard):
    # An order exception is not proof that the other orders share it. Split the
    # physical scope explicitly instead of silently adopting a base version.
    if len(orders) <= 1:
        return
    exceptions = StandardRevision.objects.filter(product=product, status="ACTIVE", applicable_order__in=orders)
    def signature(row):
        snapshot = row.snapshot
        criteria = sorted(tuple(str(c.get(key, "")) for key in ("name", "stage", "lower", "upper", "unit", "limit_mode", "upper_inclusive", "lower_inclusive", "method", "requirement_state", "follow_package")) for c in snapshot.get("criteria", []))
        return (row.whole_mold, criteria, snapshot.get("route", {}), snapshot.get("process_parameters", {}), snapshot.get("hardness", {}), snapshot.get("bake", {}))
    if any(standard is None or signature(row) != signature(standard) for row in exceptions):
        raise ValidationError({"order_ids": "共单包含独立生效的订单特规且技术要求不一致；请先核对技术兼容性并明确拆段，不自动合用通用标准。"})


def source_rows(product, orders):
    ids = [o.pk for o in orders]
    customers = {order_customer_key(o) for o in orders}
    qs = ProductInspectionCriterion.objects.filter(product_specification=product).filter(Q(order__isnull=True) | Q(order_id__in=ids)).select_related("source_batch")
    return [c for c in qs.order_by("id") if not customers or not c.customer or c.customer.casefold() in customers
            or (c.order_id in ids and c.source_batch and c.source_batch.source_type == ci.FACTORY)]


def criterion_data(c):
    raw = c.raw_data or {}
    return {"id": c.pk, "order_id": c.order_id, "customer": c.customer, "inspection_item": c.inspection_item,
            "lower_limit": c.lower_limit, "upper_limit": c.upper_limit, "unit": c.unit,
            "source_batch_id": str(c.source_batch_id) if c.source_batch_id else None,
            "source_file": c.source_batch.original_name if c.source_batch_id else "",
            "source_url": f"/api/flow-cards/source-files/{c.source_batch_id}/" if c.source_batch_id else None,
            "source_sheet": c.source_sheet, "source_row": c.source_row, "version": c.version, "category": c.category, "project_no": c.project_no,
            "raw_data": raw, "stage": raw.get("stage"), "method": raw.get("method", ""),
            "limit_mode": raw.get("limit_mode"), "upper_inclusive": raw.get("upper_inclusive", True),
            "lower_inclusive": raw.get("lower_inclusive", True)}


def conflict_items(product, orders, standard):
    """Compare source identities, not aliases inferred from item codes."""
    rows = source_rows(product, orders)
    by_name = defaultdict(list)
    for row in rows:
        # Unclassified Xiangbang source groups are printable references. Neither
        # 2001 nor 6001 is assigned to an actual shop-floor inspection stage.
        if row.order_id and row.source_batch and row.source_batch.source_type == ci.FACTORY:
            continue
        by_name[(row.inspection_item, (row.raw_data or {}).get("stage", ""))].append(row)
    chosen = standard.snapshot.get("criteria", []) if standard else []
    problems = []
    for (name, stage), values in by_name.items():
        signatures = {(c.lower_limit, c.upper_limit, c.unit, (c.raw_data or {}).get("upper_inclusive", True), (c.raw_data or {}).get("lower_inclusive", True)) for c in values}
        chosen_source_ids = {v.get("id") for v in chosen} | {v.get("overrides_criterion_id") for v in chosen}
        missing_special = [c for c in values if c.order_id and c.pk not in chosen_source_ids]
        if len(signatures) > 1 or missing_special:
            selected_ids = [v["id"] for v in chosen if v.get("id") in {c.pk for c in values}]
            problems.append({"key": f"{stage}:{name}", "name": name, "stage": stage,
                             "sources": [criterion_data(c) for c in values],
                             "selected_ids": selected_ids,
                             "resolved_by_standard": len(selected_ids) == 1 and not missing_special,
                             "missing_order_ids": [c.pk for c in missing_special]})
    return problems


def groups_for(run, orders, product):
    qs = CardGroup.objects.filter(product=product)
    if run:
        # An order-only draft is intentionally discoverable after a task is added.
        qs = qs.filter(Q(run=run) | Q(run__isnull=True, orders__in=orders))
    elif orders:
        qs = qs.filter(orders__in=orders)
    return qs.distinct().order_by("-created_at")


def ranges_for(run, exclude=None):
    if not run:
        return []
    return list(CardGroup.objects.filter(run=run, range_start__isnull=False).exclude(pk=exclude).order_by("range_start").values("id", "range_start", "range_end", "status"))


def free_ranges(total, occupied):
    if not total:
        return []
    result, cursor = [], 1
    for row in occupied:
        if row["range_start"] > cursor:
            result.append([cursor, min(total, row["range_start"] - 1)])
        cursor = max(cursor, row["range_end"] + 1)
    if cursor <= total:
        result.append([cursor, total])
    return result


def context(params, user):
    from .guidance import summary
    from .services import standard_data
    from .views import group_data
    product, run, orders, runs = resolve(params)
    inspections = ci.get_order_snapshots(orders)
    standards = applicable_standards(product, run, orders, inspections)
    active = [s for s in standards if s.status == "ACTIVE"]
    # Order exceptions must be explicitly selected if they coexist with a base.
    chosen = active[0] if orders and len(active) == 1 else None
    if params.get("standard_id"):
        chosen = next((s for s in standards if str(s.pk) == str(params["standard_id"])), None)
        if chosen is None:
            raise ValidationError({"standard_id": "所选版本不适用当前客户/订单/模具。"})
    conflicts = conflict_items(product, orders, chosen)
    groups = list(groups_for(run, orders, product))
    drafts = [g for g in groups if g.status == "DRAFT"]
    preferred = drafts[0] if len(drafts) == 1 else next((g for g in groups if g.status == "GENERATED" and g.run_id == (run.pk if run else None)), None)
    if preferred is None:
        preferred = next((g for g in groups if g.status == "GENERATED" and (not run or g.run_id is None)), None)
    occupied = ranges_for(run)
    available = free_ranges(run.planned_mold_count, occupied) if run and is_open_run(run) else []
    missing = []
    if not chosen:
        missing.append("唯一适用的生效标准（可先保存待准备）")
    if any(not c["resolved_by_standard"] for c in conflicts):
        missing.append("逐项核对来源差异/订单特规")
    if len(drafts) > 1:
        missing.append("多份历史准备尚未合并，请明确选择原草稿，不自动覆盖")
    if run and is_open_run(run) and not available and not drafts:
        missing.append("本段已全部备卡，请查看原卡，不重发整套")
    process = {k: getattr(product, k) for k in ("primary_curing", "secondary_curing", "total_cavities", "effective_cavities", "actual_material_length", "actual_cut_weight", "large_strip_weight_g", "large_strip_count", "small_strip_weight_g", "small_strip_count", "forming_material_weight_g") if hasattr(product, k)}
    visible_groups = groups[:20]
    review_groups = {g.pk:g for g in [*visible_groups, *([preferred] if preferred else [])] if g.status == "GENERATED"}
    original_reviews = {}
    for identity, g in review_groups.items():
        frozen = (g.snapshot.get("standard") or {}).get("snapshot", {})
        comparison = summary(product, snapshot={**frozen, "process_parameters": g.snapshot.get("process_parameters", frozen.get("process_parameters", {}))})
        original_reviews[str(identity)] = [r for r in comparison if r["key"] in ("primary_curing_time", "pressure") and r["warning"] and (r.get("raw_reference") or r["value"] != "未提供")]
    return {"preparation_enabled": True, "write_enabled": settings.FLOW_CARDS_WRITE_ENABLED,
            "can_maintain_standard": can_maintain(user),
            "product": {"id": product.pk, "name": product.product_name, "specification": product.specification, "material": product.material, "customer_product_no": product.customer_product_no},
            "order_plan": not bool(run), "run_id": run.pk if run else None, "mold_id": run.mold_id if run else None,
            "order_ids": [o.pk for o in orders], "orders": [card_order(o, inspections) for o in orders],
            "runs": [{"id": r.pk, "segment_no": r.segment_no, "status": r.status, "status_display": r.get_status_display(), "station_id": r.station_id, "mold_id": r.mold_id, "planned_molds": r.planned_mold_count, "historical": not is_open_run(r), "ranges": ranges_for(r)} for r in runs],
            "suggested_molds": available[0][1] - available[0][0] + 1 if available else None,
            "available_ranges": available, "occupied_ranges": occupied,
            "preferred_group": group_data(preferred) if preferred else None,
            "existing_groups": [group_data(g) for g in visible_groups], "missing": missing,
            "original_reference_reviews": original_reviews,
            "guidance": summary(product, chosen, order_reference=common_order_reference(inspections)),
            "standard_candidates": [{"id": v.pk, "version": v.version, "customer_key": v.customer_key,
                "source": v.source, "source_kind": v.source_kind, "guidance": summary(product, v, order_reference=common_order_reference(inspections))} for v in active],
            "standards": [standard_data(s) for s in standards], "auto_standard_id": chosen.pk if chosen and chosen.status == "ACTIVE" and all(c["resolved_by_standard"] for c in conflicts) else None,
            "criteria": [criterion_data(c) for c in source_rows(product, orders)[:50]],
            "criteria_count": len(source_rows(product, orders)), "conflicts": conflicts,
            "raw_process_reference": process,
            "suggestion_id": OrderCardPreparation.objects.filter(order__in=orders).values_list("pk", flat=True).first(),
            "limitations": ["只读打开不创建生产/草稿/卡号。", "制卡不增加产量、库存或交付。", "订单可直接填写预计模数制卡；不虚构生产任务和有效孔数。"]}


def lock_product(product):
    # SQLite has no row locks. Acquire its write reservation BEFORE range reads.
    # No-op SQL bypasses save/signals/updated_at and changes no business value.
    ProductSpecification.objects.filter(pk=product.pk).update(product_name=F("product_name"))


def card_order(order, inspections):
    result = {"id": order.pk, "order_no": order.order_no, "item_no": order.item_no, "customer": order_customer_key(order)}
    if order.pk in inspections:
        source = inspections[order.pk]
        result.update(customer_code=source["customer_code"], customer_project_no=source["project_no"])
    return result


def common_order_reference(inspections):
    values = [s["reference"] for s in inspections.values()]
    return values[0] if values and all(v == values[0] for v in values) else None


def check_customer_inspections(orders, inspections):
    if len(orders) < 2 or not inspections or not any(v["groups"] for v in inspections.values()):
        return
    signatures = {v["digest"] for v in inspections.values()}
    if len(inspections) != len(orders) or len(signatures) != 1 or "" in signatures:
        raise ValidationError({"order_ids": "翔邦订单客户代号、项目或类别限值不同／未确认，请分别备卡；不影响原生产任务。"})


def build_plan(data, exclude=None):
    from . import services as s
    product, run, orders, _ = resolve(data)
    if not orders and data.get("order_ids"):
        orders = list(QualityOrder.objects.filter(pk__in=data["order_ids"]).select_related("source_batch", "last_source_batch").order_by("id"))
    if not orders:
        raise ValidationError({"order_id": "请从明确的订单项次进入。"})
    if run and any(g.product_id != product.pk for g in CardGroup.objects.filter(run=run)):
        raise ValidationError({"product_id": "本生产段已存在其他产品的卡，不可换身份后重新发卡。"})
    if run and not is_open_run(run):
        raise ValidationError({"run_id": "已完成/已取消任务只作历史；请先明确安排新的生产段。"})
    inspections = ci.get_order_snapshots(orders)
    standard_id = data.get("standard_id")
    candidates = applicable_standards(product, run, orders, inspections)
    if not standard_id:
        active = [v for v in candidates if v.status == "ACTIVE"]
        if len(active) == 1 and all(c["resolved_by_standard"] for c in conflict_items(product, orders, active[0])):
            standard_id = active[0].pk
    standard = next((v for v in candidates if str(v.pk) == str(standard_id)), None)
    if standard_id and (not standard or standard.status not in ("ACTIVE", "DRAFT")):
        raise ValidationError({"standard_id": "标准已被替代或客户/订单/模具/机台不适用。"})
    if run and set(data.get("order_ids", [])) - set(run_order_ids(run)):
        raise ValidationError({"order_ids": "不能把任务外的订单并入实物范围。"})
    customers = {order_customer_key(o) for o in orders}
    if len(customers) != 1 or (len(orders) > 1 and None in customers):
        raise ValidationError({"order_ids": "跨客户/身份不明的订单不能合并制卡。"})
    if any(o.product_specification_id != product.pk for o in orders):
        raise ValidationError({"product_id": "共单必须是明确的同一产品档案。"})
    check_customer_inspections(orders, inspections)
    require_compatible_orders(product, orders, standard)
    occupied = ranges_for(run, exclude)
    # Old groups have no structured range; never guess their historical coverage.
    if run and CardGroup.objects.filter(run=run).exclude(pk=exclude).filter(range_start__isnull=True).exists():
        raise ValidationError({"range_start": "本段存在未结构化的历史卡范围，请先核对原卡；不能猜配或重复发卡。"})
    available = free_ranges(run.planned_mold_count, occupied) if run else []
    start = s.integer(data.get("range_start") or (available[0][0] if available else 1), "range_start")
    planned = data.get("planned_molds")
    basis = "尚未确认本段计划"
    arranged = data.get("arranged_quantity")
    output = data.get("confirmed_effective_output")
    arrangement = {"arranged_quantity": arranged, "confirmed_effective_output": output,
                   "output_unit": data.get("output_unit"), "output_basis": data.get("output_basis", ""),
                   "effective_output_confirmed": data.get("effective_output_confirmed") is True}
    if run:
        planned = planned or (available[0][1] - start + 1 if available else None)
        basis = f"生产任务#{run.pk} · 第{run.segment_no}段 · 已确认计划{run.planned_mold_count}模"
    elif planned is not None:
        basis = "订单直接安排预计生产模数（人工明确填写，非欠交推算，不计实际产量）"
    elif arranged is not None or output is not None:
        if arranged is not None:
            arranged = s.integer(arranged, "arranged_quantity")
        if output is not None:
            output = s.integer(output, "confirmed_effective_output")
        if arranged and output and data.get("output_unit") == "件/模" and data.get("effective_output_confirmed") is True and str(data.get("output_basis", "")).strip():
            planned = (arranged + output - 1) // output
            basis = f"本次安排 {arranged:,} 件 ÷ {output:,} 件/模，向上取整＝{planned:,} 模；有效产出依据：{s.required_text(data, 'output_basis', 500)}"
        elif arranged:
            basis = "暂不能试算：请填写并核对每模有效产出、件/模单位及依据；不使用档案孔数代填。"
    planned = s.integer(planned, "planned_molds", nullable=True)
    prior = groups_for(run, orders, product).exclude(pk=exclude).filter(status="GENERATED")
    if run:
        prior = prior.filter(run__isnull=True)
    if prior.exists() and data.get("append_confirmed") is not True:
        raise PreparationConflict("订单已有原卡；请先查看/补打。确有另一批计划时明确确认另建，不能重复发卡。")
    end = start + planned - 1 if planned else None
    if end and run and (end > run.planned_mold_count or any(start <= r["range_end"] and end >= r["range_start"] for r in occupied)):
        raise PreparationConflict("本段模数超出确认计划或与已有卡组重叠；请刷新查看尚未覆盖范围。")
    body = {**data, "product_id": product.pk, "run_id": run.pk if run else None,
            "order_ids": [o.pk for o in orders], "standard_id": standard.pk if standard else None,
            "planned_molds": planned, "plan_basis": basis,
            "whole_mold": (data.get("whole_mold") if data.get("whole_mold") in ("REQUIRED", "NOT_REQUIRED") else standard.whole_mold if standard else "UNKNOWN"),
            "whole_mold_basis": data.get("whole_mold_basis") or (f"采用已确认标准v{standard.version}：{standard.source}" if standard else ""),
            "molds_per_card": data.get("molds_per_card") or (standard.snapshot.get("packaging", {}).get("molds_per_card", 100) if standard else 100)}
    plan = s.group_plan(body, separate_customer_reference=True)
    plan["snapshot"]["orders"] = [card_order(o, inspections) for o in orders]
    plan["snapshot"]["customer_inspections"] = ci.card_payloads(orders, inspections)
    plan["snapshot"]["template"] = s.current_template()
    plan["range_start"], plan["range_end"] = (start, end) if planned and run else (None, None)
    plan["context_key"] = f"run:{run.pk}" if run else f"order:{orders[0].pk}"
    plan["snapshot"].update({"preparation_v2": True, "range_start": plan["range_start"], "range_end": plan["range_end"],
                            "plan_source": "RUN" if run else "ORDER_EXPLICIT" if data.get("planned_molds") is not None else "ARRANGEMENT_SUGGESTION",
                            "append_confirmed": data.get("append_confirmed") is True,
                            "arrangement": arrangement, "preparation_notes": str(data.get("preparation_notes", ""))[:2000],
                            "decisions": deepcopy(data.get("decisions", {})),
                            "route": deepcopy(standard.snapshot.get("route", {})) if standard else {},
                            "process_parameters": deepcopy(standard.snapshot.get("process_parameters", {})) if standard else {},
                            "packaging": deepcopy(standard.snapshot.get("packaging", {})) if standard else {},
                            "source_references": [{"order_id": o.pk, "batch_id": str(o.source_batch_id) if o.source_batch_id else None, "sheet": o.source_sheet, "row": o.source_row} for o in orders]})
    overrides = data.get("process_overrides", {})
    if not isinstance(overrides, dict):
        raise ValidationError({"process_overrides": "本次参数调整必须逐字段保存。"})
    if overrides:
        for name, value in overrides.items():
            if not isinstance(value, dict) or not str(value.get("source", "")).strip():
                raise ValidationError({"process_overrides": f"本次{name}调整需说明来源，不改产品母版。"})
        merged = {**plan["snapshot"]["process_parameters"], **overrides}
        plan["snapshot"]["process_parameters"] = standard_metadata({"process_parameters": merged})["process_parameters"]
    plan["snapshot"]["process_overrides"] = deepcopy(overrides)
    from .guidance import summary, temperature_plan
    plan["snapshot"]["temperature_plan"] = temperature_plan(data.get("temperature_plan", standard.snapshot.get("packaging", {}).get("temperature_plan") if standard else None))
    plan["snapshot"]["raw_process_reference"] = {"primary_curing": product.primary_curing, "secondary_curing": product.secondary_curing}
    plan["snapshot"]["guidance"] = summary(product, snapshot={**(standard.snapshot if standard else {}), "process_parameters": plan["snapshot"]["process_parameters"]},
                                           order_reference=common_order_reference(inspections))
    if not run and planned:
        plan["snapshot"]["effective_cavities"] = output
    return plan


def apply_plan(group, plan, user):
    group.run, group.mold_id, group.standard, group.diagram = plan["run"], plan["mold_id"], plan["standard"], plan["diagram"]
    group.planned_molds, group.molds_per_card = plan["planned"], plan["per_card"]
    group.plan_basis, group.whole_mold, group.snapshot = plan["snapshot"]["plan_basis"], plan["snapshot"]["whole_mold"], plan["snapshot"]
    group.context_key, group.range_start, group.range_end = plan["context_key"], plan["range_start"], plan["range_end"]
    group.updated_by = user


@transaction.atomic
def save_draft(data, user):
    from . import services as s
    require_enabled()
    key = s.request_key(data)
    old = s.replay(CardGroup, key, data)
    if old:
        return old
    product = s.get_row(ProductSpecification, data.get("product_id"), "product_id")
    lock_product(product)
    group = s.get_row(CardGroup, data["group_id"], "group_id") if data.get("group_id") else None
    if group:
        previous = TraceAudit.objects.filter(object_type="CardGroup", object_id=str(group.pk), action="SAVE_PREPARATION", evidence__request_key=key).first()
        if previous:
            if previous.evidence.get("digest") != s.digest(data):
                raise ValidationError({"request_key": "同一请求号不能用于不同修改。"})
            return group
        if group.status != "DRAFT" or group.product_id != product.pk:
            raise ValidationError({"group_id": "只能继续同产品的未发卡草稿，旧卡不可改写。"})
        if data.get("expected_revision") != group.revision:
            raise PreparationConflict({"detail": "另一位经手人已保存，请重新读取并比较后再改。", "current_revision": group.revision, "current_snapshot": group.snapshot, "updated_at": group.updated_at.isoformat(), "updated_by": group.updated_by_id})
    plan = build_plan(data, group.pk if group else None)
    if not group:
        existing = groups_for(plan["run"], plan["orders"], product).filter(status="DRAFT").first()
        if existing:
            raise PreparationConflict({"detail": "同一生产段已有准备，请继续原草稿，不创建第二份。", "group_id": str(existing.pk), "current_revision": existing.revision})
        group = CardGroup(product=product, created_by=user, request_key=key, request_digest=s.digest(data))
    else:
        group.revision += 1
    apply_plan(group, plan, user)
    group.save()
    group.orders.set(plan["orders"])
    for order in plan["orders"]:
        suggestion, _ = OrderCardPreparation.objects.get_or_create(order=order, defaults={"created_by": user})
        suggestion.draft, suggestion.state = group, "PREPARING"
        suggestion.save(update_fields=["draft", "state", "updated_at"])
    s.audit(group, "SAVE_PREPARATION", user, {"request_key": key, "digest": s.digest(data), "revision": group.revision})
    return group


def validate_generation(group):
    if group.run and not is_open_run(group.run):
        raise ValidationError({"run_id": "请先明确新的生产段，不能沿用已结束的旧计划。"})
    if not group.run and group.snapshot.get("plan_source") != "ORDER_EXPLICIT":
        raise ValidationError({"planned_molds": "请直接填写本次预计生产模数，不用欠交或建议件数替代确认。"})
    if not group.standard or group.standard.status != "ACTIVE":
        raise ValidationError({"standard_id": "没有适用生效标准，只能保存待准备。"})
    orders = list(group.orders.select_related("source_batch", "last_source_batch"))
    prior = groups_for(group.run, orders, group.product).exclude(pk=group.pk).filter(status="GENERATED")
    if group.run_id:
        prior = prior.filter(run__isnull=True)
    if prior.exists() and not group.snapshot.get("append_confirmed"):
        raise PreparationConflict("订单已有原卡，请核对是否重复；另批计划需明确确认。")
    if group.run and {o.pk for o in orders} != set(run_order_ids(group.run)):
        raise ValidationError({"order_ids": "生产段关联订单已变化，请更新原准备后再发卡。"})
    if len({order_customer_key(o) for o in orders}) != 1 or any(o.product_specification_id != group.product_id for o in orders):
        raise ValidationError({"order_ids": "生产段客户或产品身份不一致，不能共用实物卡。"})
    inspections = ci.get_order_snapshots(orders)
    check_customer_inspections(orders, inspections)
    current_source = ci.card_payloads(orders, inspections)
    before_source = group.snapshot.get("customer_inspections", [])
    def source_key(refs):
        return {i["order_id"]: (i["digest"], i["groups"], i["reference"], i["status"]) for i in refs}
    if source_key(before_source) != source_key(current_source):
        raise PreparationConflict("订单客户要求已变化，请刷新并重新保存备卡资料后再发号。")
    require_compatible_orders(group.product, orders, group.standard)
    if group.standard not in applicable_standards(group.product, group.run, orders, inspections):
        raise ValidationError({"standard_id": "标准适用条件已变化，请重新核对。"})
    if group.snapshot.get("standard", {}).get("snapshot") != group.standard.snapshot:
        raise ValidationError({"standard_id": "标准内容已变化，请重开草稿重新核对，不用新内容冒充原快照。"})
    if group.run and (not group.range_start or group.range_end > group.run.planned_mold_count):
        raise ValidationError({"range_end": "未确认本段模数范围或计划已变化。"})
    unresolved = []
    for problem in conflict_items(group.product, orders, group.standard):
        if problem["resolved_by_standard"]:
            continue
        decision = group.snapshot.get("decisions", {}).get(problem["key"])
        allowed = {v["id"] for v in group.standard.snapshot.get("criteria", [])}
        # A source decision must refer to a frozen selected requirement. Text cannot waive it.
        if not isinstance(decision, int) or decision not in allowed or problem["missing_order_ids"]:
            unresolved.append(problem["name"])
    for c in group.standard.snapshot.get("criteria", []):
        if c.get("follow_package") is False:
            continue
        if c.get("requirement_state") in ("RAW_UNCONFIRMED", "NOT_PROVIDED") and c.get("critical", True):
            unresolved.append(c["name"])
        role = "baked_hardness" if c.get("stage") == "BAKED_HARDNESS" else "molding"
        if c.get("limit_mode") != "NONE" and c.get("requirement_state") != "NOT_APPLICABLE" and not group.snapshot.get("route", {}).get(role):
            unresolved.append(f"{c['name']}的适用工序未开启")
    for name, param in group.snapshot.get("process_parameters", {}).items():
        if param.get("critical") is True and param.get("state") not in ("CONFIRMED", "NOT_APPLICABLE"):
            unresolved.append(name)
    if unresolved:
        raise ValidationError({"decisions": "关键要求尚未逐项确认：" + "、".join(unresolved)})
    if not group.snapshot.get("route", {}).get("confirmed"):
        raise ValidationError({"route": "产品路线尚未确认，先保存待准备。"})
    route = group.snapshot["route"]
    if route.get("bake") and any(value["reference"]["secondary"]["state"] == "NOT_REQUIRED" for value in inspections.values()):
        raise ValidationError({"bake": "本单翔邦原表 0min / 0°C 表示无需二烤；所选工艺路线要求二烤，请核对标准。"})
    bake = group.standard.snapshot.get("bake", {})
    if route.get("bake") and bake.get("state") != "APPLICABLE":
        raise ValidationError({"bake": "路线需要二烤，但建议条件未确认。"})
    if route.get("baked_hardness") and not route.get("bake"):
        raise ValidationError({"route": "烤后硬度须对应实际二烤路线，不能沿用无关历史硬度。"})


@transaction.atomic
def activate_standard(standard, user):
    from . import services as s
    require_enabled()
    require_maintainer(user)
    lock_product(standard.product)
    standard.refresh_from_db()
    if standard.status == "ACTIVE":
        return standard
    if standard.status != "DRAFT":
        raise ValidationError({"status": "仅可生效新草稿；被替代/旧版不可直接复活。"})
    previous = StandardRevision.objects.filter(product=standard.product, mold=standard.mold,
        customer_key=standard.customer_key, applicable_order=standard.applicable_order, status="ACTIVE")
    # A revision for another machine/drawing does not retire the first scope.
    same_scope = [row.pk for row in previous if row.snapshot.get("applicability", {}) == standard.snapshot.get("applicability", {})]
    StandardRevision.objects.filter(pk__in=same_scope).update(status="SUPERSEDED")
    standard.status = "ACTIVE"
    standard.save(update_fields=["status"])
    s.audit(standard, "ACTIVATE_STANDARD", user, {"at": timezone.now().isoformat(), "source": standard.source})
    return standard


def standard_metadata(data):
    """Versioned product fields, not a second product/criterion master."""
    result = {}
    if data.get("import_source") is not None:
        import json
        origin = data["import_source"]
        if not isinstance(origin, dict) or len(json.dumps(origin, ensure_ascii=False)) > 128000:
            raise ValidationError({"import_source": "来源原文过大，请拆分为单产品标准表，或手工核对录入。"})
        result["import_source"] = deepcopy(origin)
    for key in ("applicability", "process_parameters", "route", "packaging"):
        value = data.get(key, {})
        if not isinstance(value, dict):
            raise ValidationError({key: "请按字段保存对象，不用说明文字代替结构化参数。"})
        result[key] = deepcopy(value)
    # Four independent temperatures, time WITH units, pressure type WITH units.
    params = result["process_parameters"]
    if len(params) > 40:
        raise ValidationError({"process_parameters": "最多40项结构化工艺参数。"})
    for key, value in params.items():
        if not isinstance(value, dict) or not isinstance(value.get("state", "RAW_UNCONFIRMED"), str):
            raise ValidationError({"process_parameters": f"{key}需保留数值、单位及确认状态。"})
        value.setdefault("state", "RAW_UNCONFIRMED")
        if value.get("state") not in ("NOT_PROVIDED", "RAW_UNCONFIRMED", "NOT_APPLICABLE", "CONFIRMED"):
            raise ValidationError({"process_parameters": f"{key}状态无效。"})
        if value.get("state") == "CONFIRMED" and key in ("upper_set_temperature", "lower_set_temperature", "upper_contact_temperature", "lower_contact_temperature", "primary_curing_time", "pressure") and not value.get("unit"):
            raise ValidationError({"process_parameters": f"{key}已确认时必须保留单位；单位未知请标待核。"})
        for field in ("lower", "upper", "value"):
            if value.get(field) not in (None, ""):
                try:
                    if not Decimal(str(value[field])).is_finite():
                        raise InvalidOperation
                except (InvalidOperation, ValueError):
                    raise ValidationError({"process_parameters": f"{key}.{field}必须是有限数值。"})
        if value.get("lower") not in (None, "") and value.get("upper") not in (None, "") and Decimal(str(value["lower"])) > Decimal(str(value["upper"])):
            raise ValidationError({"process_parameters": f"{key}下限不得超过上限。"})
        if any(not isinstance(value.get(field, ""), str) or len(value.get(field, "")) > 2000 for field in ("unit", "source", "raw_text")):
            raise ValidationError({"process_parameters": "单位、原文、出处每项最多2000字。"})
    route = result["route"]
    for key in ("confirmed", "molding", "patrol", "deburr", "bake", "baked_hardness", "inspection"):
        if key in route and not isinstance(route[key], bool):
            raise ValidationError({"route": f"{key}必须是布尔值。"})
    from .services import integer
    packaging = result["packaging"]
    if "temperature_plan" in packaging:
        from .guidance import temperature_plan
        packaging["temperature_plan"] = temperature_plan(packaging["temperature_plan"])
        if packaging["temperature_plan"]["mode"] == "REFERENCE":
            raise ValidationError({"temperature_plan": "原卡或实测记录仅能在本次制卡引用，不作为产品标准默认引用。"})
    if "molds_per_card" in packaging:
        packaging["molds_per_card"] = integer(packaging["molds_per_card"], "molds_per_card", maximum=100000)
    if packaging.get("patrol_every_molds") is not None:
        packaging["patrol_every_molds"] = integer(packaging["patrol_every_molds"], "patrol_every_molds", maximum=100000)
    return result


def package_todos(snapshot):
    route = snapshot.get("route", {})
    labels = [("molding", "成型实测", "测量员"), ("patrol", "巡检", "巡检员"), ("deburr", "毛边", "毛边作业员"), ("bake", "二次加硫", "二烤作业员"), ("baked_hardness", "烤后硬度", "测量员"), ("inspection", "品检", "品检员")]
    result = [{"stage": k, "label": label, "role": role, "state": "SYSTEM_UNREGISTERED"} for k, label, role in labels if route.get(k)]
    if snapshot.get("whole_mold") == "REQUIRED":
        result.insert(2, {"stage": "whole_mold", "label": "看整模", "role": "整模作业员", "state": "SYSTEM_UNREGISTERED"})
    return result


def package_progress(card):
    """References to actual facts, NOT a synthetic completion or work quantity."""
    todos = deepcopy(card.group.snapshot.get("todos", []))
    if not todos:
        return []
    kind_map = {"molding": {"UPPER_TEMP", "LOWER_TEMP", "MOLDING_HARDNESS", "DIMENSION"}, "patrol": {"APPEARANCE"},
                "whole_mold": {"WHOLE_MOLD"}, "deburr": {"DEBURR"}, "bake": {"BAKE"}, "baked_hardness": {"BAKED_HARDNESS"}, "inspection": set()}
    events = list(card.events.all())
    for todo in todos:
        refs = [e for e in events if e.kind in kind_map.get(todo["stage"], set())]
        todo.update({"state": "HAS_RECORD_REVIEW_SCOPE" if refs else "SYSTEM_UNREGISTERED",
                     "fact_ids": [str(e.pk) for e in refs], "employees": sorted({e.employee_name for e in refs if e.employee_name}),
                     "notice": "有原记录，请核对实际范围；不自动认定整包完成" if refs else "系统未登记，请核对纸卡；不等于现场未做"})
    return todos
