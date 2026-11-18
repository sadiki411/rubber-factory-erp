"""Disposable synthetic acceptance data and service-generated PDFs. Never opens an existing DB."""
import argparse
import hashlib
import json
import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--database", default=str(ROOT / ".codex_tmp/flow-review-rv2.sqlite3"))
args = parser.parse_args()
db = Path(args.database).resolve()
if db.exists() or db.suffix != ".sqlite3" or db.parent != (ROOT / ".codex_tmp").resolve():
    raise SystemExit("Refusing existing/non-disposable database: use a new .codex_tmp/*.sqlite3")
db.parent.mkdir(parents=True, exist_ok=True)
os.environ.update(SQLITE_PATH=str(db), FLOW_CARDS_WRITE_ENABLED="1", DJANGO_SETTINGS_MODULE="erp.settings",
                  FLOW_CARDS_PRIVATE_ROOT=str(db.with_suffix(".private")))
sys.path.insert(0, str(ROOT / "backend"))
import django
django.setup()
from django.core.management import call_command
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient
from orders.models import ProductSpecification
from quality.models import QualityOrder, QualityEmployee
from flowcards import services as s
from flowcards.models import AppearanceIssue

call_command("migrate", interactive=False, verbosity=0)
user = get_user_model().objects.create_user("flow-review", password="FlowReview-Local-Only")
employee = QualityEmployee.objects.create(employee_no="RV-01", name="隔离作业员", role="BOTH")
client = APIClient(HTTP_HOST="localhost"); client.force_authenticate(user)
appearance = list(AppearanceIssue.objects.values_list("pk", flat=True))[:4]
output = ROOT / "output/pdf"; output.mkdir(parents=True, exist_ok=True)
evidence = {"synthetic_only": True, "database": str(db), "employee_id": employee.pk, "samples": []}

def criteria():
    return [{"name": name, "stage": stage, "lower": lower, "upper": upper, "unit": unit, "method": "隔离示例方法，不作客户批准要求", "numeric": True}
            for name, stage, lower, upper, unit in [("上模温度", "UPPER_TEMP", "170", "180", "℃"), ("下模温度", "LOWER_TEMP", "168", "178", "℃"),
            ("成型硬度", "MOLDING_HARDNESS", "65", "75", "示例量表"), ("烤后硬度", "BAKED_HARDNESS", "66", "74", "示例量表"), ("内径", "DIMENSION", "21", "21.3", "mm")]]

for index, (tag, whole, full, long) in enumerate([
    ("完整要求_需整模", "REQUIRED", True, False), ("完整要求_不需整模", "NOT_REQUIRED", True, False),
    ("缺要求_待确认", "REQUIRED", False, False), ("长字段_续页", "REQUIRED", True, True)], 1):
    product = ProductSpecification.objects.create(specification=("隔离长字段不裁切-" * 18 if long else f"RV-DEMO-{index}"), material="隔离示例料", product_name="合成样张非正式参数")
    order = QualityOrder.objects.create(order_no=f"RV-DEMO-ORDER-{index}", item_no="01", product_specification=product,
        specification=product.specification, material=product.material, product_name="隔离示例", source_system="翔邦", order_quantity=20000, created_by=user)
    rows = criteria() if full else []
    if long:
        rows += [{"name": f"附加尺寸{n}", "stage": "DIMENSION", "lower": "1", "upper": "2", "unit": "mm", "method": "隔离确认量具", "numeric": True} for n in range(1,5)]
    standard = s.confirm_standard({"request_key": uuid.uuid4().hex, "product_id": product.pk,
        "source": "隔离合成确认，仅验证要求→快照→PDF链；不代表真实产品标准", "appearance_ids": appearance,
        "whole_mold": whole, "dimension_count": (5 if long else 1) if full else None, "criteria": rows,
        "bake": {"state": "APPLICABLE", "temperature_lower": 150, "temperature_upper": 160, "minutes_lower": 120, "minutes_upper": 150} if full else {"state": "UNKNOWN"},
        "hardness": {stage: {"object": "PRODUCT" if stage == "MOLDING_HARDNESS" else "COUPON", "scale": "示例量表" if full else "", "instrument": "手持，隔离例非实际型号"} for stage in s.HARDNESS_STAGES}}, user)
    group = s.generate_group(s.create_group({"request_key": uuid.uuid4().hex, "product_id": product.pk, "order_ids": [order.pk],
        "planned_molds": 2050 if full and not long else 100, "molds_per_card": 100, "whole_mold": whole, "whole_mold_basis": "隔离场景确认",
        "plan_basis": "隔离0至2050模，不记正式产量", "standard_id": standard.pk, "append_confirmed": True}, user), user)
    cards = list(group.cards.all()); chosen = [cards[0], cards[2], cards[-1]] if len(cards)>3 else cards
    # Only the tail is a same-identity reprint, recorded by the real confirmation service.
    if len(cards)>3:
        first = s.create_print({"request_key": uuid.uuid4().hex, "card_ids": [str(cards[-1].pk)]}, user)
        s.confirm_print(first, {"outcomes": {str(cards[-1].pk): "SUCCESS"}}, user)
    body = {"request_key": uuid.uuid4().hex, "card_ids": [str(c.pk) for c in chosen]}
    response = client.post("/api/flow-cards/print-jobs/", body, format="json")
    if response.status_code != 201: raise RuntimeError(response.content)
    job = response.json()
    response = client.get(job["pdf_url"])
    raw = b"".join(response.streaming_content); response.close()
    path = output / f"工序流转卡_RV_{tag}.pdf"; path.write_bytes(raw)
    evidence["samples"].append({"tag": tag, "product_id": product.pk, "order_id": order.pk, "standard_id": standard.pk, "group_id": str(group.pk),
        "cards": [{"id": str(c.pk), "card_no": c.card_no, "sequence": c.sequence, "molds": c.planned_molds} for c in chosen],
        "print_job_id": job["id"], "pdf": str(path), "sha256": hashlib.sha256(raw).hexdigest()})
    print(path)

product = ProductSpecification.objects.create(specification="RV-浏览器操作", material="ISOLATED", product_name="隔离浏览器路径")
order = QualityOrder.objects.create(order_no="RV-BROWSER-ORDER", item_no="01", product_specification=product, specification=product.specification,
    material=product.material, product_name=product.product_name, order_quantity=1000, source_system="翔邦", created_by=user)
evidence["browser"] = {"product_id": product.pk, "order_id": order.pk}
path = db.with_suffix(".json"); path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
print("Synthetic evidence:", path)
