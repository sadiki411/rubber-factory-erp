"""Human-readable annotations for imported customer inspection item codes."""
from unicodedata import normalize


# Keep aligned with frontend/src/inspectionItemLabel.ts; neither is stored in snapshots.
ITEM_NOTES = {
    "HD": "硬度",
    "SG": "密度",
    "ID": "内径",
    "OD": "外径",
    "THK": "厚度",
    "C/S-A": "轴向线径，方向待核",
    "C/S-R": "径向线径，方向待核",
    "MISMATCH": "合模错位",
    "FLASHT": "毛边厚度",
    "FLASHLEN": "毛边伸出长度",
    "FLASH": "毛边，量法待确认",
    "[1]": "图纸尺寸1，位置见图纸",
    "[2]": "图纸尺寸2，位置见图纸",
    "[3]": "图纸尺寸3，位置见图纸",
}


def inspection_item_label(item):
    value = "" if item is None else str(item)
    note = ITEM_NOTES.get(normalize("NFKC", value).strip().upper())
    return f"{value}（{note}）" if note else value
