"""Local synthetic 1x/5x benchmark; all writes go to Django's memory test DB.

From backend (PowerShell):
  $env:ERP_ANALYTICS_SCALE_BENCHMARK='1'
  python manage.py shell -c "exec(open('../scripts/benchmark_analytics_scale.py', encoding='utf-8').read())"

This mirrors record COUNTS, not the distribution of the private production DB.
It does not measure network, cameras, UI, concurrent saves or genuinely cold OS
caches. Never compare local timings directly to the production host's timings.
"""
import json
import math
import os
import platform
import statistics
import time
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection, reset_queries
from django.db.models import Sum
from django.test.runner import DiscoverRunner
from django.test.utils import CaptureQueriesContext, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from analytics.services import build_dashboard
from quality.models import (
    ProcessCard, ProcessCardUnitBinding, QualityEmployee, QualityOrder,
    QualityReturnAllocation, QualityReworkCase, QualityShipmentBatch,
    QualityShipmentLine, QualityShipmentOrderAllocation,
)
from quality.services import delivered_quantities_by_order
from quality.views import ProcessCardViewSet, QualityShipmentLedgerView, QualitySummaryView

DAY = date(2026, 10, 8)
SAMPLES = 100
BASE = {"orders": 162, "batches": 576, "lines": 581,
        "allocations": 610, "employees": 11, "return_cases": 210}


def emit(name, value):
    print(name, json.dumps(value, ensure_ascii=False, default=str), flush=True)


def populate(multiplier, user):
    """Append complete cohorts so the second stage is exactly five times base."""
    for cohort in range(multiplier):
        prefix = f"SCALE-{QualityOrder.objects.count()}-{cohort}"
        employees = QualityEmployee.objects.bulk_create([
            QualityEmployee(employee_no=f"{prefix}-E-{i}", name=f"Test {i}", role="BOTH")
            for i in range(BASE["employees"])
        ])
        orders = QualityOrder.objects.bulk_create([
            QualityOrder(
                order_no=f"{prefix}-O-{i}", item_no=str(i), product_name="Test ring",
                specification="240" if i % 12 == 0 else f"TEST-{i % 12}",
                material="N7200", order_quantity=10000, order_date=DAY, due_date=DAY,
                created_by=user,
            ) for i in range(BASE["orders"])
        ])
        batches = QualityShipmentBatch.objects.bulk_create([
            QualityShipmentBatch(
                shipment_no=f"{prefix}-S-{i}", status="CONFIRMED", shipment_date=DAY,
                inspector=employees[i % len(employees)], order=orders[i % len(orders)],
                created_by=user,
            ) for i in range(BASE["batches"])
        ])
        cards = ProcessCard.objects.bulk_create([
            ProcessCard(
                card_no=f"{prefix}-C-{i}", order=orders[(i % len(batches)) % len(orders)],
                quantity=1000, unit_weight_g=Decimal("1"), status="SHIPPED",
                created_by=user,
            ) for i in range(BASE["lines"])
        ])
        lines = QualityShipmentLine.objects.bulk_create([
            QualityShipmentLine(
                batch=batches[i % len(batches)], process_card=cards[i], order=cards[i].order,
                piece_quantity=1000, net_weight_kg=Decimal("1"),
                unit_weight_g_snapshot=Decimal("1"),
                specification_snapshot=cards[i].order.specification, material_snapshot="N7200",
            ) for i in range(BASE["lines"])
        ])
        ProcessCardUnitBinding.objects.bulk_create([
            ProcessCardUnitBinding(
                process_card=cards[i], shipment_batch=line.batch,
                shipment_unit_no=1 + i // len(batches), piece_quantity=1000,
                net_weight_kg=Decimal("1"), created_by=user,
            ) for i, line in enumerate(lines)
        ])
        shares = []
        for i, line in enumerate(lines):
            split = i < BASE["allocations"] - BASE["lines"]
            quantity = 600 if split else 1000
            shares.append(QualityShipmentOrderAllocation(
                shipment_line=line, order=line.order, sequence=1, piece_start=0,
                piece_end=quantity, piece_quantity=quantity,
                net_weight_kg=Decimal(quantity) / 1000,
                order_no_snapshot=line.order.order_no, item_no_snapshot=line.order.item_no,
                specification_snapshot=line.specification_snapshot, material_snapshot="N7200",
            ))
            if split:
                # The second item has exactly the same product/material.
                other = orders[(i + 12) % len(orders)]
                shares.append(QualityShipmentOrderAllocation(
                    shipment_line=line, order=other, sequence=2, piece_start=600,
                    piece_end=1000, piece_quantity=400, net_weight_kg=Decimal("0.4"),
                    order_no_snapshot=other.order_no, item_no_snapshot=other.item_no,
                    specification_snapshot=line.specification_snapshot, material_snapshot="N7200",
                ))
        shares = QualityShipmentOrderAllocation.objects.bulk_create(shares)
        cases = QualityReworkCase.objects.bulk_create([
            QualityReworkCase(
                case_no=f"{prefix}-R-{i}", shipment_line=line, shipment_batch=line.batch,
                process_card=line.process_card, affected_quantity=1000,
                affected_weight_kg=Decimal("1"), origin="CUSTOMER_RETURN",
                status="WAITING_REWORK", opened_on=DAY, responsible_inspector=line.batch.inspector,
                created_by=user,
            ) for i, line in enumerate(lines[:BASE["return_cases"]])
        ])
        cases_by_line = {case.shipment_line_id: case for case in cases}
        QualityReturnAllocation.objects.bulk_create([
            QualityReturnAllocation(
                case=cases_by_line[share.shipment_line_id], shipment_line=share.shipment_line,
                shipment_order_allocation=share, piece_quantity=share.piece_quantity,
                net_weight_kg=share.net_weight_kg,
            ) for share in shares if share.shipment_line_id in cases_by_line
        ])


