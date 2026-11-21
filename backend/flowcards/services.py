import hashlib
import json
import math
import uuid
from copy import deepcopy
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError, PermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError

from orders.identities import order_customer_key
from orders.models import ProductSpecification, ProductInspectionCriterion
from production.models import ProductionRun
from quality.models import QualityOrder, QualityEmployee, ProcessCardUnitBinding
from .models import (
    AppearanceIssue, StandardRevision, CardGroup, InternalCard, CardEvent,
    PrintJob, PrintItem, TemplatePublication, ShipmentSource, TraceAudit, PositionDiagram,
)
from .limits import validate_limits, evaluate

RENDERER = "flowcard-review-20261119-v4"
PREPARATION_RENDERER = "flowcard-guidance-20261121-v6"
TEMPLATE = {"renderer": RENDERER, "version": "草稿", "width_mm": 190, "height_mm": 130}


def current_renderer():
    return PREPARATION_RENDERER if settings.FLOW_CARDS_PREPARATION_ENABLED else RENDERER


def current_template():
    return {**TEMPLATE, "renderer": current_renderer()}

MEASURE_STAGES = {"UPPER_TEMP", "LOWER_TEMP", "MOLDING_HARDNESS", "BAKED_HARDNESS", "DIMENSION", "SECONDARY_CURE"}
KINDS = MEASURE_STAGES | {"APPEARANCE", "WHOLE_MOLD", "DEBURR", "BAKE", "ANOMALY", "RESOLUTION", "SOURCE_CONFIRM", "VOID", "WORK_GUIDANCE"}
HARDNESS_STAGES = {"MOLDING_HARDNESS", "BAKED_HARDNESS"}


def hardness_configuration(data):
    supplied = data.get("hardness", {})
    if not isinstance(supplied, dict):
        raise ValidationError({"hardness": "请分别确认两个阶段的测量对象和方法。"})
    result = {}
    for stage in sorted(HARDNESS_STAGES):
        value = supplied.get(stage, {})
        if not isinstance(value, dict) or value.get("object", "UNKNOWN") not in ("PRODUCT", "COUPON", "UNKNOWN"):
            raise ValidationError({"hardness": "测量对象只能是成品、胶片或待确认，不按规格尺寸推断。"})
        result[stage] = {"object": value.get("object", "UNKNOWN")}
        for key in ("scale", "instrument", "points", "specimen", "conditioning", "reading_time", "aggregation", "accepted_method"):
            text = value.get(key, "")
            if not isinstance(text, str) or len(text) > 500:
                raise ValidationError({"hardness": "测法资料每项最多500字；未知可留空。"})
            result[stage][key] = text.strip()
    return result


def write_enabled():
    if not settings.FLOW_CARDS_WRITE_ENABLED:
        raise PermissionDenied("工序流转卡处于只读试点状态，尚未开放写入。")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def required_text(data, key, limit=2000):
    value = data.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValidationError({key: f"请填写明确依据（最多{limit}字）。"})
    return value.strip()


def integer(value, name, minimum=1, maximum=10000000, nullable=False):
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not str(value).isdigit() or not minimum <= int(value) <= maximum:
        raise ValidationError({name: f"请填写{minimum}至{maximum}的整数。"})
    return int(value)


def get_row(model, value, name):
    try:
        return model.objects.get(pk=value)
    except (model.DoesNotExist, ValueError, TypeError, OverflowError, DjangoValidationError):
        raise ValidationError({name: "记录不存在或编号无效，请明确选择，不能按相似名称推断。"})


def card_ids(data):
    ids = data.get("card_ids", [])
    if not isinstance(ids, list) or not 1 <= len(ids) <= 100:
        raise ValidationError({"card_ids": "请选择1至100张卡。"})
    try:
        return [uuid.UUID(str(value)) for value in ids]
    except (ValueError, TypeError, AttributeError):
        raise ValidationError({"card_ids": "内部卡编号无效。"})


def request_key(data):
    return required_text(data, "request_key", 128)


def replay(model, key, payload):
    existing = model.objects.filter(request_key=key).first()
    if existing and existing.request_digest != digest(payload):
        raise ValidationError({"request_key": "同一请求号已用于不同内容，请先核对原结果。"})
    return existing


def audit(obj, action, user, evidence):
    TraceAudit.objects.create(object_type=obj.__class__.__name__, object_id=str(obj.pk), action=action, created_by=user, evidence=evidence)


def standard_data(standard):
    return {"id": standard.pk, "version": standard.version, "product_id": standard.product_id,
            "mold_id": standard.mold_id, "source": standard.source, "scope": standard.scope,
            "whole_mold": standard.whole_mold, "snapshot": standard.snapshot,
            "confirmed_at": standard.created_at.isoformat(), "status": standard.status,
            "customer_key": standard.customer_key, "source_kind": standard.source_kind,
            "applicable_order_id": standard.applicable_order_id, "created_by": standard.created_by_id}


def requirement_warnings(product, orders, standard=None):
    """Expose differing source requirements; never resolve them by newest timestamp."""
    rows = list(ProductInspectionCriterion.objects.filter(product_specification=product).filter(
        Q(order__isnull=True) | Q(order_id__in=[o.pk for o in orders])))
    by_name = {}
    for row in rows:
        meta = row.raw_data or {}
        by_name.setdefault(row.inspection_item, set()).add((row.lower_limit, row.upper_limit, row.unit,
            meta.get("limit_mode"), meta.get("upper_inclusive", True), meta.get("lower_inclusive", True)))
    warnings = [f"项目“{name}”存在不同范围/单位，须核对来源后确认适用要求。" for name, values in by_name.items() if len(values) > 1]
    chosen = {e["id"] for e in standard.snapshot["criteria"]} if standard else set()
    if any(row.order_id and row.pk not in chosen for row in rows):
        warnings.append("有订单专用检验要求未包含在所选确认版本中；不能直接用产品默认版本替代。")
    return warnings


