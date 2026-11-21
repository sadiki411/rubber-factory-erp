"""Explicit comparison rules; legacy snapshots retain their two-sided semantics."""
from decimal import Decimal, InvalidOperation
from rest_framework.exceptions import ValidationError


def limit_mode(row):
    return row.get("limit_mode") or ("RANGE" if row.get("numeric") else "PENDING")


def validate_limits(row):
    mode = limit_mode(row)
    if mode not in ("UPPER", "LOWER", "RANGE", "PENDING", "NONE"):
        raise ValidationError({"limit_mode": "请选择仅上限、上下限、界限待确认或不要求测。"})
    for key in ("lower_inclusive", "upper_inclusive"):
        if key in row and not isinstance(row[key], bool):
            raise ValidationError({key: "边界包含规则须明确为是/否。"})
    if mode in ("UPPER", "LOWER", "RANGE"):
        try:
            upper = Decimal(row["upper"]) if mode != "LOWER" else None
            lower = Decimal(row["lower"]) if mode != "UPPER" else None
            if (upper is not None and not upper.is_finite()) or (lower is not None and not lower.is_finite()) or (upper is not None and lower is not None and lower > upper):
                raise InvalidOperation
            if lower == upper and not (row.get("lower_inclusive", True) and row.get("upper_inclusive", True)):
                raise InvalidOperation
            if mode == "UPPER" and row.get("lower") not in (None, ""):
                raise ValidationError({"lower": "仅上限时请留空下限；有下限的要求应选择上下限，不能忽略。"})
            if mode == "LOWER" and row.get("upper") not in (None, ""):
                raise ValidationError({"upper": "仅下限时请留空上限；有上限请选择上下限。"})
        except (KeyError, InvalidOperation, ValueError, TypeError):
            raise ValidationError({"criteria": "请填写明确的数值界限；上下限控制时两端必填，不用0代替空白。"})
    return mode


def limit_text(row):
    mode = limit_mode(row)
    if mode == "NONE":
        return "不要求测"
    if mode == "PENDING":
        return "界限待确认"
    unit = row.get("unit") or "单位待确认"
    upper = row.get("upper", "")
    if mode == "UPPER":
        return f"{'≤' if row.get('upper_inclusive', True) else '<'} {upper} {unit}"
    if mode == "LOWER":
        return f"{'≥' if row.get('lower_inclusive', True) else '>'} {row.get('lower', '')} {unit}"
    if row.get("lower_inclusive", True) and row.get("upper_inclusive", True):
        return f"{row.get('lower', '')}～{upper} {unit}"
    return f"{'[' if row.get('lower_inclusive', True) else '('}{row.get('lower', '')}, {upper}{']' if row.get('upper_inclusive', True) else ')'} {unit}"


def evaluate(row, actual, method_matches):
    mode = limit_mode(row)
    result = {"judgment": "NOT_EVALUATED", "limit_text": limit_text(row)}
    if mode in ("PENDING", "NONE"):
        return {**result, "measurement_message": "不要求测，未判定" if mode == "NONE" else "界限待确认，未判定"}
    if not row.get("unit") or (not row.get("method") and row.get("comparison_basis") != "DIMENSION_READING") or not method_matches:
        return {**result, "measurement_message": "单位/测法未确认或与标准不一致，未判定"}
    upper = Decimal(row["upper"]) if mode != "LOWER" else None
    lower = Decimal(row["lower"]) if mode != "UPPER" else None
    side = "UPPER" if upper is not None and (actual > upper or actual == upper and not row.get("upper_inclusive", True)) else (
        "LOWER" if lower is not None and (actual < lower or actual == lower and not row.get("lower_inclusive", True)) else None)
    if side:
        difference = abs(actual - (upper if side == "UPPER" else lower))
        text = f"超上限 {difference} {row['unit']}" if side == "UPPER" else f"低于下限 {difference} {row['unit']}"
        if difference == 0:
            text = "达到不包含的上限" if side == "UPPER" else "达到不包含的下限"
        return {**result, "judgment": "OUTSIDE", "limit_side": side, "difference": str(difference), "measurement_message": text}
    return {**result, "judgment": "WITHIN", "measurement_message": (
        "达到上限，未超" if actual == upper else "未超上限" if mode == "UPPER" else "达到下限，未低于" if actual == lower else "未低于下限" if mode == "LOWER" else "在确认范围内")}
