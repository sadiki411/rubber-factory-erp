"""Material fields frozen for a card: never turn an order reference into an actual."""
import re
from decimal import Decimal

from rest_framework.exceptions import ValidationError

STRIP_FIELDS = ("large_strip_weight_g", "large_strip_count", "small_strip_weight_g", "small_strip_count")
WEIGHT_PATTERN = r"([0-9]{1,9}(?:\.[0-9]{1,2})?)\s*(?:g|克)?"


def normalize_weight(value):
    # A slash is always large/small, not a fraction or the weight of one mold.
    parts = value.replace("／", "/").split("/")
    matches = [re.fullmatch(WEIGHT_PATTERN, part.strip(), re.IGNORECASE) for part in parts]
    if len(parts) > 2 or not all(matches):
        return None
    return "/".join(format(Decimal(match[1]).normalize(), "f") for match in matches)


def paper_material(product, run=None, overrides=None, *, compact=False):
    def populated(value):
        return value is not None and value != ""

    # Take a whole layout from one source; never mix a run's large strip with
    # another layout's small strip. A recorded zero is not a missing value.
    run_layout = run and any(populated(getattr(run, key)) for key in STRIP_FIELDS)
    source = run if run_layout else product
    result = {
        "actual_material_length": product.actual_material_length.strip(),
        "strip_source": "RUN" if run_layout else "PRODUCT",
        **{key: str(value) if populated(value := getattr(source, key)) else ""
           for key in STRIP_FIELDS},
    }
    if not compact:
        return result
    actual = product.actual_cut_weight.strip()
    actual_weight = normalize_weight(actual)

    def strip_weight(obj):
        if obj is None or not populated(large := obj.large_strip_weight_g):
            return ""
        small = obj.small_strip_weight_g
        return normalize_weight(f"{large}/{small}" if populated(small) else str(large)) or ""

    # A partial run layout (e.g. count only) must not suppress a usable product value.
    run_weight, product_weight = strip_weight(run), strip_weight(product)
    weight = run_weight or actual_weight or product_weight or ""
    weight_source = ("RUN" if run_weight else "PRODUCT_ACTUAL" if actual_weight
                     else "PRODUCT_STRIPS" if product_weight else "MISSING")
    result.update(weight_g=weight, weight_source=weight_source,
                  length_source="PRODUCT_ACTUAL" if result["actual_material_length"] else "MISSING",
                  weight_reference=actual if actual and not actual_weight else "")
    if overrides is not None:
        if not isinstance(overrides, dict):
            raise ValidationError({"paper_material": "请分别填写本次料长与料重。"})
        for key in ("actual_material_length", "weight_g"):
            if key not in overrides:
                continue
            value = overrides[key]
            if not isinstance(value, str) or len(value) > (100 if key == "actual_material_length" else 40):
                raise ValidationError({"paper_material": "本次料长或料重格式、长度不正确。"})
            value = value.strip()
            if key == "weight_g" and value:
                value = normalize_weight(value)
                if value is None:
                    raise ValidationError({"paper_material": "料重单位为 g，请填单值或大条/小条，例如 13 或 13/11（最多两位小数）。"})
            if value != result[key]:
                result["weight_source" if key == "weight_g" else "length_source"] = "CARD"
            result[key] = value
    return result


def material_text(material):
    length = material.get("actual_material_length") or "____"
    if re.fullmatch(r"\d+(?:\.\d+)?", length):
        length += " mm"
    # Only new snapshots opt in. Existing jobs and issued old cards keep their original layout.
    if "weight_g" in material:
        return f"料长 {length}    料重 {material['weight_g'] or '____'} g"
    large, small = material.get("large_strip_weight_g"), material.get("small_strip_weight_g")

    def strip(weight, quantity):
        amount = str(weight) if weight not in (None, "") else "____"
        if "." in amount:
            amount = amount.rstrip("0").rstrip(".")
        return f"{amount}g" + (f"×{quantity}条" if quantity else "")

    large_count, small_count = material.get("large_strip_count"), material.get("small_strip_count")
    if small or small_count:
        weight = f"大条 {strip(large, large_count)} / 小条 {strip(small, small_count)}"
    else:
        weight = f"条重 {strip(large, large_count)}"
    return f"料长 {length}    {weight}"