@transaction.atomic
def confirm_standard(data, user):
    write_enabled()
    from . import preparation as prep
    if prep.enabled():
        prep.require_maintainer(user)
    key = request_key(data)
    existing = replay(StandardRevision, key, data)
    if existing:
        return existing
    product = get_row(ProductSpecification, data.get("product_id"), "product_id")
    if prep.enabled() and isinstance(data.get("import_source"), dict):
        identity = data["import_source"].get("identity", {})
        if not isinstance(identity, dict):
            raise ValidationError({"import_source": "导入身份格式无效。"})
        def normalized(value):
            return "".join(str(value or "").strip().casefold().replace("×", "x").replace("*", "x").replace("＊", "x").split())
        for field in ("customer_product_no", "specification", "material"):
            actual, imported = getattr(product, field), identity.get(field)
            if actual and imported and normalized(actual) != normalized(imported):
                raise ValidationError({"import_source": f"导入{field}与当前产品不符，请选择正确产品，不猜配。"})
    # Lock the stable parent, including the first revision on PostgreSQL.
    ProductSpecification.objects.select_for_update().get(pk=product.pk)
    source = required_text(data, "source")
    hardness = hardness_configuration(data)
    dimensions = integer(data.get("dimension_count"), "dimension_count", minimum=0, maximum=30, nullable=True)
    bake = deepcopy(data.get("bake", {"state": "UNKNOWN"}))
    if not isinstance(bake, dict) or bake.get("state") not in ("UNKNOWN", "APPLICABLE", "NOT_APPLICABLE"):
        raise ValidationError({"bake": "二烤要求须区分适用、不适用和待确认。"})
    if bake["state"] == "APPLICABLE":
        for prefix in ("temperature", "minutes"):
            try:
                # A specified temperature/time is not a tolerance interval.
                # Keep the fixed value explicit; equal legacy bounds only support
                # existing frozen-card readers. Never rewrite earlier snapshots.
                if bake.get(f"{prefix}_value") not in (None, ""):
                    fixed = Decimal(str(bake[f"{prefix}_value"]))
                    if not fixed.is_finite() or fixed < 0:
                        raise InvalidOperation
                    if any(bake.get(f"{prefix}_{side}") not in (None, "") and Decimal(str(bake[f"{prefix}_{side}"])) != fixed for side in ("lower", "upper")):
                        raise InvalidOperation
                    bake[f"{prefix}_lower"] = bake[f"{prefix}_upper"] = str(fixed)
                lower, upper = Decimal(str(bake[f"{prefix}_lower"])), Decimal(str(bake[f"{prefix}_upper"]))
                if not lower.is_finite() or not upper.is_finite() or lower < 0 or lower > upper:
                    raise InvalidOperation
            except (KeyError, InvalidOperation, ValueError):
                raise ValidationError({"bake": "需要二烤时请填写温度℃、时间min的指定值；原表明确给范围时才填范围。数值须有限且非负，不同时填写冲突的指定值和范围。"})
    whole = data.get("whole_mold", "UNKNOWN")
    if whole not in ("REQUIRED", "NOT_REQUIRED", "UNKNOWN"):
        raise ValidationError({"whole_mold": "请选择需整模、不需或待确认。"})
    mold = get_row(__import__("molds.models", fromlist=["MoldAsset"]).MoldAsset, data["mold_id"], "mold_id") if data.get("mold_id") else None
    entries = data.get("criteria", [])
    if not isinstance(entries, list) or len(entries) > 30:
        raise ValidationError({"criteria": "最多确认30个项目。"})
    issues = data.get("appearance_ids", [])
    if not isinstance(issues, list) or len(set(map(str, issues))) != 4:
        raise ValidationError({"appearance_ids": "请选择四项外观问题。"})
    issues = [integer(value, "appearance_ids", maximum=2147483647) for value in issues]
    selected_by_id = {row.pk: row for row in AppearanceIssue.objects.filter(pk__in=issues, is_active=True)}
    if len(selected_by_id) != 4:
        raise ValidationError({"appearance_ids": "外观选项不存在或已停用。"})
    selected = [selected_by_id[pk] for pk in issues]
    criteria, snapshots = [], []
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("stage") not in MEASURE_STAGES:
            raise ValidationError({"criteria": "检验阶段无效。"})
        method = entry.get("method") or ""
        if not isinstance(method, str) or len(method) > 300:
            raise ValidationError({"method": "测法最多300字；未确认可留空，实测不硬判。"})
        method = method.strip()
        unit = entry.get("unit") or ""
        if not isinstance(unit, str) or len(unit) > 100:
            raise ValidationError({"unit": "单位须为文字，最多100字；未确认可留空。"})
        if entry.get("criterion_id"):
            row = get_row(ProductInspectionCriterion, entry["criterion_id"], "criterion_id")
            if row.product_specification_id != product.pk:
                raise ValidationError({"criterion_id": "检验要求不属于此产品。"})
            if prep.enabled():
                customer = str(data.get("customer_key", "")).strip().casefold()
                if row.customer and row.customer.casefold() != customer:
                    raise ValidationError({"criterion_id": "原行属于其他客户，不能作为当前标准的依据。"})
                if row.order_id and str(row.order_id) != str(data.get("applicable_order_id")):
                    raise ValidationError({"criterion_id": "订单特规只能在其明确适用的订单中引用；不可变成通用标准。"})
                changed = any(k in entry and str(entry[k]) != str(getattr(row, f"{k}_limit" if k in ("lower", "upper") else k)) for k in ("lower", "upper", "unit"))
                raw = row.raw_data or {}
                changed = changed or any(k in entry and entry[k] != raw.get(k, True if k.endswith("inclusive") else "PENDING")
                                         for k in ("limit_mode", "upper_inclusive", "lower_inclusive"))
                if changed and entry.get("override") is not True:
                    raise ValidationError({"criterion_id": "引用原行不可静默改值；请选择明确的新版本/本单例外。"})
                if entry.get("override") is True:
                    order = get_row(__import__("quality.models", fromlist=["QualityOrder"]).QualityOrder, data["applicable_order_id"], "applicable_order_id") if data.get("applicable_order_id") else None
                    if order and order.product_specification_id != product.pk:
                        raise ValidationError({"applicable_order_id": "特规订单必须明确关联同一产品。"})
                    row = ProductInspectionCriterion.objects.create(product_specification=product, order=order,
                        customer=row.customer, inspection_item=required_text(entry, "name", 200) if "name" in entry else row.inspection_item,
                        lower_limit=str(entry.get("lower", row.lower_limit)), upper_limit=str(entry.get("upper", row.upper_limit)), unit=entry.get("unit", row.unit),
                        source_batch=row.source_batch, source_sheet=row.source_sheet, source_row=row.source_row,
                        raw_data={**(row.raw_data or {}), "overrides_criterion_id": row.pk, "source": source, "stage": entry["stage"],
                                  "method": method, **{k: entry[k] for k in ("limit_mode", "upper_inclusive", "lower_inclusive") if k in entry}})
        else:
            # New manual requirements join the EXISTING product criteria table.
            row = ProductInspectionCriterion.objects.create(
                product_specification=product, order_id=data.get("applicable_order_id") if prep.enabled() else None,
                customer=str(data.get("customer_key", "")).strip() if prep.enabled() else "",
                category="工序流转卡确认", inspection_item=required_text(entry, "name", 200),
                lower_limit=str(entry.get("lower", "")), upper_limit=str(entry.get("upper", "")),
                unit=unit.strip(), raw_data={"source": source, "method": method, "stage": entry["stage"],
                    **{k: entry[k] for k in ("limit_mode", "upper_inclusive", "lower_inclusive") if k in entry}},
            )
        numeric = entry.get("numeric", False)
        if numeric not in (True, False):
            raise ValidationError({"criteria": "numeric必须为布尔值。"})
        rules = {k: entry.get(k, (row.raw_data or {}).get(k)) for k in ("limit_mode", "upper_inclusive", "lower_inclusive")
                 if k in entry or k in (row.raw_data or {})}
        mode = validate_limits({"lower": row.lower_limit, "upper": row.upper_limit, "numeric": numeric, **rules})
        snapshots.append({"id": row.pk, "order_id": row.order_id, "name": row.inspection_item,
                          "lower": row.lower_limit, "upper": row.upper_limit, "unit": row.unit,
                          "stage": entry["stage"], "method": method, "numeric": mode in ("UPPER", "LOWER", "RANGE"),
                          "limit_mode": mode, "upper_inclusive": rules.get("upper_inclusive", True), "lower_inclusive": rules.get("lower_inclusive", True), "source": source,
                          "source_sheet": row.source_sheet, "source_row": row.source_row,
                          "source_batch_id": str(row.source_batch_id) if row.source_batch_id else None})
        if prep.enabled() and (row.raw_data or {}).get("overrides_criterion_id"):
            snapshots[-1]["overrides_criterion_id"] = row.raw_data["overrides_criterion_id"]
        criteria.append(row)
    if dimensions is not None and dimensions < sum(e["stage"] == "DIMENSION" and e["limit_mode"] != "NONE" and entry.get("follow_package", True) for e, entry in zip(snapshots, entries)):
        raise ValidationError({"dimension_count": "需要的尺寸项目数不能少于本次已确认的尺寸项目。"})
    version = (StandardRevision.objects.filter(product=product).aggregate(v=Max("version"))["v"] or 0) + 1
    metadata = prep.standard_metadata(data) if prep.enabled() else {}
    extra = {}
    if prep.enabled():
        kind = data.get("source_kind", "FACTORY_EXPERIENCE")
        if kind not in ("CUSTOMER", "FACTORY_PROCESS", "FACTORY_EXPERIENCE"):
            raise ValidationError({"source_kind": "请区分客户要求、本厂工艺和本厂经验待核。"})
        applicable_order_id = data.get("applicable_order_id")
        if applicable_order_id:
            order = get_row(QualityOrder, applicable_order_id, "applicable_order_id")
            if order.product_specification_id != product.pk:
                raise ValidationError({"applicable_order_id": "特规不能属于其他产品。"})
        extra = {"status": "DRAFT", "customer_key": str(data.get("customer_key", "")).strip(),
                 "source_kind": kind, "applicable_order_id": applicable_order_id}
        for snapshot, entry, row in zip(snapshots, entries, criteria):
            state = entry.get("requirement_state", "CONFIRMED" if snapshot["limit_mode"] in ("UPPER", "LOWER", "RANGE") else "NOT_APPLICABLE" if snapshot["limit_mode"] == "NONE" else "MEASURE_NO_LIMIT")
            if state not in ("CONFIRMED", "NOT_PROVIDED", "RAW_UNCONFIRMED", "NOT_APPLICABLE", "MEASURE_NO_LIMIT"):
                raise ValidationError({"requirement_state": "要求状态无效。"})
            snapshot.update({"requirement_state": state, "critical": entry.get("critical", True)})
            if "follow_package" in entry:
                if not isinstance(entry["follow_package"], bool):
                    raise ValidationError({"follow_package": "是否随包测必须明确为是或否。"})
                snapshot["follow_package"] = entry["follow_package"]
            if "provenance" in entry:
                provenance = entry["provenance"]
                if not isinstance(provenance, dict) or len(json.dumps(provenance, ensure_ascii=False)) > 6000:
                    raise ValidationError({"provenance": "原文及出处格式无效或过长。"})
                snapshot["provenance"] = provenance
                # A new/explicit override joins the original product criteria table;
                # merely referencing a prior row must never rewrite its provenance.
                if not entry.get("criterion_id") or entry.get("override") is True:
                    row.raw_data = {**(row.raw_data or {}), "standard_import": provenance}
                    row.save(update_fields=["raw_data"])
    result = StandardRevision.objects.create(product=product, mold=mold, version=version, source=source, request_key=key, request_digest=digest(data),
        scope=str(data.get("scope", "")), whole_mold=whole, created_by=user,
        snapshot={"criteria": snapshots, "bake": bake, "hardness": hardness, "dimension_count": dimensions,
                  "appearance": [{"id": e.pk, "name": e.name} for e in selected], **metadata}, **extra)
    result.criteria.set(criteria)
    result.appearance.set(selected)
    audit(result, "CONFIRM", user, {"source": source})
    return result


