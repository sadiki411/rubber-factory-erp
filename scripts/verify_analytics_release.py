"""Read-only release audit and 100-sample server benchmark.

Run against the intended database, never creates test business events:
  cd backend
  python manage.py shell -c "exec(open('../scripts/verify_analytics_release.py', encoding='utf-8').read())"
API timings include response rendering, but exclude network, login and camera/UI.
"""
from datetime import date
import json
import math
import statistics
import time

from django.contrib.auth import get_user_model
from django.db import connection, reset_queries
from django.db.models import Sum
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIRequestFactory, force_authenticate

from analytics.services import build_dashboard
from quality.models import (
    QualityEmployee, QualityOrder, QualityReworkCase, QualityShipment,
    QualityShipmentBatch, QualityShipmentLine, QualityShipmentOrderAllocation,
)
from quality.services import order_delivery_totals, shipment_inspectors
from quality.views import ProcessCardViewSet, QualityShipmentLedgerView, QualitySummaryView

DATE_FROM = date(2026, 10, 1)
DATE_TO = date(2026, 10, 8)
SAMPLES = 100
factory = APIRequestFactory()
user = get_user_model().objects.filter(is_active=True, is_superuser=True).first()
if user is None:
    raise RuntimeError("An existing active administrator is required for read-only inspection")


def invoke(view, path, params):
    request = factory.get(path, params)
    force_authenticate(request, user=user)
    response = view(request)
    if response.status_code != 200:
        raise RuntimeError(f"{path}: HTTP {response.status_code}")
    response.render()
    return response.data


def emit(name, value):
    print(name, json.dumps(value, ensure_ascii=False, default=str, indent=2), flush=True)


params = {"date_from": DATE_FROM.isoformat(), "date_to": DATE_TO.isoformat()}
targets = {
    "analytics_service": lambda: build_dashboard(date_from=DATE_FROM, date_to=DATE_TO),
    "quality_summary": lambda: invoke(QualitySummaryView.as_view(), "/api/quality/summary/", params),
    "single_card_scan": lambda: invoke(ProcessCardViewSet.as_view({"get": "scan"}), "/api/quality/process-cards/scan/", {"code": "04-M003-2607080061"}),
    "shipment_search": lambda: invoke(QualityShipmentLedgerView.as_view(), "/api/quality/shipment-ledger/", dict(params, specification="240", material="N7200", page_size=20, compact=True)),
}

emit("SCALE", {
    "orders": QualityOrder.objects.count(), "batches": QualityShipmentBatch.objects.count(),
    "lines": QualityShipmentLine.objects.count(), "allocations": QualityShipmentOrderAllocation.objects.count(),
    "employees": QualityEmployee.objects.count(), "return_cases": QualityReworkCase.objects.count(),
    "confirmed_pieces_all_time": QualityShipmentLine.objects.filter(batch__status="CONFIRMED").aggregate(total=Sum("piece_quantity"))["total"],
})
for name, fn in targets.items():
    reset_queries()
    start = time.perf_counter()
    with CaptureQueriesContext(connection) as ctx:
        result = fn()
    cold = (time.perf_counter() - start) * 1000
    samples = []
    for _ in range(SAMPLES):
        start = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - start) * 1000)
    ordered = sorted(samples)
    emit("BENCH", {
        "target": name, "query_count": len(ctx), "samples": SAMPLES,
        "cold_ms": round(cold, 2), "p50_ms": round(statistics.median(samples), 2),
        "p95_ms": round(ordered[math.ceil(.95 * SAMPLES) - 1], 2), "max_ms": round(max(samples), 2),
        "payload_json_bytes": len(json.dumps(result, ensure_ascii=False, default=str).encode("utf-8")),
    })

dashboard = targets["analytics_service"]()
summary = targets["quality_summary"]()
emit("PERIOD", {"date_from": DATE_FROM, "date_to": DATE_TO, "factory": dashboard["quality"]["total"]["shipped_quantity"], "employee_total": sum(row["shipped_quantity"] for row in dashboard["quality_employee_performance"]), "attribution": dashboard.get("quality_attribution"), "quality_factory": summary["totals"]["shipped_quantity"]})

unassigned = []
for batch in QualityShipmentBatch.objects.filter(status="CONFIRMED", shipment_date__range=(DATE_FROM, DATE_TO)).select_related("inspector").prefetch_related("inspectors", "lines__order", "lines__order_allocations__order"):
    if shipment_inspectors(batch):
        continue
    lines = list(batch.lines.all())
    unassigned.append({
        "type": "WEIGHTED", "id": batch.pk, "shipment_no": batch.shipment_no,
        "date": batch.shipment_date, "quantity": sum(int(line.piece_quantity or 0) for line in lines),
        "products": sorted(set(f"{line.specification_snapshot}/{line.material_snapshot}" for line in lines)),
        "order_items": sorted(set(f"{allocation.order.order_no}/{allocation.order.item_no}" for line in lines for allocation in line.order_allocations.all())),
    })
for shipment in QualityShipment.objects.filter(shipment_date__range=(DATE_FROM, DATE_TO)).select_related("inspector", "order").prefetch_related("inspectors"):
    if not shipment_inspectors(shipment):
        unassigned.append({"type": "LEGACY", "id": shipment.pk, "shipment_no": shipment.shipment_no, "date": shipment.shipment_date, "quantity": shipment.shipped_quantity})
emit("UNASSIGNED", {"count": len(unassigned), "quantity": sum(row["quantity"] for row in unassigned), "records": unassigned})

sample = QualityShipmentBatch.objects.filter(shipment_no="QS-20261006-7E5D607B").prefetch_related("lines__order_allocations__order").first()
if sample:
    allocations = [allocation for line in sample.lines.all() for allocation in line.order_allocations.all()]
    ids = {allocation.order_id for allocation in allocations}
    emit("SAMPLE", {"batch_id": sample.pk, "physical_quantity": sum(line.piece_quantity for line in sample.lines.all()), "allocations": [{"order_id": a.order_id, "item_no": a.order.item_no, "quantity": a.piece_quantity, "weight_kg": a.net_weight_kg} for a in allocations], "order_balances": order_delivery_totals(ids), "quality_orders": [row for row in summary["order_stats"] if row["order_id"] in ids], "analytics_orders": [row for row in dashboard["order_performance"] if row["order_id"] in ids]})
emit("DONE", True)