def benchmark(stage, name, fn):
    reset_queries()
    started = time.perf_counter()
    with CaptureQueriesContext(connection) as captured:
        result = fn()
    first = (time.perf_counter() - started) * 1000
    samples = []
    for _ in range(SAMPLES):
        started = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - started) * 1000)
    emit("BENCH", {
        "stage": stage, "target": name, "samples": SAMPLES, "failures": 0,
        "query_count": len(captured), "first_ms": round(first, 2),
        "p50_ms": round(statistics.median(samples), 2),
        "p95_ms": round(sorted(samples)[math.ceil(.95 * SAMPLES) - 1], 2),
        "max_ms": round(max(samples), 2),
        "payload_json_bytes": len(json.dumps(result, ensure_ascii=False, default=str).encode()),
    })


@override_settings(DEBUG=False, ALLOWED_HOSTS=["testserver"])
def run():
    if os.environ.get("ERP_ANALYTICS_SCALE_BENCHMARK") != "1":
        raise RuntimeError("Explicit local benchmark opt-in is required")
    if connection.vendor != "sqlite":
        raise RuntimeError("This local-only benchmark requires SQLite")
    connection.settings_dict.setdefault("TEST", {})["NAME"] = ":memory:"
    runner = DiscoverRunner(verbosity=0, interactive=False)
    old_config = runner.setup_databases()
    try:
        if "memory" not in str(connection.settings_dict["NAME"]):
            raise RuntimeError("Refusing fixture writes outside the memory test database")
        user = get_user_model().objects.create_user(username="scale-test", is_superuser=True)
        factory = APIRequestFactory()
        def invoke(view, path, params):
            request = factory.get(path, params)
            force_authenticate(request, user=user)
            response = view(request)
            if response.status_code != 200:
                raise AssertionError(f"{path}: {response.status_code} {response.data}")
            response.render()
            return response.data

        scan_view = ProcessCardViewSet.as_view({"get": "scan"})
        params = {"date_from": "2026-10-01", "date_to": DAY.isoformat()}
        emit("ENV", {"platform": platform.platform(), "python": platform.python_version(),
                     "database": "isolated in-memory SQLite", "distribution": "synthetic"})
        for stage, append in ((1, 1), (5, 4)):
            populate(append, user)
            models = {"orders": QualityOrder, "batches": QualityShipmentBatch,
                      "lines": QualityShipmentLine, "allocations": QualityShipmentOrderAllocation,
                      "employees": QualityEmployee, "return_cases": QualityReworkCase}
            counts = {name: model.objects.count() for name, model in models.items()}
            assert counts == {name: count * stage for name, count in BASE.items()}, counts
            emit("SCALE", {"stage": stage, **counts})
            cards = list(ProcessCard.objects.order_by("-id").values_list("card_no", flat=True)[:50])
            def scan(code):
                return invoke(scan_view, "/api/quality/process-cards/scan/", {"code": code})
            targets = {
                "analytics_service": lambda: build_dashboard(date_from=date(2026, 10, 1), date_to=DAY),
                "quality_summary": lambda: invoke(QualitySummaryView.as_view(), "/api/quality/summary/", params),
                "single_card_scan": lambda: scan(cards[0]),
                "shipment_search": lambda: invoke(QualityShipmentLedgerView.as_view(),
                    "/api/quality/shipment-ledger/", dict(params, specification="240", material="N7200", page_size=20, compact=True)),
            }
            for name, fn in targets.items():
                benchmark(stage, name, fn)
            dashboard, summary = targets["analytics_service"](), targets["quality_summary"]()
            physical = QualityShipmentLine.objects.aggregate(total=Sum("piece_quantity"))["total"]
            assert dashboard["quality"]["total"]["shipped_quantity"] == physical
            assert summary["totals"]["shipped_quantity"] == physical
            balances = delivered_quantities_by_order(QualityOrder.objects.values_list("id", flat=True))
            assert sum(balances.values()) == (BASE["lines"] - BASE["return_cases"]) * stage * 1000
            emit("QUANTITY", {"stage": stage, "physical": physical, "net_delivery": sum(balances.values())})
            for size in (1, 20, 50):
                started = time.perf_counter()
                for code in cards[:size]:
                    assert scan(code)["found"] is True
                assert scan(cards[0])["found"] is True  # duplicate lookup, no write
                assert scan("SCALE-NOT-FOUND")["found"] is False
                emit("SCAN_SEQUENCE", {"stage": stage, "valid": size, "duplicate": 1,
                    "missing": 1, "failures": 0, "api_only_total_ms": round((time.perf_counter() - started) * 1000, 2)})
        emit("DONE", True)
    finally:
        runner.teardown_databases(old_config)


run()