def card_mold_plan(planned, per_card, custom=None):
    """Explicit per-package plan; never infer actual production from it."""
    if custom is not None:
        if not isinstance(custom, list) or not 1 <= len(custom) <= 500:
            raise ValidationError({"card_molds": "请填写1至500张卡的预计模数。"})
        values = [integer(v, "card_molds", maximum=100000) for v in custom]
        if planned is None or sum(values) != planned:
            raise ValidationError({"card_molds": "逐卡模数合计必须等于本次预计生产模数。"})
        return values
    if not planned:
        return []
    count = math.ceil(planned / per_card)
    if count > 500:
        raise ValidationError({"planned_molds": "一次最多500张，请明确分批范围。"})
    return [min(per_card, planned - i * per_card) for i in range(count)]


def group_plan(data):
    product = get_row(ProductSpecification, data.get("product_id"), "product_id")
    run = get_row(ProductionRun, data["run_id"], "run_id") if data.get("run_id") else None
    ids = data.get("order_ids", [])
    if not isinstance(ids, list) or len(ids) > 200:
        raise ValidationError({"order_ids": "请选择订单项次，最多200项。"})
    ids = [integer(value, "order_ids", maximum=2147483647) for value in ids]
    if run:
        if run.product_specification_id != product.pk:
            raise ValidationError({"run_id": "任务没有明确关联此产品，先核对产品身份。"})
        allowed = set(run.order_links.values_list("order_id", flat=True))
        if run.order_id:
            allowed.add(run.order_id)
        if not ids:
            ids = list(allowed)
        if not set(ids) <= allowed:
            raise ValidationError({"order_ids": "只能选任务明确关联的订单项次。"})
    orders = list(QualityOrder.objects.filter(pk__in=ids).select_related("source_batch", "last_source_batch").order_by("id"))
    if len(orders) != len(set(ids)) or not orders:
        raise ValidationError({"order_ids": "请选择存在的订单项次。"})
    if any(o.product_specification_id != product.pk for o in orders):
        raise ValidationError({"order_ids": "产品身份不一致；相同规格材质不等于同一产品。"})
    customers = {order_customer_key(o) for o in orders}
    if len(customers) > 1 or len(orders) > 1 and None in customers:
        raise ValidationError({"order_ids": "跨客户请分别制卡，不能自动共用检验要求。"})
    standard = get_row(StandardRevision, data["standard_id"], "standard_id") if data.get("standard_id") else None
    mold_id = run.mold_id if run else data.get("mold_id")
    if standard and (standard.product_id != product.pk or (standard.mold_id and standard.mold_id != mold_id)):
        raise ValidationError({"standard_id": "要求的产品或实物模具不适用。"})
    if standard and any(e.get("order_id") and e["order_id"] not in {o.pk for o in orders} for e in standard.snapshot["criteria"]):
        raise ValidationError({"standard_id": "含其他订单专用要求，请核对适用范围后另建确认版本。"})
    planned = integer(data.get("planned_molds"), "planned_molds", nullable=True)
    per_card = integer(data.get("molds_per_card", 100), "molds_per_card", maximum=100000)
    card_molds = card_mold_plan(planned, per_card, data.get("card_molds"))
    count = len(card_molds)
    whole = data.get("whole_mold", "UNKNOWN")
    if whole not in ("REQUIRED", "NOT_REQUIRED", "UNKNOWN"):
        raise ValidationError({"whole_mold": "整模选择无效。"})
    diagram = get_row(PositionDiagram, data["diagram_id"], "diagram_id") if data.get("diagram_id") else None
    if diagram and (diagram.product_id != product.pk or not mold_id or diagram.mold_id != mold_id):
        raise ValidationError({"diagram_id": "定点图必须匹配本次实际模具，不能套用同型号另一副。"})
    total = integer(data.get("confirmed_total_cavities"), "confirmed_total_cavities", nullable=True)
    if total is not None:
        required_text(data, "cavities_basis")
    appearance = standard.snapshot["appearance"] if standard else list(AppearanceIssue.objects.filter(is_active=True).order_by("id").values("id", "name")[:4])
    if data.get("appearance_ids") is not None:
        issue_ids = data["appearance_ids"]
        if not isinstance(issue_ids, list) or len(issue_ids) != 4 or len(set(map(str, issue_ids))) != 4:
            raise ValidationError({"appearance_ids": "请选择四项不同的外观问题。"})
        issue_ids = [integer(value, "appearance_ids", maximum=2147483647) for value in issue_ids]
        selected = {row["id"]: row for row in AppearanceIssue.objects.filter(pk__in=issue_ids, is_active=True).values("id", "name")}
        appearance = [selected[pk] for pk in issue_ids if pk in selected]
    if len(appearance) != 4:
        raise ValidationError({"appearance": "请先维护四项适用外观问题。"})
    snapshot = {"card_molds": card_molds, "appearance": appearance, "product": {"id": product.pk, "name": product.product_name, "specification": product.specification,
                            "material": product.material, "customer_product_no": product.customer_product_no},
        "orders": [{"id": o.pk, "order_no": o.order_no, "item_no": o.item_no, "customer": order_customer_key(o)} for o in orders],
        "run_id": run.pk if run else None, "mold_id": mold_id,
        "effective_cavities": run.cavities if run else None, "confirmed_total_cavities": total,
        "cavities_basis": data.get("cavities_basis", ""), "standard": standard_data(standard) if standard else None,
        "requirement_warnings": requirement_warnings(product, orders, standard), "requirements_review": str(data.get("requirements_review", "")),
        "whole_mold": whole, "whole_mold_basis": str(data.get("whole_mold_basis", "")),
        "diagram_id": diagram.pk if diagram else None, "template": {**TEMPLATE,
            "width_mm": integer(data.get("paper_width_mm", 190), "paper_width_mm", minimum=180, maximum=220),
            "height_mm": integer(data.get("paper_height_mm", 130), "paper_height_mm", minimum=125, maximum=165)},
        "plan_basis": required_text(data, "plan_basis"), "material_evidence": str(data.get("material_evidence", ""))}
    return {"product": product, "run": run, "orders": orders, "standard": standard, "diagram": diagram,
            "mold_id": mold_id, "planned": planned, "per_card": per_card, "count": count, "snapshot": snapshot}


