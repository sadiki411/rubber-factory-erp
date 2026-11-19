"""In-memory synthetic mixed-criteria PDF evidence; issues no cards/templates."""
import copy
import hashlib
import json
import os
import subprocess
import sys
import types
import uuid
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.update(SQLITE_PATH=str(ROOT / ".codex_tmp/flow-pilot-20261119.sqlite3"), DJANGO_SETTINGS_MODULE="erp.settings")
sys.path.insert(0, str(ROOT / "backend"))
import django
django.setup()
from flowcards.pdf_v3 import render_cards_v3
from flowcards.pdf_v4 import render_cards_v4
from flowcards.services import TEMPLATE

target = ROOT / "output/pdf/flow-pilot-20261119"
target.mkdir(parents=True, exist_ok=True)
def criterion(name, mode="UPPER", **extra):
    return {"id": len(name), "stage": "DIMENSION", "name": name, "lower": "", "upper": "3.05" if mode == "UPPER" else "",
            "unit": "mm", "method": "合成隔离测法，非客户要求", "limit_mode": mode, "upper_inclusive": True, **extra}

none = criterion("内径不要求测", "NONE")
line = criterion("线径")
cases = [("01_常规O圈_不测在前", [none, line]), ("02_常规O圈_不测在后", [line, none]),
         ("03_客户例外_两必测中插不测", [line, criterion("高度不测", "NONE"), criterion("内径", "RANGE", lower="99.8", upper="100.2")]),
         ("04_非O杂件_四必测跨页", [criterion("宽度"), none, criterion("高度"), criterion("槽宽"), criterion("厚度")]),
         ("05_全部明确不测", [none, criterion("线径不测", "NONE")])]
evidence = {"synthetic_only": True, "issued_identity": False, "formal_template_published": False, "samples": []}
old = types.ModuleType("flowcards._frozen_review_v3"); old.__package__ = "flowcards"
exec(subprocess.check_output(["git", "show", "e6a469a:backend/flowcards/pdf_v3.py"], cwd=ROOT).decode("utf-8"), old.__dict__)
for i, (name, dimensions) in enumerate(cases, 1):
    criteria = [{"id": n, "name": heading, "stage": stage, "lower": "60", "upper": "75", "unit": unit, "method": "隔离已确认测法",
                 "limit_mode": "RANGE"} for n, heading, stage, unit in [(101,"上模温度","UPPER_TEMP","℃"), (102,"下模温度","LOWER_TEMP","℃"),
                    (103,"成型硬度","MOLDING_HARDNESS","示例量表"), (104,"烤后硬度","BAKED_HARDNESS","示例量表")]]
    standard = {"version": 1, "snapshot": {"criteria": criteria + dimensions,
        "dimension_count": sum(c["limit_mode"] != "NONE" for c in dimensions), "bake": {"state": "NOT_APPLICABLE"},
        "hardness": {stage: {"object":"PRODUCT", "scale":"示例量表"} for stage in ("MOLDING_HARDNESS","BAKED_HARDNESS")}}}
    snapshot = {"template": {**TEMPLATE, "version":"草稿"}, "product": {"specification":"隔离100x3" if i != 4 else "隔离杂件12x4", "material":"DEMO-ONLY",
        "customer_product_no": f"SYNTH-{i}", "name":"合成样张非客户标准"}, "orders":[{"order_no":"SYNTH-NOT-ISSUED", "item_no":str(i), "customer":"合成客户"}],
        "standard":standard, "whole_mold":"REQUIRED" if i in (3,4) else "NOT_REQUIRED",
        "appearance":[{"id":n,"name":name} for n,name in enumerate(["毛刺","杂质","包风","缺料"],1)]}
    card = types.SimpleNamespace(pk=uuid.UUID(int=i), card_no=f"FC-SYNTH-PREVIEW-20261119-{i:03}",
        group=types.SimpleNamespace(snapshot=snapshot), original_date=date(2026,11,19), planned_molds=100, sequence=1, denominator=1)
    raw = render_cards_v4([(card,0)])
    path = target / (name + ".pdf"); path.write_bytes(raw)
    frozen_before, frozen_after = old.render_cards_v3([(card,0)]), render_cards_v3([(card,0)])
    assert frozen_before == frozen_after, name
    evidence["samples"].append({"file":str(path.relative_to(ROOT)).replace("\\","/"), "sha256":hashlib.sha256(raw).hexdigest(),
        "expected_dimensions":[c["name"] for c in dimensions if c["limit_mode"] != "NONE"],
        "v3_old_source_sha256":hashlib.sha256(frozen_before).hexdigest(), "v3_current_default_sha256":hashlib.sha256(frozen_after).hexdigest(), "v3_identical":True})
(target/"mixed-pdf-evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2),encoding="utf-8")
print(json.dumps(evidence, ensure_ascii=False, indent=2))
