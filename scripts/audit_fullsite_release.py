"""Read-only comparison on an isolated production snapshot. Never repairs data.

SQLITE_PATH must point at the migrated copy. Baseline source is loaded from Git
into a separate module; both implementations read the exact same snapshot.
"""
import json
import os
import subprocess
import types
from datetime import date
from decimal import Decimal

from django.db import connection
from django.db.models import Count, F

from analytics import services
from orders.serializers import BusinessOrderSerializer
from production.models import ProductionDailyLog
from quality.models import QualityEmployee, QualityOrder, QualityReworkCase, QualityShipmentBatch
from quality.services import delivered_quantities_by_order, shipment_card_references

connection.ensure_connection()
connection.connection.execute("PRAGMA query_only=ON")
baseline = types.ModuleType("analytics.baseline_services")
source = subprocess.check_output(["git", "show", "14cb692:backend/analytics/services.py"], cwd="..", encoding="utf-8")
exec(compile(source, "baseline/analytics/services.py", "exec"), baseline.__dict__)
params = {"date_from": date(2026, 10, 1), "date_to": date(2026, 10, 13)}
before = baseline.build_dashboard(**params)
after = services.build_dashboard(**params)

def emit(name, value):
    print(name, json.dumps(value, ensure_ascii=False, default=str), flush=True)

for section, fields in (("quality", services.QUALITY_FIELDS), ("finance", services.FINANCE_FIELDS)):
    for origin in ("automatic", "manual", "total"):
        for field in fields:
            assert before[section][origin][field] == after[section][origin][field], (section, origin, field)
emit("UNCHANGED", {"quality_and_finance_fields": True, "factory_pieces": after["quality"]["total"]["shipped_quantity"], "attribution": after["quality_attribution"]})
emit("PRODUCTION", {"before": before["production"]["total"], "after": after["production"]["total"], "snapshot_cavity_differences": list(ProductionDailyLog.objects.exclude(cavities_snapshot=0).exclude(cavities_snapshot=F('run__cavities')).values('id', 'cavities_snapshot', 'run__cavities'))})
for person in QualityEmployee.objects.all():
    detail = services.build_quality_employee_details(**params, quality_employee_id=person.pk)
    row = next((item for item in after["quality_employee_performance"] if item["employee_id"] == person.pk), None)
    for field in ("responsible_return_quantity", "reworked_quantity"):
        assert sum(item[field] for item in detail["product_stats"]) == (row[field] if row else 0), (person.pk, field)
    assert sum(item["attributed_shipped_quantity"] for item in detail["product_stats"]) == (row["shipped_quantity"] if row else 0)
emit("EMPLOYEE_RECONCILIATION", {"employees": QualityEmployee.objects.count(), "passed": True})
for number in ("QS-20261006-7E5D607B", "QS-20261012-67FBA828"):
    batch = QualityShipmentBatch.objects.get(shipment_no=number)
    references = shipment_card_references(batch)
    allocations = [item for line in batch.lines.all() for item in line.order_allocations.all()]
    assert sum(item.piece_quantity for item in allocations) == sum(line.piece_quantity for line in batch.lines.all())
    emit("SOURCE", {"shipment": number, "cards": references, "allocation_quantity": sum(item.piece_quantity for item in allocations), "allocation_weight": sum((item.net_weight_kg for item in allocations), Decimal(0))})
old_ids = [109, 110]
balances = delivered_quantities_by_order(old_ids)
for order in QualityOrder.objects.filter(pk__in=old_ids):
    data = BusinessOrderSerializer(order, context={"delivered_quantities_by_order": balances}).data
    if order.production_required is False or balances.get(order.pk, 0) >= order.order_quantity:
        assert data["production_remaining_quantity"] == 0
    emit("SCHEDULE", {"order_id": order.pk, "status": data["production_requirement_status"], "production_remaining": data["production_remaining_quantity"], "historical_produced": data["produced_quantity"]})
case = QualityReworkCase.objects.get(pk=236)
found = []
for person in QualityEmployee.objects.all():
    detail = services.build_quality_employee_details(**params, quality_employee_id=person.pk)
    found += [item for item in detail["records"] if item["source_type"] == "REWORK_CASE" and item["source_no"] == case.case_no and item["event_type"] == "RETURN_RESPONSIBILITY"]
assert found and all(Decimal(item["source_weight_kg"]) == case.affected_weight_kg and item["net_weight_kg"] is None for item in found), found
emit("RETURN_WEIGHT", {"case_id": case.pk, "record_weight": case.affected_weight_kg, "detail_rows": len(found), "not_a_new_scale_reading": True})
emit("DEFECT_COVERAGE", list(ProductionDailyLog.objects.values("defective_quantity_recorded", "defective_quantity").annotate(records=Count("id"))))
emit("DONE", True)
if os.environ.get("QA_UI_FIXTURE") == "1":
    person_id = next(row["employee_id"] for row in after["quality_employee_performance"] if row["shipped_quantity"] > 0)
    emit("UI_FIXTURE", {"dashboard": after, "detail": services.build_quality_employee_details(**params, quality_employee_id=person_id), "orders": [BusinessOrderSerializer(order, context={"delivered_quantities_by_order": balances}).data for order in QualityOrder.objects.filter(pk__in=old_ids)]})
