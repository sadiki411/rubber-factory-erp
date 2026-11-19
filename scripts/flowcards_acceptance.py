"""Reproducible synthetic migration/performance proof. Refuses non-isolated DBs."""
import argparse
import hashlib
import json
import os
import sqlite3
import statistics
import sys
import time
import uuid
from pathlib import Path

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--database", required=True)
parser.add_argument("--report", required=True)
args = parser.parse_args()
db = Path(args.database).resolve()
if not db.is_relative_to((root / ".codex_tmp").resolve()) or db.exists() or db.suffix != ".sqlite3":
    raise SystemExit("Use a NEW .sqlite3 inside .codex_tmp only; no real database is accepted.")
db.parent.mkdir(parents=True, exist_ok=True)
os.environ.update(SQLITE_PATH=str(db), DJANGO_SETTINGS_MODULE="erp.settings", FLOW_CARDS_WRITE_ENABLED="1", DJANGO_ALLOWED_HOSTS="testserver,localhost")
sys.path.insert(0, str(root / "backend"))
import django
django.setup()
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.contrib.auth import get_user_model
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient
from quality.models import QualityOrder
from orders.models import ProductSpecification
from production.models import ProductionRun, ProductionDailyLog


def fingerprint():
    result = {}
    with connection.cursor() as cursor:
        for table in sorted(connection.introspection.table_names()):
            if not table.startswith(("orders_", "production_", "quality_", "molds_", "inventory_")):
                continue
            cursor.execute(f'SELECT * FROM "{table}" ORDER BY rowid')
            rows = cursor.fetchall()
            result[table] = {"count": len(rows), "sha256": hashlib.sha256(json.dumps(rows, default=str, ensure_ascii=False).encode()).hexdigest()}
    return result


executor = MigrationExecutor(connection)
executor.migrate([leaf for leaf in executor.loader.graph.leaf_nodes() if leaf[0] != "flowcards"])
user = get_user_model().objects.create_user(username="synthetic-flow-review")
product = ProductSpecification.objects.create(product_name="隔离示例", specification="215", material="N7200")
orders = [QualityOrder.objects.create(order_no=f"ISOLATED-{index}", item_no="1", product_specification=product, specification="215", material="N7200", source_system="翔邦", order_quantity=100000, created_by=user) for index in [1, 2]]
run = ProductionRun.objects.create(order=orders[0], product_specification=product, order_no="ISOLATED-MERGED", specification="215", material="N7200", cavities=396, planned_mold_count=2000, order_quantity=200000, status="COMPLETED", is_ledger_only=True, created_by=user)
ProductionDailyLog.objects.create(run=run, produced_mold_count=100, cavities_snapshot=396)
before = fingerprint()
start = time.perf_counter()
executor = MigrationExecutor(connection)
executor.migrate(executor.loader.graph.leaf_nodes())
migration_ms = (time.perf_counter()-start)*1000
after = fingerprint()
assert before == after, "A base business table changed during the new migration"
client = APIClient(); client.force_authenticate(user)
from flowcards import services as s
from flowcards.models import InternalCard


def measured(label, work, iterations=20):
    durations, sql, sizes, errors = [], [], [], 0
    for _ in range(iterations):
        start = time.perf_counter()
        with CaptureQueriesContext(connection) as captured:
            response = work()
        durations.append((time.perf_counter()-start)*1000)
        sql.append(len(captured))
        sizes.append(len(response.content))
        errors += int(response.status_code >= 400)
    durations.sort()
    return {"label": label, "samples": iterations, "p50_ms": round(statistics.median(durations), 3), "p95_ms": round(durations[max(0, int(len(durations)*.95)-1)], 3), "max_sql": max(sql), "max_bytes": max(sizes), "errors": errors}


