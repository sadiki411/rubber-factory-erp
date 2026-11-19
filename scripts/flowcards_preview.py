"""Synthetic PDF review, never opens Django or a database. Run from repo root."""
import datetime
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from flowcards.pdf import render_cards

root = Path(__file__).resolve().parents[1] / "output" / "pdf"
root.mkdir(parents=True, exist_ok=True)
for whole, filename in [("REQUIRED", "工序流转卡_需整模_隔离样张.pdf"), ("NOT_REQUIRED", "工序流转卡_不需整模_隔离样张.pdf")]:
    snapshot = {"product": {"id": 1, "name": "隔离示例O型圈", "customer_product_no": "215", "specification": "215", "material": "N7200"},
        "orders": [{"id": 1, "order_no": "DEMO-ORDER-01", "item_no": "3", "customer": "隔离示例"}, {"id": 2, "order_no": "DEMO-ORDER-02", "item_no": "4", "customer": "隔离示例"}],
        "whole_mold": whole, "appearance": [{"id": i+1, "name": n} for i, n in enumerate(["毛刺", "杂质", "包风", "缺料"])],
        "template": {"renderer": "flowcard-draft-20261118-v1", "version": "草稿"},
        "standard": {"version": 1, "snapshot": {"criteria": [
            {"id": 1, "stage": "UPPER_TEMP", "name": "上模", "lower": "170", "upper": "180", "unit": "℃", "method": "接触探头"},
            {"id": 2, "stage": "LOWER_TEMP", "name": "下模", "lower": "168", "upper": "178", "unit": "℃", "method": "接触探头"},
            {"id": 3, "stage": "MOLDING_HARDNESS", "name": "成型后", "lower": "65", "upper": "75", "unit": "Shore A", "method": "示例方法"},
            {"id": 4, "stage": "BAKED_HARDNESS", "name": "烤后", "lower": "66", "upper": "74", "unit": "Shore A", "method": "示例方法"},
            {"id": 5, "stage": "DIMENSION", "name": "内径", "lower": "21.0", "upper": "21.3", "unit": "mm", "method": "示例方法"},
        ]}}}
    group = SimpleNamespace(snapshot=snapshot)
    cards = [(SimpleNamespace(pk=uuid.UUID(int=i), card_no=f"FC-DEMO-20261118-{i:010d}", group=group,
        original_date=datetime.date(2026, 11, 18), sequence=seq, denominator=21, planned_molds=molds), reprint)
        for i, seq, molds, reprint in [(1, 1, 100, 0), (3, 3, 100, 0), (21, 21, 50, 1)]]
    path = root / filename
    path.write_bytes(render_cards(cards))
    print(path)

if "--long-fields" in sys.argv:
    import copy
    long = copy.deepcopy(snapshot)
    long["product"]["specification"] = "隔离长规格字段不裁切核对-" * 12
    long["orders"] = [{"id": i, "order_no": f"ISOLATED-LONG-ORDER-{i:06d}", "item_no": str(i), "customer": "隔离示例"} for i in range(12)]
    long["standard"]["snapshot"]["criteria"].extend([{ "id": 10+i, "stage": "DIMENSION", "name": f"附加尺寸{i}", "lower": "1", "upper": "2", "unit": "mm", "method": "隔离方法" } for i in range(4)])
    path = root.parents[1] / "tmp" / "pdfs" / "long-fields-review.pdf"
    path.parent.mkdir(parents=True, exist_ok=True)
    sample = SimpleNamespace(pk=uuid.UUID(int=99), card_no="FC-20261118-0000000000000099", group=SimpleNamespace(snapshot=long), original_date=datetime.date(2026, 11, 18), sequence=1, denominator=1, planned_molds=100)
    path.write_bytes(render_cards([(sample, 2)]))
    print(path)
