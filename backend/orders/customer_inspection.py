"""翔邦下单方要求。保留原始类别，不把客户限值当成车间岗位、实测或放行规则。"""
from collections import defaultdict
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
import unicodedata

from django.db import transaction
from django.db.models import F
from rest_framework.exceptions import ValidationError

from .identities import order_customer_key
from .models import BusinessImportBatch, CustomerInspectionReview, OrderInspectionSource, ProductInspectionCriterion, ProductSpecification

FACTORY = BusinessImportBatch.SourceType.FACTORY_WORK_CONTACT
SOURCE = "翔邦"


def clean(value):
    return "" if value is None else unicodedata.normalize("NFKC", str(value)).strip()


def code(value):
    return clean(value).upper()


def number(value):
    text = clean(value)
    if not text:
        return ""
    try:
        value = Decimal(text)
        if not value.is_finite() or value.adjusted() > 20 or value.adjusted() < -20:
            return None
        if not value:
            return "0"
        formatted = format(value, "f")
        return formatted.rstrip("0").rstrip(".") if "." in formatted else formatted
    except InvalidOperation:
        return None


_SPAN = r"(?P<lo>\d{1,6}(?:\.\d{1,4})?)(?:\s*[-~～—–至]\s*(?P<hi>\d{1,6}(?:\.\d{1,4})?))?"


def curing(raw, *, primary):
    text = clean(raw)
    time = re.search(rf"时间\s*{_SPAN}\s*(?P<unit>min|秒|分钟|s)(?![A-Za-z])", text, re.I)
    temp = re.search(rf"温度\s*{_SPAN}\s*(?:°\s*C|℃)", text, re.I)
    result = {"raw": str(raw or ""), "state": "NOT_PROVIDED" if not text else "PENDING"}
    if not time or not temp:
        return result
    time_min, time_max = number(time["lo"]), number(time["hi"] or time["lo"])
    temp_min, temp_max = number(temp["lo"]), number(temp["hi"] or temp["lo"])
    if Decimal(time_min) > Decimal(time_max) or Decimal(temp_min) > Decimal(temp_max):
        return result
    original_unit = time["unit"].lower()
    if not primary and original_unit not in ("min", "分钟"):
        return result
    result.update(time_lower=time_min, time_upper=time_max, temperature_lower=temp_min, temperature_upper=temp_max,
                  unit="s" if primary else "min", source_unit=original_unit,
                  corrected_unit=primary and original_unit in ("min", "分钟"))
    values = (time_min, time_max, temp_min, temp_max)
    if not any(Decimal(value) for value in values) and not primary:
        result["state"] = "NOT_REQUIRED"
    elif all(Decimal(value) > 0 for value in values):
        result["state"] = "REFERENCE"
    return result


def reference(raw):
    raw = raw or {}
    def get(key):
        value = raw.get(key)
        return value.get("display_text", "") if isinstance(value, dict) else value

    return {
        "primary": curing(get("一次加硫条件"), primary=True),
        "secondary": curing(get("二次加硫条件"), primary=False),
        "mold_no": clean(get("模具号")), "mold_size": clean(get("模具尺寸")),
        "material_length": clean(get("料长")), "cut_weight": clean(get("切料重")),
    }