@transaction.atomic
def create_group(data, user):
    from . import preparation as prep
    if prep.enabled():
        return prep.save_draft(data, user)
    write_enabled()
    key = request_key(data)
    existing = replay(CardGroup, key, data)
    if existing:
        return existing
    plan = group_plan(data)
    # The source parent serializes preparation requests on databases supporting
    # row locks. SQLite conflicts are returned with the original request key.
    ProductSpecification.objects.select_for_update().get(pk=plan["product"].pk)
    prior = CardGroup.objects.filter(run=plan["run"]) if plan["run"] else CardGroup.objects.filter(orders__in=plan["orders"]).distinct()
    if prior.exists() and data.get("append_confirmed") is not True:
        raise ValidationError({"append_confirmed": "来源已有卡组，请先查看原组；如确需追加，明确本次范围并勾选追加确认。"})
    plan["snapshot"]["append_confirmed"] = data.get("append_confirmed") is True
    group = CardGroup.objects.create(request_key=key, request_digest=digest(data), product=plan["product"],
        run=plan["run"], mold_id=plan["mold_id"], standard=plan["standard"], diagram=plan["diagram"],
        planned_molds=plan["planned"], molds_per_card=plan["per_card"], plan_basis=plan["snapshot"]["plan_basis"],
        whole_mold=plan["snapshot"]["whole_mold"], snapshot=plan["snapshot"], created_by=user)
    group.orders.set(plan["orders"])
    audit(group, "DRAFT", user, {"request_key": key})
    return group


@transaction.atomic
def revise_draft(group, data, user):
    from . import preparation as prep
    if prep.enabled():
        return prep.save_draft({**data, "group_id": str(group.pk)}, user)
    write_enabled()
    group = CardGroup.objects.select_for_update().get(pk=group.pk)
    key = request_key(data)
    previous = TraceAudit.objects.filter(object_type="CardGroup", object_id=str(group.pk), action="REVISE_DRAFT", evidence__request_key=key).first()
    if previous:
        if previous.evidence["request_digest"] != digest(data):
            raise ValidationError({"request_key": "原准备组修改请求号已用于不同内容。"})
        return group
    if group.status != "DRAFT" or group.cards.exists():
        raise ValidationError({"group": "已生成卡片的快照不能修改；补打保留原件，追加另建组。"})
    plan = group_plan(data)
    if plan["product"].pk != group.product_id:
        raise ValidationError({"product_id": "准备组不能换成另一产品，请另建明确来源。"})
    before = group.snapshot
    group.run, group.mold_id, group.standard, group.diagram = plan["run"], plan["mold_id"], plan["standard"], plan["diagram"]
    group.planned_molds, group.molds_per_card = plan["planned"], plan["per_card"]
    group.plan_basis, group.whole_mold, group.snapshot = plan["snapshot"]["plan_basis"], plan["snapshot"]["whole_mold"], plan["snapshot"]
    group.save(update_fields=["run", "mold", "standard", "diagram", "planned_molds", "molds_per_card", "plan_basis", "whole_mold", "snapshot"])
    group.orders.set(plan["orders"])
    audit(group, "REVISE_DRAFT", user, {"request_key": key, "request_digest": digest(data), "before": before, "after": group.snapshot})
    return group


@transaction.atomic
def generate_group(group, user, expected_revision=None):
    write_enabled()
    from . import preparation as prep
    if prep.enabled():
        prep.lock_product(group.product)
    group = CardGroup.objects.select_for_update().get(pk=group.pk)
    if group.status == "GENERATED":
        return group
    if prep.enabled() and expected_revision is not None and expected_revision != group.revision:
        raise prep.PreparationConflict("发卡前草稿版本已变化，请重新读取核对。")
    if prep.enabled():
        prep.validate_generation(group)
        occupied = prep.ranges_for(group.run, group.pk)
        if any(group.range_start <= r["range_end"] and group.range_end >= r["range_start"] for r in occupied):
            raise prep.PreparationConflict("本段模数与其他准备/卡组重叠，请核对原卡。")
    if not group.planned_molds:
        raise ValidationError({"planned_molds": "未确认模数只能保存准备组，不生成有效包。"})
    if group.whole_mold == "UNKNOWN" or not group.snapshot.get("whole_mold_basis"):
        raise ValidationError({"whole_mold": "正式制卡前请明确需/不需整模及选择依据。"})
    if not prep.enabled() and group.snapshot.get("requirement_warnings") and not group.snapshot.get("requirements_review", "").strip():
        raise ValidationError({"requirements_review": "检验要求存在差异/订单专用缺项，请核对并填写本组采用要求的依据；准备组保留，不自动选择。"})
    renderer = current_renderer()
    publication = TemplatePublication.objects.filter(renderer=renderer).first()
    snapshot = dict(group.snapshot)
    snapshot["template"] = {**snapshot["template"], "renderer": renderer, "version": publication.version if publication else "草稿"}
    snapshot["confirmed_by"] = user.pk
    if prep.enabled():
        snapshot["todos"] = prep.package_todos(snapshot)
    now = timezone.now()
    day = timezone.localtime(now).date()
    distribution = card_mold_plan(group.planned_molds, group.molds_per_card, group.snapshot.get("card_molds"))
    count = len(distribution)
    cards = []
    for index in range(count):
        identity = uuid.uuid4()
        cards.append(InternalCard(id=identity, group=group, card_no=f"FC-{day:%Y%m%d}-{identity.hex[:16].upper()}",
            sequence=index + 1, denominator=count, original_date=day,
            planned_molds=distribution[index]))
    if snapshot.get("temperature_plan", {}).get("mode") == "FIRST":
        snapshot["temperature_first_card_no"] = cards[0].card_no
    snapshot["digest"] = digest(snapshot)
    InternalCard.objects.bulk_create(cards)
    group.status, group.generated_at, group.generated_by = "GENERATED", now, user
    group.snapshot = snapshot
    group.save(update_fields=["status", "generated_at", "generated_by", "snapshot"])
    if prep.enabled():
        from .models import OrderCardPreparation
        remaining = prep.free_ranges(group.run.planned_mold_count, prep.ranges_for(group.run)) if group.run_id else []
        OrderCardPreparation.objects.filter(order__in=group.orders.all()).update(state="PENDING" if remaining else "GENERATED", updated_at=now)
    audit(group, "GENERATE", user, {"count": count, "molds": group.planned_molds})
    return group