timings = []
group_results = []
for count in [20, 100]:
    data = {"request_key": uuid.uuid4().hex, "product_id": product.pk, "order_ids": [o.pk for o in orders],
            "planned_molds": count*100, "molds_per_card": 100, "whole_mold": "REQUIRED", "whole_mold_basis": "隔离示例确认", "plan_basis": "隔离制卡计划", "append_confirmed": True}
    start = time.perf_counter()
    with CaptureQueriesContext(connection) as captured:
        group = s.generate_group(s.create_group(data, user), user)
    group_results.append({"cards": count, "elapsed_ms": round((time.perf_counter()-start)*1000, 3), "sql": len(captured), "molds": sum(group.cards.values_list("planned_molds", flat=True))})
card = InternalCard.objects.first()
timings.append(measured("internal scan, 120 cards, no history", lambda: client.get("/api/flow-cards/cards/scan/", {"code": f"ERPFC:{card.pk}"})))
for index in range(100):
    s.record_event({"request_key": uuid.uuid4().hex, "card_ids": [str(card.pk)], "kind": "WHOLE_MOLD", "scope": f"隔离范围{index}", "evidence": "隔离原记录", "values": {"status": "PARTIAL", "fixed_positions": index % 5}}, user)
timings.append(measured("internal scan, same card +100 events", lambda: client.get("/api/flow-cards/cards/scan/", {"code": f"ERPFC:{card.pk}"})))
timings.append(measured("card detail, +100 events", lambda: client.get(f"/api/flow-cards/cards/{card.pk}/")))
timings.append(measured("card events, paginated 20", lambda: client.get(f"/api/flow-cards/cards/{card.pk}/events/")))
timings.append(measured("100 cards, shared snapshot not copied per card", lambda: client.get(f"/api/flow-cards/groups/{group.pk}/cards/", {"page_size": 100})))
quantity_unchanged = before == fingerprint()
assert quantity_unchanged, "Creating cards/events changed base business tables"
from quality.models import QualityShipmentBatch, QualityShipmentLine
binding_performance = []
for count in [1, 100]:
    # Pre-existing confirmed posting is a synthetic fixture, not a new posting
    # created by binding. Compare legacy state immediately around binding.
    batch = QualityShipmentBatch.objects.create(status="CONFIRMED", created_by=user)
    QualityShipmentLine.objects.create(batch=batch, order=orders[0], product_specification=product,
        piece_quantity=100, net_weight_kg="1.000", unit_weight_g_snapshot="10.00000")
    selected = list(InternalCard.objects.order_by("card_no")[:count])
    payload = [{"card_id": str(row.pk), "request_key": uuid.uuid4().hex, "evidence": "隔离明确来源集合，未知逐包份额"} for row in selected]
    old = fingerprint()
    start = time.perf_counter()
    with CaptureQueriesContext(connection) as captured:
        sources = s.bind_sources(batch, payload, user)
    elapsed = (time.perf_counter() - start) * 1000
    assert old == fingerprint(), "Binding sources changed legacy quantities"
    binding_performance.append({"cards": count, "elapsed_ms": round(elapsed, 3), "sql": len(captured), "sources": len(sources), "legacy_fingerprint_equal": True})
report = {"environment": {"python": sys.version.split()[0], "database": "isolated SQLite on local Windows", "transport": "Django APIClient, no HTTP/TLS/camera/mobile", "baseline": "baced3ad26519b934a8c7f3b9d0830d45540df0d"},
    "migration_ms": round(migration_ms, 3), "migration_legacy_fingerprint_equal": before == after,
    "card_operations_legacy_fingerprint_equal": quantity_unchanged, "legacy_tables": before,
    "generated": group_results, "performance": timings, "binding_performance": binding_performance,
    "not_tested": ["production clone migration", "real Android/WebView scan", "weak network", "physical paper printing", "production SQL timings", "Docker font packaging"]}
report_path = Path(args.report)
report_path.parent.mkdir(parents=True, exist_ok=True)
report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({key: report[key] for key in ["migration_ms", "migration_legacy_fingerprint_equal", "card_operations_legacy_fingerprint_equal", "generated", "performance", "binding_performance"]}, ensure_ascii=False, indent=2))