def build_snapshot(rows, raw):
    groups = defaultdict(list)
    problems = set()
    identities = set()
    categories = defaultdict(set)
    line_counts = defaultdict(dict)
    for row in rows:
        customer, project, category, version = (code(row.get(key)) for key in ("customer", "project_no", "category", "version"))
        origin = (customer, project, category, version)
        if not all(origin):
            problems.add("有检验行缺客户代号、项目号、类别或版本。")
        identities.add((customer, project))
        categories[category].add(version)
        name, unit = clean(row.get("inspection_item")), clean(row.get("unit"))
        low, high = number(row.get("lower_limit")), number(row.get("upper_limit"))
        valid = bool(name and unit and low is not None and high is not None and (low or high)
                     and not (low and high and Decimal(low) > Decimal(high)))
        if not valid:
            problems.add("有检验行数值、范围或单位待核对；没有按 0 补齐。")
        sig = (low, high, unit)
        if name in line_counts[origin] and line_counts[origin][name] != sig:
            problems.add("同类同版的同一检验项目有不同限值或单位。")
        line_counts[origin][name] = sig
        groups[origin].append({
            "item": name, "lower": clean(row.get("lower_limit")), "upper": clean(row.get("upper_limit")),
            "unit": unit, "sheet": row.get("sheet") or "", "row": row.get("row"),
            "file": row.get("file") or "", "batch": row.get("batch") or "",
        })
    if len(identities) > 1:
        problems.add("同一订单项次对应多个客户或项目号，待核对。")
    if any(len(versions) > 1 for versions in categories.values()):
        problems.add("同一类别同时带有不同版本，待核对。")
    customer, project = next(iter(identities)) if len(identities) == 1 else ("", "")
    normalized = [(key, sorted(line_counts[key].items())) for key in sorted(groups)]
    digest = hashlib.sha256(json.dumps(normalized, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest() if groups and not problems else ""
    return {
        "customer_code": customer, "project_no": project, "digest": digest,
        "groups": [{"customer_code": key[0], "project_no": key[1], "category": key[2], "version": key[3], "rows": groups[key]}
                   for key in sorted(groups)],
        "problems": sorted(problems), "reference": reference(raw), "inherited": False,
    }


def imported_rows(rows, batch):
    return [{**row, "file": batch.original_name, "batch": str(batch.pk)} for row in rows]


def record_import(records, orders_by_key, batch, user):
    """Freeze a complete received bundle; an omitted sheet does not erase existing evidence."""
    by_order = defaultdict(list)
    for row in records:
        if row["record_type"] == "INSPECTION_CRITERION" and not row.get("stale_source_version"):
            by_order[row.get("order_source_key")].append(row)
    for record in records:
        key = record["source_key"]
        if record["record_type"] != "ORDER" or key not in orders_by_key or record.get("stale_source_version"):
            continue
        order = orders_by_key[key]
        rows = by_order.get(key, [])
        old = OrderInspectionSource.objects.filter(order=order).first()
        snapshot = build_snapshot(imported_rows(rows, batch), record.get("raw_data"))
        explicit = (code(record.get("customer_code")), code(record.get("inspection_project_no")))
        document = batch
        if not rows and old and old.snapshot.get("groups") and old.snapshot.get("product_id") == order.product_specification_id:
            previous = (old.snapshot["customer_code"], old.snapshot["project_no"])
            if not any(value and value != previous[index] for index, value in enumerate(explicit)):
                snapshot = {**old.snapshot, "reference": snapshot["reference"]}
                document = old.document
        if not rows and not old and not any(explicit) and ProductInspectionCriterion.objects.filter(order=order, source_batch__source_type=FACTORY).exists():
            continue
        resolved = (snapshot["customer_code"], snapshot["project_no"])
        if rows and any(value and value != resolved[i] for i, value in enumerate(explicit)):
            snapshot["problems"].append("订单表与检验表的客户代号或项目号不一致。")
            snapshot.update(digest="", customer_code="", project_no="")
        if not snapshot["groups"] and not rows and explicit[0]:
            snapshot.update(customer_code=explicit[0], project_no=explicit[1])
            approved = CustomerInspectionReview.objects.filter(
                product_id=order.product_specification_id, customer_code=explicit[0], project_no=explicit[1], is_current=True,
            ).first() if explicit[1] and order.product_specification_id else None
            if approved:
                snapshot = inherited_snapshot(approved, snapshot["reference"])
        snapshot["product_id"] = order.product_specification_id
        if (old and old.snapshot.get("product_id") == snapshot["product_id"]
                and old.snapshot.get("digest") == snapshot["digest"] and old.snapshot.get("reference") == snapshot["reference"]
                and not old.snapshot.get("inherited") and (snapshot["digest"] or old.snapshot.get("groups") == snapshot["groups"])):
            continue
        OrderInspectionSource.objects.update_or_create(order=order, defaults={"document": document, "recorded_by": user, "snapshot": snapshot})
        if order.customer_code != snapshot["customer_code"]:
            type(order).objects.filter(pk=order.pk).update(customer_code=snapshot["customer_code"])
            order.customer_code = snapshot["customer_code"]


def get_order_snapshots(orders):
    orders = list(orders)
    results = {}
    factory_orders = {o.pk: o for o in orders if order_customer_key(o) == SOURCE}
    if not factory_orders:
        return results
    persisted = {row.order_id: row for row in OrderInspectionSource.objects.filter(order_id__in=factory_orders)}
    raw = defaultdict(list)
    for row in ProductInspectionCriterion.objects.filter(
        order_id__in=set(factory_orders) - persisted.keys(), source_batch__source_type=FACTORY,
    ).select_related("source_batch").defer("source_batch__payload").order_by("source_batch_id", "source_row", "id"):
        order = factory_orders[row.order_id]
        if row.product_specification_id != order.product_specification_id or code(row.item_no) != code(order.item_no):
            continue
        raw[row.order_id].append({
            **{key: getattr(row, key) for key in ("customer", "project_no", "category", "version", "inspection_item", "lower_limit", "upper_limit", "unit")},
            "sheet": row.source_sheet, "row": row.source_row, "file": row.source_batch.original_name, "batch": str(row.source_batch_id),
        })
    for pk, order in factory_orders.items():
        source = persisted.get(pk)
        if source and source.snapshot.get("product_id") != order.product_specification_id:
            results[pk] = {**build_snapshot([], order.raw_data), "problems": ["订单产品关联已变化，请人工核对原要求。"]}
        elif source:
            results[pk] = {**source.snapshot}
        else:
            snapshot = build_snapshot(raw[pk], order.raw_data)
            results[pk] = snapshot
    return results


def approval_map(orders, snapshots):
    products = {o.product_specification_id for o in orders if o.pk in snapshots and snapshots[o.pk]["digest"]}
    digests = {v["digest"] for v in snapshots.values() if v["digest"]}
    return {(v.product_id, v.digest): v for v in
            CustomerInspectionReview.objects.filter(product_id__in=products, digest__in=digests)}


def order_payload(order, snapshot=None, approvals=None):
    if snapshot is None:
        snapshot = get_order_snapshots([order]).get(order.pk)
    if snapshot is None:
        return None
    if approvals is None:
        approvals = approval_map([order], {order.pk: snapshot})
    approval = approvals.get((order.product_specification_id, snapshot["digest"]))
    status = "CONFLICT" if snapshot["problems"] else (
        "INHERITED" if snapshot["inherited"] and approval else "CONFIRMED" if approval else
        "PENDING" if snapshot["groups"] else "MISSING"
    )
    return {**snapshot, "status": status, "source": SOURCE, "supplier": order.source_system,
            "order_id": order.pk, "order_no": order.order_no, "item_no": order.item_no,
            "specification": order.specification, "material": order.material,
            "review_id": approval.pk if approval else None,
            "reviewed_at": approval.confirmed_at.isoformat() if approval else None}


def card_payloads(orders, snapshots):
    approvals = approval_map(orders, snapshots)
    return [order_payload(order, snapshots[order.pk], approvals) for order in orders
            if order.pk in snapshots and snapshots[order.pk]["groups"]]


def current_options(order):
    if not order.product_specification_id:
        return []
    return [{"id": r.pk, "customer_code": r.customer_code, "project_no": r.project_no,
             "versions": [{"category": g["category"], "version": g["version"]} for g in r.snapshot["groups"]]}
            for r in CustomerInspectionReview.objects.filter(product_id=order.product_specification_id, is_current=True)
            .order_by("customer_code", "project_no")]


def inherited_snapshot(approved, ref):
    return {**approved.snapshot, "product_id": approved.product_id, "inherited": True, "reference": ref}


def previous_diff(order, payload):
    if payload["status"] != "PENDING":
        return []
    prior = CustomerInspectionReview.objects.filter(product_id=order.product_specification_id,
        customer_code=payload["customer_code"], project_no=payload["project_no"], is_current=True).first()
    if not prior:
        return []

    def values(snapshot):
        return {(group["category"], row["item"]): (group["version"], row["lower"], row["upper"], row["unit"])
                for group in snapshot["groups"] for row in group["rows"]}

    old, new = values(prior.snapshot), values(payload)
    return [{"category": key[0], "item": key[1], "previous": old.get(key), "incoming": new.get(key)}
            for key in sorted(old.keys() | new.keys()) if old.get(key) != new.get(key)]


@transaction.atomic
def review_order(order, user, data):
    from quality.models import QualityOrder
    if not order.product_specification_id:
        raise ValidationError({"inspection": "请先关联明确的产品档案。"})
    # Serialize changes to the current revision for this product, including on SQLite.
    ProductSpecification.objects.filter(pk=order.product_specification_id).update(product_name=F("product_name"))
    order = QualityOrder.objects.select_related("source_batch", "last_source_batch").get(pk=order.pk)
    payload = order_payload(order)
    if not payload or not order.product_specification_id:
        raise ValidationError({"inspection": "仅适用翔邦联络单。"})
    if data.get("action") == "inherit":
        if payload["status"] != "MISSING":
            raise ValidationError({"inspection": "已有本单原表或需核对的问题，不用其他订单覆盖。"})
        try:
            approved = CustomerInspectionReview.objects.get(pk=data.get("review_id"), product_id=order.product_specification_id, is_current=True)
        except (CustomerInspectionReview.DoesNotExist, ValueError, TypeError, OverflowError):
            raise ValidationError({"inspection": "请选择本产品适用的现行已确认版本。"})
        if ((payload["customer_code"] and payload["customer_code"] != approved.customer_code)
                or (payload["project_no"] and payload["project_no"] != approved.project_no)):
            raise ValidationError({"inspection": "所选版本与本单已知客户或项目不符。"})
        snapshot = inherited_snapshot(approved, payload["reference"])
        OrderInspectionSource.objects.update_or_create(order=order, defaults={"snapshot": snapshot, "document": None, "recorded_by": user})
        QualityOrder.objects.filter(pk=order.pk).update(customer_code=approved.customer_code)
    elif data.get("action") == "confirm":
        if not payload["digest"] or payload["digest"] != data.get("digest"):
            raise ValidationError({"inspection": "标准有冲突或与预览不同，请刷新后核对。"})
        if payload["review_id"]:
            return payload
        current = CustomerInspectionReview.objects.filter(
            product_id=order.product_specification_id, customer_code=payload["customer_code"],
            project_no=payload["project_no"], is_current=True,
        )
        # If a superficially older order is being confirmed, it remains usable
        # for its own bundle but must not supersede a more recent source.
        previous = current.select_related("order").first()
        previous_date = previous.snapshot.get("source_document_at") if previous else None
        source_date = order.source_document_at.isoformat() if order.source_document_at else None
        is_current = not previous or bool(source_date and previous_date and source_date >= previous_date)
        if is_current:
            current.update(is_current=False)
        snapshot = {key: payload[key] for key in ("groups", "customer_code", "project_no", "digest", "problems", "inherited")}
        snapshot["source_document_at"] = source_date
        CustomerInspectionReview.objects.create(
            product_id=order.product_specification_id, order=order, customer_code=payload["customer_code"],
            project_no=payload["project_no"], digest=payload["digest"], snapshot=snapshot, is_current=is_current, confirmed_by=user,
        )
    else:
        raise ValidationError({"action": "请选择核对本单或沿用已确认标准。"})
    return order_payload(order)


def product_reviews(product, page=1):
    start = (page - 1) * 30
    orders = list(product.orders.filter(
        inspection_criteria__source_batch__source_type=FACTORY,
    ).select_related("source_batch", "last_source_batch").defer("source_batch__payload", "last_source_batch__payload")
        .order_by("-source_document_at", "-pk").distinct()[start:start + 31])
    has_next = len(orders) == 31
    orders = orders[:30]
    snapshots = get_order_snapshots(orders)
    approved = approval_map(orders, snapshots)
    unique = {}
    versions = list(CustomerInspectionReview.objects.filter(product=product)
                    .select_related("order").order_by("-is_current", "-confirmed_at", "-pk")[start:start + 31])
    has_next = has_next or len(versions) == 31
    for match in versions[:30]:
        order = match.order
        unique[match.digest] = {
            "order_id": order.pk, "order_no": order.order_no, "item_no": order.item_no,
            "customer_code": match.customer_code, "project_no": match.project_no,
            "groups": match.snapshot["groups"], "status": "CURRENT" if match.is_current else "HISTORICAL",
            "key": match.digest,
        }
    for order in orders:
        snapshot = snapshots.get(order.pk)
        if not snapshot or not snapshot["groups"]:
            continue
        digest = snapshot["digest"]
        key = digest or order.pk
        if key not in unique:
            match = approved.get((product.pk, digest))
            unique[key] = {"order_id": order.pk, "order_no": order.order_no, "item_no": order.item_no,
                           "customer_code": snapshot["customer_code"], "project_no": snapshot["project_no"],
                           "groups": snapshot["groups"], "status": ("CURRENT" if match.is_current else "HISTORICAL") if match else "PENDING",
                           "key": str(key)}
    return list(unique.values()), has_next
