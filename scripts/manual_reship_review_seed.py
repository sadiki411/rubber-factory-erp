"""New synthetic-only employee UI fixture for the narrow no-scan reship fix.

No prefilled shipment, return, source binding, reshipment or stock. Refuse any
existing path; all credentials stay in ignored .codex_tmp only.
"""
import json
import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
run = sys.argv[1] if len(sys.argv) > 1 else ''
if run not in ('', '-verified', '-recording'):
    raise SystemExit('Unsupported isolated review run')
DB = ROOT / f".codex_tmp/manual-reship-20261119{run}.sqlite3"
if DB.exists():
    raise SystemExit("Refuse existing database; retain previous evidence")
DB.parent.mkdir(exist_ok=True)
os.environ.update(SQLITE_PATH=str(DB), DJANGO_SETTINGS_MODULE="erp.settings",
                  FLOW_CARDS_WRITE_ENABLED="0", FLOW_CARDS_PRIVATE_ROOT=str(DB.with_suffix(".private")))
sys.path.insert(0, str(ROOT / "backend"))
import django
django.setup()
from django.core.management import call_command
from django.contrib.auth import get_user_model
from orders.models import ProductSpecification
from quality.models import QualityEmployee, QualityOrder, ProcessCard, ProductUnitWeight

call_command("migrate", interactive=False, verbosity=0)
password = secrets.token_urlsafe(24)
worker = get_user_model().objects.create_user("reship-worker", password=password)
staff = [QualityEmployee.objects.create(employee_no=f"SYNTH-QC-0{i}", name=f"合成品检员{i}", role="BOTH") for i in (1, 2)]
evidence = {"synthetic_only": True, "initial_shipments": 0, "initial_returns": 0,
            "ordinary_employee": {"is_staff": worker.is_staff, "is_superuser": worker.is_superuser},
            "employees": [{"id": e.pk, "employee_no": e.employee_no, "name": e.name} for e in staff], "suites": []}
for tag in ("SCAN", "MANUAL"):
    spec = f"SYNTH-{tag}-100"
    product = ProductSpecification.objects.create(specification=spec, material="DEMO-ONLY", product_name="全新隔离演练")
    orders = [QualityOrder.objects.create(order_no=f"SYNTH-{tag}-ORDER-{i}", item_no=f"0{i}",
        specification=spec, material="DEMO-ONLY", product_name=product.product_name, product_specification=product,
        source_system="翔邦", order_quantity=quantity, due_date=f"2026-11-{20+i:02}", created_by=worker)
        for i, quantity in ((1, 60), (2, 40))]
    weight = ProductUnitWeight.objects.create(product_specification=product, sample_count=100, sample_total_weight_g=100,
        unit_weight_g=1, measured_on="2026-11-19", created_by=worker)
    card = ProcessCard.objects.create(card_no=f"SYNTH-{tag}-CARD-001", qr_text=f"SYNTH-{tag}-CARD-001",
        order=orders[0], product_specification=product, quantity=100, unit_weight_g=1, unit_weight_config=weight,
        specification_snapshot=spec, material_snapshot="DEMO-ONLY", source_order_no=orders[0].order_no,
        source_item_no="01", sample_count_snapshot=100, sample_total_weight_g_snapshot=100, created_by=worker)
    evidence["suites"].append({"tag": tag, "product": spec, "material": "DEMO-ONLY", "product_id": product.pk,
        "customer_card": card.card_no, "customer_card_id": card.pk,
        "orders": [{"id": o.pk, "order_no": o.order_no, "item_no": o.item_no, "quantity": o.order_quantity} for o in orders]})
DB.with_suffix(".credentials.json").write_text(json.dumps({"username": worker.username, "password": password}), encoding="utf-8")
DB.with_suffix(".seed.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(evidence, ensure_ascii=False, indent=2))
