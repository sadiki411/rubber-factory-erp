"""Evidence only: read synthetic UI DB, verify a disposable clone and restore.

Never opens the default/production DB. Refuses non-SYNTH orders. The source UI
DB is opened read-only; permission/freeze/recovery work uses a new clone.
"""
import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / ".codex_tmp/flow-pilot-20261119.sqlite3"
CLONE = ROOT / ".codex_tmp/flow-pilot-20261119-verify.sqlite3"
OUT = ROOT / "output/flow-pilot-20261119"
if not SOURCE.is_file() or CLONE.exists():
    raise SystemExit("Require the synthetic UI DB and a fresh verification clone")
source = sqlite3.connect(f"{SOURCE.as_uri()}?mode=ro", uri=True)
assert source.execute("select count(*) from quality_qualityorder where order_no not like 'SYNTH-%'").fetchone()[0] == 0
source.backup(sqlite3.connect(CLONE))
source.close()
os.environ.update(SQLITE_PATH=str(CLONE), DJANGO_SETTINGS_MODULE="erp.settings", FLOW_CARDS_WRITE_ENABLED="1",
                  FLOW_CARDS_PRIVATE_ROOT=str(CLONE.with_suffix(".private")))
sys.path.insert(0, str(ROOT / "backend"))
import django
django.setup()
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.db import connection
from rest_framework.test import APIClient
from quality.models import (QualityOrder, QualityShipmentBatch, QualityShipmentOrderAllocation,
                            QualityReturnAllocation, QualityReworkCase, ProcessCard)
from flowcards.models import InternalCard, CardEvent, ShipmentSource, TemplatePublication, PrintJob
from flowcards import services as s

worker = get_user_model().objects.get(username="pilot-worker")
reader = get_user_model().objects.get(username="pilot-reader")
client = APIClient(HTTP_HOST="127.0.0.1"); client.force_authenticate(worker)
evidence = {"synthetic_only": True, "source_opened_read_only": True, "formal_publications": TemplatePublication.objects.count(),
            "ordinary_worker": {"is_staff": worker.is_staff, "is_superuser": worker.is_superuser}, "suites": [], "permissions": []}
for tag in ("SCAN", "MANUAL"):
    orders = list(QualityOrder.objects.filter(order_no__startswith=f"SYNTH-{tag}-").order_by("id"))
    allocations = list(QualityShipmentOrderAllocation.objects.filter(order__in=orders).select_related("shipment_line__batch", "order"))
    returns = list(QualityReturnAllocation.objects.filter(shipment_order_allocation__in=allocations).select_related("case", "shipment_order_allocation__order"))
    batches = list(QualityShipmentBatch.objects.filter(lines__order_allocations__order__in=orders).distinct().order_by("id"))
    cases = list(QualityReworkCase.objects.filter(shipment_batch__in=batches).order_by("id"))
    cards = list(InternalCard.objects.filter(group__orders__in=orders).distinct().order_by("sequence"))
    shipment_rows = [{"id": b.pk, "shipment_no": b.shipment_no, "pieces": b.shipped_quantity, "status": b.status,
                      "inspectors": list(b.inspectors.values_list("employee_no", flat=True)),
                      "allocations": [{"order_no": a.order.order_no, "item_no": a.order.item_no,
                                       "pieces": a.piece_quantity, "kg": str(a.net_weight_kg)} for a in allocations if a.shipment_line.batch_id == b.pk]}
                     for b in batches]
    order_rows = []
    for order in orders:
        sent = sum(a.piece_quantity for a in allocations if a.order_id == order.pk and a.shipment_line.batch.status == "CONFIRMED")
        returned = sum(a.piece_quantity for a in returns if a.shipment_order_allocation.order_id == order.pk and a.case.status != "CANCELLED")
        order_rows.append({"id": order.pk, "order_no": order.order_no, "item_no": order.item_no, "required": order.order_quantity,
                           "gross_sent": sent, "valid_returned": returned, "net": sent-returned, "stored_status": order.status})
    sources = list(ShipmentSource.objects.filter(card__in=cards).order_by("id"))
    evidence["suites"].append({"tag": tag, "orders": order_rows, "shipments": shipment_rows,
        "returns": [{"id": c.pk, "case_no": c.case_no, "source_batch_id": c.shipment_batch_id, "unit_no": c.shipment_unit_no,
                     "pieces": c.affected_quantity, "status": c.status, "process_card_id": c.process_card_id,
                     "return_round": c.return_round, "reshipment_batch_id": c.reshipment_batch_id,
                     "allocations": [{"order_no": r.shipment_order_allocation.order.order_no, "item_no": r.shipment_order_allocation.order.item_no,
                                      "pieces": r.piece_quantity} for r in returns if r.case_id == c.pk]} for c in cases],
        "internal_cards": [{"id": str(c.pk), "card_no": c.card_no, "planned_molds": c.planned_molds} for c in cards],
        "sources": [{"card_no": row.card.card_no, "batch_id": row.batch_id, "scope": row.scope,
                     "shipment_unit_no": row.shipment_unit_no, "state": row.state} for row in sources],
        "net_total": sum(o["net"] for o in order_rows)})
    assert all(r.scope == "SET_UNKNOWN" and r.shipment_unit_no is None for r in sources)
    assert all(sum(a["pieces"] for a in row["allocations"]) == row["pieces"] == 100 for row in shipment_rows)