@transaction.atomic
def create_print(data, user):
    write_enabled()
    key = request_key(data)
    existing = replay(PrintJob, key, data)
    if existing:
        return existing
    second = data.get("second_inspection", False)
    if not isinstance(second, bool):
        raise ValidationError({"second_inspection": "是否二检必须明确选择。"})
    ids = card_ids(data)
    cards = list(InternalCard.objects.select_for_update().filter(pk__in=ids).select_related("group").order_by("group_id", "sequence"))
    if len(cards) != len(set(map(str, ids))) or any(c.status != "ACTIVE" for c in cards):
        raise ValidationError({"card_ids": "卡不存在或已作废。"})
    templates = {(c.group.snapshot["template"]["renderer"], c.group.snapshot["template"].get("width_mm", 190), c.group.snapshot["template"].get("height_mm", 130)) for c in cards}
    if len(templates) > 1:
        raise ValidationError({"card_ids": "不同模板渲染版本或纸张尺寸请分别打印，旧卡不套新模板。"})
    if second and any(c.group.snapshot["template"]["renderer"] in (PREPARATION_RENDERER, "flowcard-preparation-20261119-v5") and not c.group.snapshot.get("route", {}).get("inspection") for c in cards):
        raise ValidationError({"second_inspection": "所选原卡有不适用品检工序的卡，不能添加二检签名；请核对适用范围。"})
    active_items = PrintItem.objects.filter(card__in=cards, active=True)
    if data.get("resume_active") is True and active_items.exists():
        job_ids = list(active_items.values_list("job_id", flat=True).distinct())
        if len(job_ids) == 1:
            job = PrintJob.objects.get(pk=job_ids[0])
            if set(job.items.values_list("card_id", flat=True)) == {c.pk for c in cards} and not job.items.filter(active=False).exists():
                if bool(job.template.get("second_inspection")) != second:
                    raise ValidationError({"second_inspection": "原打印任务的格式已经冻结；请先继续/核对原任务，不覆盖原PDF。"})
                return job
    if active_items.exists():
        raise ValidationError({"card_ids": "有未核对打印结果的任务，请继续原任务，不能重复补打。"})
    template = {**deepcopy(cards[0].group.snapshot["template"]), "second_inspection": second}
    job = PrintJob.objects.create(request_key=key, request_digest=digest(data), template=template, created_by=user)
    PrintItem.objects.bulk_create([PrintItem(job=job, card=c, proposed_reprint=c.reprint_count + (1 if c.printed_at else 0)) for c in cards])
    audit(job, "FILE_PREPARED_NOT_PRINTED", user, {"cards": list(map(str, ids))})
    return job


@transaction.atomic
def confirm_print(job, data, user):
    write_enabled()
    job = PrintJob.objects.select_for_update().get(pk=job.pk)
    outcomes = data.get("outcomes", {})
    if not isinstance(outcomes, dict) or not outcomes or len(outcomes) > 100:
        raise ValidationError({"outcomes": "请明确各卡成功、失败/取消或结果未知。"})
    items = {str(i.card_id): i for i in job.items.select_for_update().select_related("card")}
    if not set(outcomes) <= items.keys() or any(v not in ("SUCCESS", "CANCELLED", "UNKNOWN") for v in outcomes.values()):
        raise ValidationError({"outcomes": "卡或结果无效。"})
    for identity, outcome in outcomes.items():
        item = items[identity]
        if item.status == outcome:
            continue
        if not item.active:
            raise ValidationError({"outcomes": "已确认的打印结果不能再次改写；请保留原事实。"})
        item.status = outcome
        if outcome != "UNKNOWN":
            item.active = False
            item.confirmed_at = timezone.now()
            if outcome == "SUCCESS":
                item.card.reprint_count = item.proposed_reprint
                item.card.printed_at = item.confirmed_at
                item.card.save(update_fields=["reprint_count", "printed_at"])
        item.save(update_fields=["status", "active", "confirmed_at"])
    job.status = "PARTIAL" if job.items.filter(active=True).exists() else "CLOSED"
    job.save(update_fields=["status"])
    audit(job, "USER_PRINT_CONFIRM", user, outcomes)
    return job


