"""Live order-group yields, with no inferred history or inventory mutation."""
import hashlib
import json
import unicodedata
from decimal import Decimal, ROUND_HALF_UP

from django.db.models import BigIntegerField, F, Sum, Value
from django.db.models.functions import Coalesce, NullIf

from .models import OrderYieldRemainder


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _identity(value):
    return unicodedata.normalize("NFKC", value or "").strip().casefold()


def order_yields(order_ids, *, delivered=None):
    """Batch graph, quantities and remainders; never allocate yields by due date.

    All explicit edges are read once so transitive groups crossing the current
    page are complete. Logs, delivery balances and confirmations are then read
    only for the selected groups, not once per row.
    """
    from production.models import ProductionDailyLog, ProductionRun
    from quality.models import QualityOrder
    from quality.services import delivered_quantities_by_order

    requested = set(order_ids)
    if not requested:
        return {}
    parents, run_orders, run_statuses = {}, {}, {}

    def root(node):
        parents.setdefault(node, node)
        while parents[node] != node:
            parents[node] = parents[parents[node]]
            node = parents[node]
        return node

    for row in ProductionRun.objects.exclude(status="CANCELLED").order_by().values(
        "id", "order_id", "status", "order_links__order_id"
    ):
        oid = row["order_links__order_id"] or row["order_id"]
        if oid is None:
            continue
        linked = run_orders.setdefault(row["id"], set())
        if linked:
            parents[root(oid)] = root(next(iter(linked)))
        linked.add(oid)
        root(oid)
        run_statuses[row["id"]] = row["status"]

    selected_roots = {root(oid) for oid in requested}
    groups, group_runs = {}, {}
    for oid in list(parents):
        group = root(oid)
        if group in selected_roots:
            groups.setdefault(group, set()).add(oid)
    for rid, ids in run_orders.items():
        group = root(next(iter(ids)))
        if group in selected_roots:
            group_runs.setdefault(group, set()).add(rid)
    relevant_ids = set().union(*groups.values())
    orders = {row["id"]: row for row in QualityOrder.objects.filter(
        pk__in=relevant_ids
    ).order_by().values("id", "order_no", "item_no", "status", "specification", "material")}
    relevant_runs = set().union(*group_runs.values()) if group_runs else set()
    quantities = {}
    if relevant_runs:
        quantities = dict(ProductionDailyLog.objects.filter(
            run_id__in=relevant_runs, is_cancelled=False
        ).order_by().values("run_id").annotate(total=Sum(
            F("produced_mold_count") * Coalesce(
                NullIf(F("cavities_snapshot"), Value(0)), F("run__cavities")
            ), output_field=BigIntegerField()
        )).values_list("run_id", "total"))
    effective = dict(delivered or {})
    effective.update(delivered_quantities_by_order(relevant_ids - effective.keys()))
    keys = {gid: _digest(sorted(ids)) for gid, ids in groups.items()}
    records = {record.group_key: record for record in OrderYieldRemainder.objects.filter(
        group_key__in=keys.values()
    ).select_related("confirmed_by")}
    summaries = {}
    for gid, ids in groups.items():
        oids, rids = sorted(ids), sorted(group_runs.get(gid, ()))
        rows = [orders[oid] for oid in oids if oid in orders]
        production = sum(int(quantities.get(rid, 0) or 0) for rid in rids)
        shipped = sum(int(effective.get(oid, 0) or 0) for oid in oids)
        record = records.get(keys[gid])
        scope_changed = bool(record and record.source_run_ids != rids)
        recorded = bool(record and not scope_changed)
        remaining = record.remaining_quantity if recorded else 0
        source_changed = bool(record and (
            scope_changed or record.production_quantity_snapshot != production
            or record.shipped_quantity_snapshot != shipped
        ))
        status = "READY"
        if not production:
            status = "NO_PRODUCTION"
        elif len({(_identity(row["specification"]), _identity(row["material"])) for row in rows}) > 1:
            status = "SOURCE_MISMATCH"
        elif any(row["status"] != "COMPLETED" for row in rows):
            status = "GROUP_INCOMPLETE"
        elif any(run_statuses[rid] != "COMPLETED" for rid in rids):
            status = "PRODUCTION_INCOMPLETE"
        percent = None
        if status == "READY":
            percent = format((Decimal(shipped + remaining) * 100 / production).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            ), "f")
        summary = {
            "group_key": keys[gid], "order_ids": oids, "run_ids": rids,
            "orders": rows, "combined": len(oids) > 1, "status": status,
            "production_quantity": production, "effective_shipped_quantity": shipped,
            "remaining_quantity": remaining, "remaining_recorded": recorded,
            "saved_remaining_quantity": record.remaining_quantity if record else None,
            "scope_changed": scope_changed, "source_changed": source_changed,
            "yield_percent": percent, "over_100": percent is not None and Decimal(percent) > 100,
            "notes": record.notes if recorded else "",
            "confirmed_at": record.updated_at.isoformat() if record else None,
            "confirmed_by_name": (
                record.confirmed_by.get_full_name() or record.confirmed_by.get_username()
            ) if record else None,
        }
        # Also protects two tabs editing the same remainder concurrently.
        summary["source_token"] = _digest({
            "orders": rows, "runs": [(rid, run_statuses[rid], quantities.get(rid, 0)) for rid in rids],
            "delivered": [(oid, effective.get(oid, 0)) for oid in oids],
            "record_updated": summary["confirmed_at"],
        })
        for oid in ids & requested:
            if oid in orders:
                summaries[oid] = summary
    return summaries
