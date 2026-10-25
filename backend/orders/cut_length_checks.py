"""Advisory checks against manually maintained cutting lengths, never source data."""

import csv
import hashlib
import io
import json
import re
import unicodedata
from collections import defaultdict
from decimal import Decimal, InvalidOperation

from .models import BusinessImportBatch, ProductSpecification


TOLERANCE_MM = Decimal("5")
_LENGTH = re.compile(
    r"(?:(?:实际切料长|实际料长|切料长|料长|人工实测|实测)\s*[:：]?\s*)?"
    r"(\d+(?:\.\d+)?)\s*(?:mm|毫米)?", re.IGNORECASE,
)


class CutLengthConfirmationRequired(Exception):
    def __init__(self, check):
        self.check = check
        super().__init__("请核对最新的料长提醒并勾选已知悉后继续导入；本次尚未写入业务数据。")


def parse_length_mm(value):
    """Only a positive, unambiguous single mm measurement is comparable."""
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    match = _LENGTH.fullmatch(text)
    if not match:
        return None
    try:
        result = Decimal(match[1])
        return result if result.is_finite() and result > 0 else None
    except InvalidOperation:
        return None


def _key(value):
    return "".join(unicodedata.normalize("NFKC", str(value or "")).split()).casefold()


def _number(value):
    return format(value, "f") if value is not None else None


def build_cut_length_check(records, source_type, *, lock=False):
    if source_type != BusinessImportBatch.SourceType.FACTORY_WORK_CONTACT:
        return None
    products = {
        row["source_key"]: row for row in records
        if row["record_type"] == "PRODUCT_SPECIFICATION"
    }
    orders = [row for row in records if row["record_type"] == "ORDER"]
    keys = {(_key(row.get("specification")), _key(row.get("material"))) for row in orders}
    fields = ("id", "specification", "material", "actual_material_length")
    # One bulk lookup, not one query per order. Mold IDs/source lengths do not
    # determine the manual baseline. Ambiguous identities stay explicitly unknown.
    candidates = [
        row for row in ProductSpecification.objects.filter(is_active=True).order_by("id").values(*fields)
        if (_key(row["specification"]), _key(row["material"])) in keys
    ] if orders else []
    if lock and candidates:
        candidates = list(ProductSpecification.objects.select_for_update().filter(
            pk__in=[row["id"] for row in candidates], is_active=True,
        ).order_by("id").values(*fields))
    by_key = defaultdict(list)
    for candidate in candidates:
        by_key[(_key(candidate["specification"]), _key(candidate["material"]))].append(candidate)

    rows = []
    for order in orders:
        product = products.get(order.get("product_spec_source_key"), {})
        matches = by_key[(_key(order.get("specification")), _key(order.get("material")))]
        linked_id = product.get("match_id") or order.get("product_spec_id")
        baseline = next((row for row in matches if row["id"] == linked_id), None)
        if baseline is None and len(matches) == 1:
            baseline = matches[0]
        customer_raw = str(product.get("material_length") or "").strip()
        actual_raw = str(baseline["actual_material_length"] or "").strip() if baseline else ""
        customer = parse_length_mm(customer_raw)
        actual = parse_length_mm(actual_raw)
        difference = customer - actual if customer is not None and actual is not None else None
        if baseline is None and len(matches) > 1:
            status, message = "AMBIGUOUS_PRODUCT", "同规格、材质存在多条档案，无法唯一确定实际切料长，请人工核对。"
        elif not actual_raw:
            status, message = "MISSING_ACTUAL", "未填写实际切料长，待补充核对；可知悉后继续导入。"
        elif actual is None:
            status, message = "UNREADABLE_ACTUAL", "实际切料长不是明确的单个毫米值，请人工核对。"
        elif not customer_raw:
            status, message = "MISSING_CUSTOMER", "本次文件未提供料长，无法比较，请人工核对。"
        elif customer is None:
            status, message = "UNREADABLE_CUSTOMER", "客户料长不是明确的单个毫米值，请人工核对。"
        elif difference < -TOLERANCE_MM:
            status, message = "SHORTER", f"客户料长偏短 {_number(-difference)}mm，可能需要重新出片，建议反馈翔邦。"
        elif difference > TOLERANCE_MM:
            status, message = "LONGER", f"客户料长偏长 {_number(difference)}mm，建议核对并反馈翔邦。"
        else:
            status, message = "IN_TOLERANCE", "差值在±5mm以内，无需提醒。"
        rows.append({
            "row_key": order["row_key"], "sheet": order["sheet"], "row": order["row"],
            "order_no": order.get("order_no", ""), "item_no": order.get("item_no", ""),
            "specification": order.get("specification", ""), "material": order.get("material", ""),
            "product_id": baseline["id"] if baseline else None,
            "customer_length": customer_raw, "actual_length": actual_raw,
            "customer_mm": _number(customer), "actual_mm": _number(actual),
            "difference_mm": _number(difference), "status": status, "message": message,
            "candidates": matches if baseline is None and len(matches) > 1 else [],
        })
    check = {"version": 1, "tolerance_mm": "5", "rows": rows,
             "alert_count": sum(row["status"] != "IN_TOLERANCE" for row in rows)}
    check["digest"] = hashlib.sha256(json.dumps(
        check, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    return check


def cut_length_report(check):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(["订单号", "项次", "规格", "材质", "客户料长(mm)", "实际切料长(mm)",
                     "差额(mm，客户-实际)", "提醒", "工作表", "行号"])
    for row in (check or {}).get("rows", []):
        if row["status"] == "IN_TOLERANCE":
            continue
        values = [row.get(field, "") for field in (
            "order_no", "item_no", "specification", "material", "customer_length",
            "actual_length", "difference_mm", "message", "sheet", "row",
        )]
        # Prevent spreadsheet formulas from executing when opening a customer file.
        writer.writerow([
            "'" + value if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@"))
            else value for value in values
        ])
    return stream.getvalue().encode("utf-8-sig")
