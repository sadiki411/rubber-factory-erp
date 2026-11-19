"""Supplement/export ONLY the disposable DB created by the review seed.

Use --supplement before employee UI resumes C; --export after UI generation.
No official credentials, source files, historical rows or template publication.
"""
import hashlib
import json
import os
import subprocess
import sys
import uuid
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = Path(os.environ["SQLITE_PATH"]).resolve()
if DB.parent != (ROOT / ".codex_tmp").resolve() or not DB.name.startswith("card-preparation-20261119-") or not DB.exists():
    raise SystemExit("Refusing non-review DB")
sys.path.insert(0, str(ROOT / "backend"))
os.environ.update(DJANGO_SETTINGS_MODULE="erp.settings", FLOW_CARDS_WRITE_ENABLED="1", FLOW_CARDS_PREPARATION_ENABLED="1")
import django
django.setup()
from django.contrib.auth import get_user_model
from flowcards import preparation as p, services as s
from flowcards.models import CardGroup, InternalCard, TemplatePublication
from flowcards.pdf import render_cards
from production.models import ProductionRun, ProductionDailyLog
from quality.models import QualityOrder, QualityShipmentBatch

if any(not number.startswith("SYNTH-") for number in QualityOrder.objects.values_list("order_no", flat=True)):
    raise SystemExit("Non-synthetic order found; refusing all writes")
OUT = ROOT / "output" / "card-preparation-20261119"
OUT.mkdir(parents=True, exist_ok=True)
maintainer = get_user_model().objects.get(username="synthetic-standard")
operator = get_user_model().objects.get(username="synthetic-operator")

if "--supplement" in sys.argv:
    order = QualityOrder.objects.get(order_no="SYNTH-PREP-20261119-1")
    draft = CardGroup.objects.get(orders=order, status="DRAFT")
    before = {"draft_id": str(draft.pk), "revision": draft.revision, "notes": draft.snapshot.get("preparation_notes"), "molds_per_card": draft.molds_per_card}
    if ProductionRun.objects.filter(order=order).exists():
        raise SystemExit("Supplement already applied; inspect instead of repeating")
    standard = s.confirm_standard({"request_key": uuid.uuid4().hex, "product_id": order.product_specification_id,
        "customer_key": "艾斯霖", "source": "合成演示标准：模拟授权维护人补齐；非正式客户参数", "source_kind": "FACTORY_PROCESS",
        "whole_mold": "NOT_REQUIRED", "dimension_count": 2, "appearance_ids": list(s.AppearanceIssue.objects.values_list("pk", flat=True)[:4]),
        "criteria": [{"name": name, "lower": "", "upper": upper, "unit": "mm", "limit_mode": "UPPER", "upper_inclusive": True,
            "method": "合成量具，仅测试字段", "stage": "DIMENSION", "numeric": True} for name, upper in (("线径", "3.60"), ("客户加测内径", "230.50"))],
        "route": {"confirmed": True, "molding": True, "patrol": True, "deburr": True, "bake": False, "baked_hardness": False, "inspection": True},
        "packaging": {"molds_per_card": 100, "patrol_every_molds": 50},
        "process_parameters": {key: {"state": "CONFIRMED", "lower": low, "upper": high, "unit": "℃", "source": "合成表达能力测试"}
            for key, low, high in (("upper_set_temperature", "175", "185"), ("lower_set_temperature", "175", "185"), ("upper_contact_temperature", "165", "175"), ("lower_contact_temperature", "165", "175"))}}, maintainer)
    p.activate_standard(standard, maintainer)
    run = ProductionRun.objects.create(order=order, product_specification=order.product_specification, order_no=order.order_no, specification=order.specification,
        material=order.material, order_quantity=3208, cavities=16, planned_mold_count=201, is_ledger_only=True, created_by=operator)
    # An independent repeat order demonstrates the next order reusing its standard.
    original = QualityOrder.objects.get(order_no="SYNTH-PREP-20261119-0")
    next_order = QualityOrder.objects.create(order_no="SYNTH-REPEAT-A-V5", item_no="1", product_specification=original.product_specification,
        specification=original.specification, material=original.material, order_quantity=3208, production_required=True, source_system="翔邦", created_by=operator)
    next_run = ProductionRun.objects.create(order=next_order, product_specification=original.product_specification, order_no=next_order.order_no,
        specification=next_order.specification, material=next_order.material, order_quantity=3208, cavities=16, planned_mold_count=201, is_ledger_only=True, created_by=operator)
    before.update({"supplemented_by": "synthetic-standard via fixture (not employee UI)", "run_id": run.pk, "standard_id": standard.pk,
        "repeat_order_id": next_order.pk, "repeat_run_id": next_run.pk, "quantity_baseline_after_fixture": {"runs": ProductionRun.objects.count(), "logs": ProductionDailyLog.objects.count(), "shipments": QualityShipmentBatch.objects.count()}})
    (OUT / "C-supplement.json").write_text(json.dumps(before, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(before, ensure_ascii=False))

if "--export" in sys.argv:
    result = {"synthetic_only": True, "official_writes": False, "template_publications": TemplatePublication.objects.count(), "groups": []}
    for group in CardGroup.objects.order_by("created_at"):
        result["groups"].append({"id": str(group.pk), "revision": group.revision, "status": group.status, "run_id": group.run_id,
            "range": [group.range_start, group.range_end], "orders": list(group.orders.values_list("id", flat=True)),
            "cards": list(group.cards.values("card_no", "planned_molds", "reprint_count")), "snapshot_sha256": s.digest(group.snapshot), "renderer": group.snapshot["template"]["renderer"]})
    for order_no, name in (("SYNTH-REPEAT-A-V5", "sample-upper-only.pdf"), ("SYNTH-PREP-20261119-1", "sample-customer-inner.pdf")):
        group = CardGroup.objects.get(orders__order_no=order_no, status="GENERATED")
        (OUT / name).write_bytes(render_cards([(group.cards.first(), 0)]))
    old_card = InternalCard.objects.filter(group__orders__order_no="SYNTH-PREP-20261119-0").first()
    baseline = subprocess.check_output(["git", "show", "0031fa23cb1ff0ce04857fad3fb850c09de532d7:backend/flowcards/pdf_v3.py"], cwd=ROOT).decode("utf-8")
    namespace = {"__package__": "flowcards", "__name__": "flowcards.baseline_pdf_v3"}
    exec(compile(baseline, "baseline_pdf_v3.py", "exec"), namespace)
    result["old_card"] = {"id": str(old_card.pk), "card_no": old_card.card_no, "snapshot_sha256": s.digest(old_card.group.snapshot)}
    for reprint in (0, 1):
        current = render_cards([(old_card, reprint)])
        old = namespace["render_cards_v3"]([(old_card, reprint)], applicable_dimensions=True)
        if current != old:
            raise AssertionError("Frozen v4 rendering changed")
        result["old_card"][f"pdf_reprint_{reprint}_sha256"] = hashlib.sha256(current).hexdigest()
        (OUT / f"frozen-v4-reprint-{reprint}.pdf").write_bytes(current)
    result["old_card"]["baseline_bytes_identical"] = True
    result["quantities_after_ui"] = {"runs": ProductionRun.objects.count(), "logs": ProductionDailyLog.objects.count(), "shipments": QualityShipmentBatch.objects.count()}
    baseline = json.loads((OUT / "C-supplement.json").read_text(encoding="utf-8"))["quantity_baseline_after_fixture"]
    assert result["quantities_after_ui"] == baseline, (result["quantities_after_ui"], baseline)
    assert not result["template_publications"]
    (OUT / "isolation-evidence.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, default=str))
