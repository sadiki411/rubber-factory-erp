"""Fresh synthetic-only UI pilot fixture. No cards/events/shipments are prefilled.

Refuses an existing database and never reads the production database. Credentials
stay in .codex_tmp, not in the review package. UI performs the closed loop.
"""
import json
import os
import secrets
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / ".codex_tmp/flow-pilot-20261119.sqlite3"
if DB.exists():
    raise SystemExit("Refusing existing synthetic database")
DB.parent.mkdir(parents=True, exist_ok=True)
os.environ.update(SQLITE_PATH=str(DB), FLOW_CARDS_WRITE_ENABLED="1", DJANGO_SETTINGS_MODULE="erp.settings",
                  FLOW_CARDS_PRIVATE_ROOT=str(DB.with_suffix(".private")))
sys.path.insert(0, str(ROOT / "backend"))
import django
django.setup()
from django.core.management import call_command
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from orders.models import ProductSpecification
from quality.models import QualityEmployee, QualityOrder, ProcessCard, ProductUnitWeight
from flowcards import services as s
from flowcards.models import AppearanceIssue

call_command("migrate", interactive=False, verbosity=0)
password = secrets.token_urlsafe(24)
user = get_user_model().objects.create_user("pilot-worker", password=password)
reader = get_user_model().objects.create_user("pilot-reader", password=secrets.token_urlsafe(24))
reader.groups.add(Group.objects.get_or_create(name="GPT只读测试")[0])
employee = QualityEmployee.objects.create(employee_no="PILOT-01", name="隔离作业员", role="BOTH")
inspector = QualityEmployee.objects.create(employee_no="PILOT-02", name="隔离品检员", role="BOTH")
appearance = list(AppearanceIssue.objects.values_list("pk", flat=True))[:4]
evidence = {"synthetic_only": True, "seed_has_no_internal_cards_events_or_shipments": True,
            "employee_id": employee.pk, "inspector_id": inspector.pk, "suites": []}
for tag, spec in [("SCAN", "PILOT-SCAN-100x3"), ("MANUAL", "PILOT-MANUAL-100x3")]:
    product = ProductSpecification.objects.create(specification=spec, material="DEMO-ONLY", customer_product_no=tag,
                                                  product_name="隔离合成O圈，非客户参数")
    orders = [QualityOrder.objects.create(order_no=f"SYNTH-{tag}-{i}", item_no=f"0{i}", specification=spec, material=product.material,
        product_specification=product, product_name=product.product_name, source_system="翔邦", order_quantity=qty,
        due_date=f"2026-11-{20+i:02}", created_by=user) for i, qty in [(1, 60), (2, 40)]]
    weight = ProductUnitWeight.objects.create(product_specification=product,
        sample_count=100, sample_total_weight_g="100", unit_weight_g="1", measured_on="2026-11-19", created_by=user)
    customer_card = ProcessCard.objects.create(card_no=f"SYNTH-{tag}-CUSTOMER-001", order=orders[0], source_item_no="01",
        source_order_no=orders[0].order_no, product_specification=product, specification_snapshot=spec, material_snapshot=product.material,
        quantity=100, unit_weight_g="1", sample_count_snapshot=100, sample_total_weight_g_snapshot="100", unit_weight_config=weight,
        qr_text=f"SYNTH-{tag}-CUSTOMER-001", created_by=user)
    st = s.confirm_standard({"request_key": uuid.uuid4().hex, "product_id": product.pk, "source": "合成隔离已确认示例，不是客户公差",
        "appearance_ids": appearance, "dimension_count": 1, "whole_mold": "NOT_REQUIRED",
        "criteria": [{"name": "内径（明确不要求测）", "stage": "DIMENSION", "limit_mode": "NONE", "unit": "mm", "method": "不适用"},
                     {"name": "线径", "stage": "DIMENSION", "limit_mode": "UPPER", "upper": "3.05", "unit": "mm", "method": "隔离批准示例测法"},
                     {"name": "烤后硬度", "stage": "BAKED_HARDNESS", "limit_mode": "RANGE", "lower": "60", "upper": "75", "unit": "示例量表", "method": "隔离硬度测法"}],
        "bake": {"state": "APPLICABLE", "temperature_lower": 150, "temperature_upper": 160, "minutes_lower": 120, "minutes_upper": 150},
        "hardness": {"BAKED_HARDNESS": {"object": "COUPON", "scale": "示例量表"}}}, user)
    evidence["suites"].append({"tag": tag, "product_id": product.pk, "orders": [{"id": o.pk, "order_no": o.order_no, "item_no": o.item_no, "quantity": o.order_quantity} for o in orders],
        "customer_card_id": customer_card.pk, "customer_card_no": customer_card.card_no, "standard_id": st.pk})
DB.with_suffix(".credentials.json").write_text(json.dumps({"username": user.username, "password": password}), encoding="utf-8")
DB.with_suffix(".json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(evidence, ensure_ascii=False, indent=2))