card = InternalCard.objects.order_by("sequence").first()
path = f"/api/flow-cards/cards/{card.pk}/record/"
for label, user in (("anonymous", None), ("read_only", reader), ("ordinary", worker)):
    check = APIClient(HTTP_HOST="127.0.0.1")
    if user: check.force_authenticate(user)
    before = CardEvent.objects.count()
    read = check.get(f"/api/flow-cards/cards/{card.pk}/")
    write = check.post(path, {}, format="json")
    evidence["permissions"].append({"role": label, "GET": read.status_code, "POST_empty": write.status_code})
    assert CardEvent.objects.count() == before
    assert (read.status_code == 200 and write.status_code == 400) if user == worker else write.status_code in (401,403)
with override_settings(FLOW_CARDS_WRITE_ENABLED=False):
    before = CardEvent.objects.count()
    disabled = client.post(path, {}, format="json")
    evidence["write_switch_off"] = {"GET": client.get(f"/api/flow-cards/cards/{card.pk}/").status_code, "POST": disabled.status_code}
    assert disabled.status_code == 403 and CardEvent.objects.count() == before

# Freeze an old-renderer synthetic card in the disposable clone, not the UI DB.
group = card.group
group.snapshot["template"]["renderer"] = "flowcard-review-20261118-v3"
group.save(update_fields=["snapshot"])
job = client.post("/api/flow-cards/print-jobs/", {"request_key":"SYNTH-OLD-FROZEN", "card_ids":[str(card.pk)]}, format="json")
assert job.status_code == 201, job.content
url = job.json()["pdf_url"]
original = b"".join(client.get(url).streaming_content)
with patch("flowcards.pdf.render_cards", side_effect=AssertionError("GET must not redraw")), override_settings(FLOW_CARDS_WRITE_ENABLED=False):
    downloaded = b"".join(client.get(url).streaming_content)
assert original == downloaded
evidence["frozen_pdf"] = {"renderer":"flowcard-review-20261118-v3", "sha256":hashlib.sha256(original).hexdigest(),
                          "download_with_write_off_and_renderer_disabled_identical": True}

def state_hash(db):
    con = sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True)
    h = hashlib.sha256(); counts = {}
    for (table,) in con.execute("select name from sqlite_master where type='table' order by name"):
        if not table.startswith(("flowcards_", "quality_", "production_", "orders_", "inventory_")): continue
        rows = con.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()
        counts[table] = len(rows)
        h.update(json.dumps([table, rows], ensure_ascii=False, default=str).encode())
    con.close()
    return {"sha256":h.hexdigest(), "table_counts":counts}

connection.close()
backup = CLONE.with_suffix(".backup.sqlite3"); restored = CLONE.with_suffix(".restored.sqlite3")
if backup.exists() or restored.exists(): raise SystemExit("Refuse overwrite of recovery evidence")
con = sqlite3.connect(CLONE); con.backup(sqlite3.connect(backup)); con.close()
con = sqlite3.connect(backup); con.backup(sqlite3.connect(restored)); con.close()
original_state, restored_state = state_hash(CLONE), state_hash(restored)
assert original_state == restored_state
job = PrintJob.objects.get(pk=job.json()["id"])
private = Path(os.environ["FLOW_CARDS_PRIVATE_ROOT"])
pdf = private / job.pdf_storage_name
private_copy = CLONE.with_suffix(".restored.private") / job.pdf_storage_name
private_copy.parent.mkdir(parents=True, exist_ok=True)
private_copy.write_bytes(pdf.read_bytes())
assert hashlib.sha256(private_copy.read_bytes()).hexdigest() == job.pdf_sha256
evidence["recovery"] = {"business_tables_equal":True, "snapshot":original_state, "private_pdf_sha256_equal":True,
                        "scope":"local SQLite backup API + restore copy; not target-server recovery or old-release application rollback"}
evidence["measurement_records"] = [{"id":str(e.pk), "kind":e.kind, "cards":list(e.cards.values_list("card_no",flat=True)),
    "business_at":e.business_at.isoformat() if e.business_at else None, "actual":e.values.get("actual"), "judgment":e.values.get("judgment"),
    "review_effect":e.values.get("review_effect"), "replaces_id":str(e.replaces_id) if e.replaces_id else None,
    "historical_for":e.values.get("historical_for"), "sample_ref":e.values.get("sample_ref")}
    for e in CardEvent.objects.order_by("created_at")]
OUT.mkdir(parents=True, exist_ok=True)
(OUT/"quantity-permission-recovery.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2),encoding="utf-8")
print(json.dumps({"net_totals":{s["tag"]:s["net_total"] for s in evidence["suites"]},"permissions":evidence["permissions"],
                  "write_switch_off":evidence["write_switch_off"],"recovery_pass":True,"published":evidence["formal_publications"]}, ensure_ascii=False))
