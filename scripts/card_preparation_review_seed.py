"""Create NEW synthetic-only SQLite for the preparation review. Never production."""
import json
import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
db = Path(os.environ["SQLITE_PATH"]).resolve()
allowed = (ROOT / ".codex_tmp").resolve()
if db.parent != allowed or db.exists() or db.suffix != ".sqlite3":
    raise SystemExit("Refusing existing/non-disposable DB. Use a new .codex_tmp/*.sqlite3")
db.parent.mkdir(exist_ok=True)
os.environ.update(DJANGO_SETTINGS_MODULE="erp.settings", FLOW_CARDS_WRITE_ENABLED="1", FLOW_CARDS_PREPARATION_ENABLED="1")
sys.path.insert(0, str(ROOT / "backend"))
import django
django.setup()
from django.core.management import call_command
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.utils import timezone
from orders.models import ProductSpecification
from quality.models import QualityOrder
from production.models import ProductionRun, ProductionRunOrder
from flowcards.models import AppearanceIssue
from flowcards import services as s, preparation as p

call_command("migrate", interactive=False, verbosity=0)
operator = get_user_model().objects.create_user(username="synthetic-operator", password=os.environ["ERP_ISOLATED_TEST_PASSWORD"])
maintainer = get_user_model().objects.create_user(username="synthetic-standard", password=os.environ["ERP_ISOLATED_TEST_PASSWORD"])
maintainer.user_permissions.add(Permission.objects.get(codename="maintain_product_standard"))
evidence = {"synthetic_only": True, "generated_at": timezone.now().isoformat(), "orders": [], "official_records_used": False}
for n, customer in enumerate(("翔邦", "艾斯霖")):
    product = ProductSpecification.objects.create(product_name=f"隔离演示{customer}，非正式标准", customer_product_no=f"SYNTH-PRODUCT-{n}", specification=f"SYNTH-{n}-230x3.55", material="SYNTH-RUBBER",
        primary_curing="时间50-60min，原单位待核", secondary_curing="温度150℃，时间120min（仅合成表达）", effective_cavities="16", total_cavities="16",
        large_strip_weight_g="21", large_strip_count=5, small_strip_weight_g="11", small_strip_count=2)
    order = QualityOrder.objects.create(order_no=f"SYNTH-PREP-20261119-{n}", item_no="1", specification=product.specification, material=product.material,
        product_specification=product, order_quantity=3208, production_required=True, source_system=customer, created_by=operator)
    p.sync_order_suggestion(order, operator)
    evidence["orders"].append({"id": order.pk, "product_id": product.pk, "order_no": order.order_no, "customer": customer})
    if n == 0:
        standard = s.confirm_standard({"request_key": uuid.uuid4().hex, "product_id": product.pk, "customer_key": customer, "source_kind": "FACTORY_PROCESS",
            "source": "全新合成标准，非实际产品依据", "whole_mold": "NOT_REQUIRED", "dimension_count": 1, "appearance_ids": list(AppearanceIssue.objects.values_list("id", flat=True)),
            "route": {"confirmed": True, "molding": True, "patrol": True, "deburr": True, "bake": False, "baked_hardness": False, "inspection": True},
            "packaging": {"molds_per_card": 100, "patrol_every_molds": 50}, "bake": {"state": "NOT_APPLICABLE"},
            "criteria": [{"name": "线径", "lower": "", "upper": "3.60", "unit": "mm", "method": "合成量具", "stage": "DIMENSION", "limit_mode": "UPPER", "numeric": True}],
            "process_parameters": {"upper_set_temperature": {"state": "CONFIRMED", "lower": "180", "upper": "190", "unit": "℃"},
                "lower_set_temperature": {"state": "CONFIRMED", "lower": "180", "upper": "190", "unit": "℃"},
                "upper_contact_temperature": {"state": "CONFIRMED", "lower": "178", "upper": "183", "unit": "℃"},
                "lower_contact_temperature": {"state": "CONFIRMED", "lower": "178", "upper": "183", "unit": "℃"}}}, maintainer)
        p.activate_standard(standard, maintainer)
        run = ProductionRun.objects.create(order=order, product_specification=product, order_no=order.order_no, specification=product.specification,
            material=product.material, order_quantity=3208, cavities=16, planned_mold_count=201, is_ledger_only=True, created_by=operator)
        for index in range(2):
            extra = QualityOrder.objects.create(order_no=f"SYNTH-COMMON-{index}", item_no="1", specification=product.specification, material=product.material,
                product_specification=product, order_quantity=100, source_system=customer, created_by=operator)
            ProductionRunOrder.objects.create(run=run, order=extra, planned_quantity=100)
        evidence["orders"][-1].update(run_id=run.pk, standard_id=standard.pk)
out = db.with_suffix(".seed.json")
out.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(evidence, ensure_ascii=False))