@transaction.atomic
def record_event(data, user, *, entry_card=None):
    write_enabled()
    key = request_key(data)
    existing = replay(CardEvent, key, data)
    if existing:
        if entry_card and not existing.cards.filter(pk=entry_card).exists():
            raise ValidationError({"entry_card": "原请求不属于当前入口卡，不能跨包恢复提交。"})
        if existing.kind == "BAKED_HARDNESS" and existing.values.get("measurement_object") == "COUPON" and not entry_card:
            raise ValidationError({"entry_card": "胶片检测请从所属工序卡入口登记。"})
        return existing
    kind = data.get("kind")
    values = deepcopy(data.get("values", {}))
    if kind not in KINDS or not isinstance(values, dict):
        raise ValidationError({"kind": "记录阶段或内容无效。"})
    ids = card_ids(data)
    cards = list(InternalCard.objects.select_for_update().filter(pk__in=ids).select_related("group"))
    if len(cards) != len(set(map(str, ids))):
        raise ValidationError({"card_ids": "卡不存在。"})
    if any(c.status != "ACTIVE" for c in cards):
        raise ValidationError({"card_ids": "作废卡不能新增作业。"})
    if entry_card and entry_card not in {c.pk for c in cards}:
        raise ValidationError({"entry_card": "检测代表范围不包含当前入口卡，不能从本包把读数记给其他包。"})
    employee = get_row(QualityEmployee, data["employee_id"], "employee_id") if data.get("employee_id") else None
    from rest_framework.fields import DateTimeField
    business_at = DateTimeField(allow_null=True).run_validation(data.get("business_at"))
    evidence = (required_text(data, "evidence") if data.get("evidence") or kind not in MEASURE_STAGES
                else "实测登记；未另填依据说明")
    scope = required_text(data, "scope")
    if kind == "WORK_GUIDANCE":
        from .preparation import require_maintainer
        require_maintainer(user)
        if not business_at:
            raise ValidationError({"business_at": "补充作业指示需填写通知时间，保存时保留经手账号和依据。"})
        values = {"note": required_text(values, "note"), "quantity_effect": "NONE", "instruction_only": True}
    if kind == "BAKE":
        if not employee or not business_at:
            raise ValidationError({"employee_id": "实际二烤须登记作业人和实际业务时间。"})
        for field_name in ("temperature_c", "minutes"):
            try:
                value = Decimal(str(values[field_name]))
                if not value.is_finite() or value <= 0:
                    raise InvalidOperation
            except (KeyError, InvalidOperation, ValueError):
                raise ValidationError({field_name: "请记录实际温度℃及保温分钟，不自动采用建议条件。"})
            values[field_name] = str(value)
        coverage = values.get("card_trays")
        if not isinstance(coverage, list) or len(coverage) != len(cards):
            raise ValidationError({"card_trays": "请明确每张卡本次实际盘号及部分/整包范围；一张卡可分次烤。"})
        mapped = {}
        for item in coverage:
            if not isinstance(item, dict) or str(item.get("card_id")) not in set(map(str, ids)) or str(item.get("card_id")) in mapped:
                raise ValidationError({"card_trays": "覆盖卡号重复或不在本次选择中。"})
            trays = item.get("trays", [])
            if not isinstance(trays, list) or not 1 <= len(trays) <= 500:
                raise ValidationError({"trays": "请明确实际盘号，允许非连续盘号，不默认覆盖中间盘。"})
            trays = sorted(set(integer(v, "trays", maximum=100000) for v in trays))
            mapped[str(item["card_id"])] = {"card_id": str(item["card_id"]), "trays": trays, "scope": required_text(item, "scope", 500)}
        samples = values.get("coupon_samples", [])
        if not isinstance(samples, list) or len(samples) > 30:
            raise ValidationError({"coupon_samples": "胶片样品最多30项。"})
        for sample in samples:
            if not isinstance(sample, dict):
                raise ValidationError({"coupon_samples": "请明确胶片原始标识与覆盖范围。"})
            required_text(sample, "sample_ref", 200)
            required_text(sample, "material_ref", 300)
            sample_cards = sample.get("card_ids", [])
            if not isinstance(sample_cards, list) or not sample_cards or any(not isinstance(c, str) or c not in mapped for c in sample_cards):
                raise ValidationError({"coupon_samples": "胶片只能代表本次明确选择的卡，不能按相同材质自动扩展。"})
            trays = sample.get("trays", [])
            # One shared coupon may represent cards placed on different trays.
            # Its actual tray(s) must belong to the selected cards' union, not
            # the intersection (which would wrongly require identical trays).
            represented_trays = {t for c in sample_cards for t in mapped[c]["trays"]}
            if not isinstance(trays, list) or not trays or any(isinstance(t, bool) or not isinstance(t, int) or t not in represented_trays for t in trays):
                raise ValidationError({"coupon_samples": "胶片取样盘须属于其代表卡的本次实际盘号。"})
            if sample.get("accompanied") is not True:
                raise ValidationError({"coupon_samples": "只有实际随对应产品二烤的胶片才可关联本次烤后检测。"})
            sample["origin"] = "本厂自制"
        values = {**values, "card_trays": list(mapped.values()), "coupon_samples": samples, "quantity_effect": "NONE"}
    if kind == "WHOLE_MOLD":
        if len(cards) != 1:
            raise ValidationError({"card_ids": "整模按实际包确认；不能把同一次签名批量代签全部包。"})
        card = cards[0]
        if card.group.whole_mold != "REQUIRED":
            raise ValidationError({"kind": "本卡快照不需整模，不新增漏检/完成记录。"})
        status = values.get("status")
        if status not in ("PARTIAL", "COMPLETED", "EXCEPTION"):
            raise ValidationError({"values": "请明确部分、整包完成或异常待处理。"})
        fixed = integer(values.get("fixed_positions"), "fixed_positions", minimum=0, nullable=True)
        total = card.group.snapshot.get("confirmed_total_cavities")
        if fixed is not None and total is not None and fixed > total:
            raise ValidationError({"fixed_positions": "定点数超过已确认总孔数，请核对。"})
        coverage = values.get("coverage", "UNKNOWN")
        if coverage not in ("WHOLE", "PARTIAL", "UNKNOWN"):
            raise ValidationError({"coverage": "请明确整包、部分范围或范围未知。"})
        if status == "COMPLETED" and (not employee or not business_at or values.get("whole_package_confirmed") is not True or coverage != "WHOLE"):
            raise ValidationError({"values": "完成须明确整包实际作业、实际人员和业务时间；照片/抽看不能代替。"})
        if values.get("diagram_id") and values["diagram_id"] != card.group.diagram_id:
            raise ValidationError({"diagram_id": "本卡引用的图版本不一致，请另存变化及实际适用模段。"})
        values = {**values, "coverage": coverage, "fixed_positions": fixed, "unit": "孔/模", "quantity_effect": "NONE"}
    if kind in MEASURE_STAGES:
        # Derived fields are server facts, never trust stale/client judgments.
        for name in ("judgment", "standard_snapshot", "limit_side", "difference", "measurement_message", "limit_text", "review_effect", "entry_card_id", "historical_for"):
            values.pop(name, None)
        state = values.get("state")
        if state not in ("MEASURED", "NOT_MEASURED", "NOT_APPLICABLE", "UNKNOWN"):
            raise ValidationError({"values": "请区分实测、未测、不适用和未知。"})
        if state != "MEASURED" and values.get("actual") not in (None, ""):
            raise ValidationError({"actual": "已有实测数字不能同时标成未知/未测/不适用；请明确清除读数或选择实测。"})
        if state == "MEASURED":
            try:
                actual = Decimal(str(values["actual"]))
                if not actual.is_finite():
                    raise InvalidOperation
            except (KeyError, InvalidOperation, ValueError):
                raise ValidationError({"actual": "实测值必须明确；真实0允许，空白不算0。"})
            if not employee or not business_at:
                raise ValidationError({"employee_id": "实测须记录实际测量人和业务时间。"})
            values = {**values, "actual": str(actual), "judgment": "NOT_EVALUATED"}
            standards = [c.group.snapshot.get("standard") for c in cards]
            if len({digest(s) for s in standards}) != 1:
                raise ValidationError({"card_ids": "共同测量的要求不同，请分别登记。"})
            criteria = (standards[0] or {}).get("snapshot", {}).get("criteria", [])
            matching = [e for e in criteria if e["id"] == values.get("criterion_id") and e["stage"] == kind]
            method_matches = True
            if kind in HARDNESS_STAGES:
                config = (standards[0] or {}).get("snapshot", {}).get("hardness", {}).get(kind, {})
                actual_object = values.get("measurement_object", "UNKNOWN")
                if actual_object not in ("PRODUCT", "COUPON", "UNKNOWN"):
                    raise ValidationError({"measurement_object": "请保留实际成品/胶片对象；未知不能按规格猜。"})
                if config.get("object", "UNKNOWN") not in ("UNKNOWN", actual_object):
                    required_text(values, "object_change_reason", 500)
                values["measurement_object"] = actual_object
                values["method_configuration"] = config
                method_matches = actual_object != "UNKNOWN" and actual_object == config.get("object") and bool(config.get("scale")) and values.get("unit") == config.get("scale")
                if actual_object == "COUPON":
                    required_text(values, "sample_ref", 200)
                    required_text(values, "material_ref", 300)
                    values["sample_origin"] = "本厂自制"
                    if kind == "BAKED_HARDNESS":
                        if not entry_card:
                            raise ValidationError({"entry_card": "胶片烤后检测须从所属工序卡入口登记，不能只提交目标卡集合。"})
                        bake = get_row(CardEvent, values.get("bake_event_id"), "bake_event_id")
                        if bake.kind != "BAKE" or CardEvent.objects.filter(replaces=bake).exists():
                            raise ValidationError({"bake_event_id": "请引用当前有效的实际二烤记录，不用建议或被更正旧记录。"})
                        samples = [s for s in bake.values.get("coupon_samples", []) if s.get("sample_ref") == values["sample_ref"] and s.get("material_ref") == values["material_ref"] and s.get("accompanied") is True]
                        if not any(set(map(str, ids)) == set(s["card_ids"]) for s in samples):
                            raise ValidationError({"sample_ref": "这张胶片未登记随所选卡的产品二烤，不能作为这些卡的烤后实测。"})
                        values["entry_card_id"] = str(entry_card)
                        values["bake_snapshot"] = {"id": str(bake.pk), "business_at": bake.business_at.isoformat(), "card_trays": bake.values["card_trays"]}
            if matching:
                criterion = matching[0]
                if values.get("unit") != criterion["unit"] or values.get("method") != criterion["method"]:
                    method_matches = False
                values["standard_snapshot"] = criterion
                values.update(evaluate(criterion, actual, method_matches))
            else:
                values["measurement_message"] = "未关联当时要求，未判定"
    if kind == "APPEARANCE":
        if values.get("normal") is True and (values.get("defect_ids") or values.get("other")):
            raise ValidationError({"values": "正常与缺陷不能同时选择。"})
        allowed = set.intersection(*[{e["id"] for e in c.group.snapshot["appearance"]} for c in cards])
        if not isinstance(values.get("defect_ids", []), list) or not set(values.get("defect_ids", [])) <= allowed:
            raise ValidationError({"defect_ids": "只能选择本次卡片快照的外观问题。"})
    if kind == "SOURCE_CONFIRM":
        if not employee or not business_at or values.get("confirmed") is not True:
            raise ValidationError({"values": "实物来源核对须明确确认人、业务时间和确认动作，计划/打印不能代替。"})
        if values.get("actual_run_id"):
            actual_run = get_row(ProductionRun, values["actual_run_id"], "actual_run_id")
            if any(c.group.product_id != actual_run.product_specification_id or c.group.mold_id and c.group.mold_id != actual_run.mold_id for c in cards):
                raise ValidationError({"actual_run_id": "实际任务产品/实物模具与卡片不一致。"})
            values = {**values, "actual_run_id": actual_run.pk}
    if kind == "DEBURR" and values.get("oil") not in (None, "NORMAL", "SMALL", "NO"):
        raise ValidationError({"oil": "洒油只能单选，未勾不是否。"})
    if kind == "DEBURR":
        from .guidance import oil_instruction
        values.pop("oil_warning", None)
        values.pop("oil_judgment", None)
        barred_cards = [c for c in cards if oil_instruction(c.group.snapshot.get("process_parameters", {}).get("oil", {})) == "要求：禁止洒油"]
        if values.get("oil") in ("NORMAL", "SMALL") and barred_cards:
            values.update(oil_warning="已确认禁止洒油，但实际登记洒油；保留实际，需负责人处理异常", oil_judgment="EXCEPTION")
    replaces = get_row(CardEvent, data["replaces_id"], "replaces_id") if data.get("replaces_id") else None
    if replaces and (replaces.kind != kind or set(replaces.cards.values_list("pk", flat=True)) != {c.pk for c in cards}):
        raise ValidationError({"replaces_id": "更正/复测须保留原记录类型和覆盖范围。"})
    if replaces and kind in MEASURE_STAGES:
        if CardEvent.objects.filter(replaces=replaces).exists():
            raise ValidationError({"replaces_id": "原测量已有后续复核，请查看最新记录后继续，不能分叉关闭提醒。"})
        if not employee or not business_at or values.get("state") != "MEASURED" or values.get("criterion_id") != replaces.values.get("criterion_id"):
            raise ValidationError({"replaces_id": "复测须同一项目、有实际读数、处理人和业务时间；未测不能关闭超限。"})
        if kind == "BAKED_HARDNESS" and any(values.get(k) != replaces.values.get(k) for k in ("bake_event_id", "sample_ref", "material_ref")):
            raise ValidationError({"replaces_id": "复测须保留同次二烤和同一胶片来源。"})
        if scope != replaces.scope or values.get("measurement_object") != replaces.values.get("measurement_object"):
            raise ValidationError({"scope": "关联复核须保留同一实物范围及检测对象；其他范围请另登记，不能解除原提醒。"})
        mode = values.get("review_mode", "RETEST")
        if mode not in ("RETEST", "CORRECTION"):
            raise ValidationError({"review_mode": "请选择新做复测或更正原录入。"})
        if values.get("same_scope_confirmed") is not True:
            raise ValidationError({"same_scope_confirmed": "请确认实际检测为原记录的同一实物范围。"})
        values["review_mode"] = mode
        if mode == "CORRECTION":
            values["correction_reason"] = required_text(values, "correction_reason", 500)
            values["review_effect"] = "CORRECTION"
        else:
            values.pop("correction_reason", None)
            from .measurements import later_minute
            candidate = CardEvent(kind=kind, scope=scope, values=values, business_at=business_at)
            values["review_effect"] = "LATER_RETEST" if later_minute(candidate, replaces) else "HISTORICAL_ONLY"
            if values["review_effect"] == "HISTORICAL_ONLY":
                values["historical_for"] = str(replaces.pk)
                replaces = None
    if kind == "RESOLUTION":
        original = get_row(CardEvent, values.get("anomaly_id"), "anomaly_id")
        if original.kind != "ANOMALY" or not {c.pk for c in cards} <= set(original.cards.values_list("pk", flat=True)):
            raise ValidationError({"anomaly_id": "只能处理这些卡的原异常。"})
        if not employee or not business_at or values.get("resolved") is not True:
            raise ValidationError({"values": "关闭异常须明确实际处理人、业务时间及已解决确认；仅记备注不关闭。"})
        required_text(values, "result")
    event = CardEvent.objects.create(request_key=key, request_digest=digest(data), kind=kind, values=values,
        employee=employee, employee_name=employee.name if employee else "", business_at=business_at,
        evidence=evidence, scope=scope, replaces=replaces, created_by=user)
    event.cards.set(cards)
    if kind == "DEBURR" and values.get("oil_judgment") == "EXCEPTION":
        anomaly = CardEvent.objects.create(request_key="oil-anomaly:" + str(event.pk), request_digest=digest({"event": str(event.pk)}),
            kind="ANOMALY", values={"note": values["oil_warning"], "deburr_event_id": str(event.pk), "quantity_effect": "NONE"},
            employee=employee, employee_name=employee.name if employee else "", business_at=business_at, evidence=evidence, scope=scope, created_by=user)
        anomaly.cards.set(barred_cards)
        audit(anomaly, "OIL_EXCEPTION", user, {"deburr_event_id": str(event.pk)})
    if kind == "VOID":
        if any(c.events.exclude(kind="VOID").exists() or c.shipment_sources.exists() for c in cards):
            raise ValidationError({"card_ids": "已有使用事实的卡不能作废后换身份；保留原卡并更正。"})
        InternalCard.objects.filter(pk__in=ids).update(status="VOID")
    audit(event, "RECORD", user, {"kind": kind, "cards": list(map(str, ids))})
    return event


