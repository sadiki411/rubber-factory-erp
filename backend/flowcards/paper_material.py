"""Material fields frozen for a card: never turn an order reference into an actual."""
import re

STRIP_FIELDS = ("large_strip_weight_g", "large_strip_count", "small_strip_weight_g", "small_strip_count")


def paper_material(product, run=None):
    def populated(value):
        return value is not None and value != ""

    # Take a whole layout from one source; never mix a run's large strip with
    # another layout's small strip. A recorded zero is not a missing value.
    run_layout = run and any(populated(getattr(run, key)) for key in STRIP_FIELDS)
    source = run if run_layout else product
    return {
        "actual_material_length": product.actual_material_length.strip(),
        "strip_source": "RUN" if run_layout else "PRODUCT",
        **{key: str(value) if populated(value := getattr(source, key)) else ""
           for key in STRIP_FIELDS},
    }


def material_text(material):
    length = material.get("actual_material_length") or "____"
    if re.fullmatch(r"\d+(?:\.\d+)?", length):
        length += " mm"
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