@transaction.atomic
def bind_sources(batch, data, user):
    """Called INSIDE the shipment transaction or explicit post-shipment trace action."""
    if not data:
        return []
    write_enabled()
    if batch.status != "CONFIRMED":
        raise ValidationError({"internal_sources": "出货尚未确认；来源不能提前展示为已出货。"})
    if not isinstance(data, list) or not 1 <= len(data) <= 100:
        raise ValidationError({"internal_sources": "来源集合最多100张。"})
    rows = []
    lines = list(batch.lines.select_related("order__source_batch", "order__last_source_batch", "process_card__order__source_batch", "process_card__order__last_source_batch"))
    line_orders = [l.order or (l.process_card.order if l.process_card_id else None) for l in lines]
    product_ids = {l.product_specification_id or (o.product_specification_id if o else None) for l, o in zip(lines, line_orders)}
    customers = {order_customer_key(o) for o in line_orders if o}
    batch_bindings = list(ProcessCardUnitBinding.objects.filter(shipment_batch=batch).select_related("process_card"))
    by_unit = {b.shipment_unit_no: b for b in batch_bindings}
    customer_refs = {str(l.process_card.tracking_id): l.process_card.card_no for l in lines if l.process_card_id}
    customer_refs.update({str(b.process_card.tracking_id): b.process_card.card_no for b in batch_bindings})
    try:
        source_cards = {str(c.pk): c for c in InternalCard.objects.filter(pk__in=[e.get("card_id") for e in data if isinstance(e, dict)]).select_related("group")}
    except (DjangoValidationError, ValueError, TypeError):
        raise ValidationError({"internal_sources": "来源卡编号无效。"})
    incoming_keys = [request_key(entry) for entry in data if isinstance(entry, dict)]
    existing = {(str(row.card_id), row.request_key): row for row in ShipmentSource.objects.filter(
        batch=batch, card_id__in=source_cards.keys(), request_key__in=incoming_keys)}
    pending = []
    for entry in data:
        if not isinstance(entry, dict):
            raise ValidationError({"internal_sources": "来源内容无效。"})
        card = source_cards.get(str(entry.get("card_id")))
        if card is None:
            raise ValidationError({"card_id": "来源卡不存在。"})
        if card.status != "ACTIVE":
            raise ValidationError({"internal_sources": "作废卡不可绑定出货。"})
        group = card.group
        if any(p is not None and p != group.product_id for p in product_ids):
            raise ValidationError({"internal_sources": "本次出货与来源卡产品身份明确不一致。"})
        source_customers = {o["customer"] for o in group.snapshot["orders"]}
        known_source_customers = {c for c in source_customers if c}
        if known_source_customers and any(c and c not in known_source_customers for c in customers):
            raise ValidationError({"internal_sources": "本次出货与来源卡客户不一致。"})
        scope = entry.get("scope", "SET_UNKNOWN")
        if scope not in ("SET_UNKNOWN", "WHOLE", "PARTIAL"):
            raise ValidationError({"scope": "请选择整包、部分或份额未知。"})
        evidence = required_text(entry, "evidence")
        unit = integer(entry.get("shipment_unit_no"), "shipment_unit_no", nullable=True)
        binding = None
        if unit:
            binding = by_unit.get(unit)
            if not binding:
                raise ValidationError({"shipment_unit_no": "无法确认此实物包，先用来源集合关联，不能按扫描顺序猜。"})
        identity_complete = product_ids == {group.product_id} and customers and all(customers) and all(source_customers) and customers == source_customers
        snapshot = {"card_no": card.card_no, "group_id": str(group.pk), "product_id": group.product_id,
                    "source_orders": group.snapshot["orders"], "shipment_no": batch.shipment_no,
                    "involved_customer_cards": [{"tracking_id": key, "card_no": value} for key, value in customer_refs.items()],
                    "shipment_status": batch.status, "customer_card_no": binding.process_card.card_no if binding else None,
                    "raw_code": str(entry.get("raw_code", ""))[:300], "quantity_effect": "NONE"}
        key = request_key(entry)
        lookup = (str(card.pk), key)
        row = existing.get(lookup)
        if row is not None and (row.scope != scope or row.shipment_unit_no != unit or row.evidence != evidence):
            raise ValidationError({"request_key": "来源请求号已用于其他关联内容。"})
        if row is None:
            row = ShipmentSource(batch=batch, card=card, request_key=key, scope=scope, shipment_unit_no=unit,
                customer_tracking_id=binding.process_card.tracking_id if binding else None, evidence=evidence, snapshot=snapshot,
                state="CONFIRMED" if identity_complete else "NEEDS_REVIEW", created_by=user)
            existing[lookup] = row
            pending.append(row)
        rows.append(row)
    ShipmentSource.objects.bulk_create(pending)
    TraceAudit.objects.bulk_create([TraceAudit(object_type="ShipmentSource", object_id=str(row.pk), action="SOURCE_BIND",
        created_by=user, evidence=row.snapshot) for row in pending])
    return rows


def mark_sources_for_review(batch, user, reason):
    """Never reconnect by a deleted/recreated line id or assume units survived."""
    active = ShipmentSource.objects.filter(batch=batch).exclude(state__in=["REVOKED", "SUPERSEDED"])
    rows = list(active.values("id", "snapshot", "state"))
    if rows:
        active.update(state="NEEDS_REVIEW")
        audit(batch, "SOURCE_REVIEW_AFTER_AMEND", user, {"reason": reason, "before": rows})


@transaction.atomic
def review_source(source, data, user):
    write_enabled()
    source = ShipmentSource.objects.select_for_update().select_related("batch").get(pk=source.pk)
    key = request_key(data)
    reason = required_text(data, "reason")
    if key == source.request_key:
        raise ValidationError({"request_key": "复核必须使用独立请求号，不能覆盖原关联请求。"})
    action = data.get("action")
    if action not in ("CONFIRM_SET", "REVOKE"):
        raise ValidationError({"action": "请选择明确重新核对来源集合或撤销错误关联。"})
    prior = TraceAudit.objects.filter(object_type="ShipmentSource", object_id=str(source.pk), action="SOURCE_REVIEW", evidence__request_key=key).first()
    if prior:
        if prior.evidence["request_digest"] != digest(data):
            raise ValidationError({"request_key": "同一复核请求号内容不一致。"})
        return prior.evidence
    if source.state in ("REVOKED", "SUPERSEDED"):
        raise ValidationError({"source": "原关系已撤销/被新版本替代，请查看原留痕与当前关系。"})
    before = {"state": source.state, "snapshot": source.snapshot}
    replacement = None
    if action == "CONFIRM_SET":
        # Explicitly re-confirm an UNKNOWN-share source set. Never reconnect an
        # old physical unit number to a newly rebuilt line by guessing.
        replacement = bind_sources(source.batch, [{"card_id": str(source.card_id), "scope": "SET_UNKNOWN",
            "request_key": key, "evidence": reason, "raw_code": source.snapshot.get("raw_code", "")}], user)[0]
        replacement.snapshot = {**replacement.snapshot, "replaces_source_id": source.pk}
        replacement.save(update_fields=["snapshot"])
    source.state = "SUPERSEDED" if replacement else "REVOKED"
    source.save(update_fields=["state"])
    evidence = {"request_key": key, "request_digest": digest(data), "reason": reason, "before": before,
                "action": action, "replacement_id": replacement.pk if replacement else None, "quantity_effect": "NONE"}
    audit(source, "SOURCE_REVIEW", user, evidence)
    return evidence
