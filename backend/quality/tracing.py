"""Read-only identity tracing; never infer a card from product text or dates.

Fulfilment allocations are accounting facts, not the printed card's origin.
Current bindings can move on reshipment, so returns and correction snapshots
must remain searchable independently of the latest binding.
"""
from django.db.models import Q
from django.utils import timezone

from .models import (
    ProcessCard, ProcessCardUnitBinding, QualityReworkCase, QualityShipmentBatch,
    QualityShipmentBatchRevision, QualityShipmentLine,
)


EVIDENCE = {
    "LINE": "明细直接关联",
    "UNIT_BINDING": "系统物理包绑定",
    "RETURN_SOURCE": "退回单保存的原出货来源",
    "RESHIP_SOURCE": "退回单保存的再出货来源",
    "REVISION_BEFORE": "更正前历史快照",
    "REVISION_AFTER": "更正后历史快照",
}


def snapshot_card_refs(snapshot):
    """Only explicit identity fields; never search arbitrary notes as evidence."""
    if not isinstance(snapshot, dict):
        return
    for key in ("lines", "process_card_bindings"):
        rows = snapshot.get(key) or []
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            card_id, card_no = row.get("process_card_id"), row.get("card_no")
            if not card_id and not card_no:
                continue
            try:
                card_id = int(card_id) if card_id else None
            except (ValueError, TypeError):
                card_id = None
            yield {
                "card_id": card_id, "card_no": str(card_no or ""),
                "shipment_unit_no": row.get("shipment_unit_no") if key == "process_card_bindings" else None,
                "line_id": row.get("id") if key == "lines" else None,
                "piece_quantity": row.get("piece_quantity"), "net_weight_kg": row.get("net_weight_kg"),
            }


def attach_shipment_card_sources(batches):
    """One bounded set of queries for any list, including compact ledgers."""
    batches = list(batches)
    missing = [batch for batch in batches if not hasattr(batch, "trace_card_sources")]
    if not missing:
        return batches
    ids = {batch.pk for batch in missing}
    references = {pk: {} for pk in ids}

    def add(batch_id, card_id, card_no, evidence, *, unit_no=None, line_id=None,
            pieces=None, weight=None, historical=False):
        if batch_id not in references or not (card_id or card_no):
            return
        key = (card_id or card_no, unit_no)
        value = references[batch_id].get(key)
        source = {"code": evidence, "label": EVIDENCE[evidence]}
        if value is None:
            value = references[batch_id][key] = {
                "card_id": card_id, "card_no": card_no, "shipment_unit_no": unit_no,
                "line_id": line_id, "piece_quantity": pieces,
                "net_weight_kg": str(weight) if weight is not None else None,
                "quantity_scope": "PHYSICAL_UNIT" if unit_no is not None and pieces is not None else "LINE" if pieces is not None else None,
                "historical_only": historical, "evidence": [],
            }
        elif not historical and value["historical_only"]:
            value.update(historical_only=False, piece_quantity=pieces,
                         net_weight_kg=str(weight) if weight is not None else None)
        if source not in value["evidence"]:
            value["evidence"].append(source)

    for line in QualityShipmentLine.objects.filter(batch_id__in=ids, process_card__isnull=False).select_related("process_card"):
        add(line.batch_id, line.process_card_id, line.process_card.card_no, "LINE", line_id=line.pk)
    for binding in ProcessCardUnitBinding.objects.filter(shipment_batch_id__in=ids).select_related("process_card"):
        add(binding.shipment_batch_id, binding.process_card_id, binding.process_card.card_no,
            "UNIT_BINDING", unit_no=binding.shipment_unit_no, pieces=binding.piece_quantity, weight=binding.net_weight_kg)
    cases = QualityReworkCase.objects.filter(
        Q(shipment_batch_id__in=ids) | Q(reshipment_batch_id__in=ids) | Q(shipment_line__batch_id__in=ids)
    ).select_related("process_card", "shipment_line__process_card")
    for case in cases:
        card = case.process_card or (case.shipment_line.process_card if case.shipment_line_id else None)
        if card is None:
            continue
        # affected_quantity may be a partial return, not the original whole
        # package. Do not present it as that package's original quantity.
        source_batch_id = case.shipment_batch_id or (case.shipment_line.batch_id if case.shipment_line_id else None)
        add(source_batch_id, card.pk, card.card_no,
            "RETURN_SOURCE", unit_no=case.shipment_unit_no, historical=True)
        add(case.reshipment_batch_id, card.pk, card.card_no,
            "RESHIP_SOURCE", unit_no=1, historical=True)
    for revision in QualityShipmentBatchRevision.objects.filter(batch_id__in=ids).only(
        "batch_id", "before_snapshot", "after_snapshot"
    ).order_by("created_at", "id"):
        for field, evidence in (("before_snapshot", "REVISION_BEFORE"), ("after_snapshot", "REVISION_AFTER")):
            for ref in snapshot_card_refs(getattr(revision, field)):
                add(revision.batch_id, ref["card_id"], ref["card_no"], evidence,
                    unit_no=ref["shipment_unit_no"], line_id=ref["line_id"],
                    historical=True)
    card_ids = {value["card_id"] for refs in references.values() for value in refs.values() if value["card_id"]}
    cards = ProcessCard.objects.select_related("order").in_bulk(card_ids)
    for batch in missing:
        refs = list(references[batch.pk].values())
        # Direct line + independent binding are two pieces of evidence for one
        # card, not two packages. Merge only when the unit identity is unique.
        for ref in list(refs):
            units = [other for other in refs if other["shipment_unit_no"] is not None
                     and (other["card_id"] or other["card_no"]) == (ref["card_id"] or ref["card_no"])]
            if ref["shipment_unit_no"] is None and len(units) == 1:
                target = units[0]
                target["evidence"].extend(item for item in ref["evidence"] if item not in target["evidence"])
                target["line_id"] = target["line_id"] or ref["line_id"]
                target["historical_only"] = target["historical_only"] and ref["historical_only"]
                refs.remove(ref)
        for ref in refs:
            card = cards.get(ref["card_id"])
            ref["card_no"] = ref["card_no"] or (card.card_no if card else "")
            ref["current_system_order"] = ({"id": card.order_id, "order_no": card.order.order_no, "item_no": card.order.item_no} if card else None)
            # The existing source_order_no may itself be an automatic fallback.
            # It is not independent evidence of the original paper order.
            ref["original_paper_order"] = None
            ref["paper_order_evidence"] = "本接口未核验原始纸卡订单，不能按当前系统来源推断"
        refs.sort(key=lambda ref: (ref["historical_only"], ref["shipment_unit_no"] is None, ref["shipment_unit_no"] or 0, ref["card_no"]))
        batch.trace_card_sources = refs
    return batches


def shipment_card_sources(batch):
    attach_shipment_card_sources([batch])
    return batch.trace_card_sources


def batch_ids_for_cards(card_ids):
    card_ids = set(card_ids)
    if not card_ids:
        return set()
    ids = set(QualityShipmentLine.objects.filter(process_card_id__in=card_ids).values_list("batch_id", flat=True))
    ids.update(ProcessCardUnitBinding.objects.filter(process_card_id__in=card_ids).values_list("shipment_batch_id", flat=True))
    for source, reshipment, line_batch in QualityReworkCase.objects.filter(
        Q(process_card_id__in=card_ids) | Q(shipment_line__process_card_id__in=card_ids)
    ).values_list("shipment_batch_id", "reshipment_batch_id", "shipment_line__batch_id"):
        ids.update(value for value in (source, reshipment, line_batch) if value)
    numbers = set(ProcessCard.objects.filter(pk__in=card_ids).values_list("card_no", flat=True))
    for revision in QualityShipmentBatchRevision.objects.only("batch_id", "before_snapshot", "after_snapshot"):
        if any(ref["card_id"] in card_ids or ref["card_no"] in numbers
               for snapshot in (revision.before_snapshot, revision.after_snapshot)
               for ref in snapshot_card_refs(snapshot)):
            ids.add(revision.batch_id)
    return ids


def batch_ids_for_card_query(query):
    # Short ambiguous fragments must not silently turn into identity matches.
    query = str(query or "").strip()
    if len(query) < 4:
        return set()
    tracking_ids = ProcessCard.objects.filter(card_no__icontains=query).values("tracking_id")
    return batch_ids_for_cards(ProcessCard.objects.filter(tracking_id__in=tracking_ids).values_list("pk", flat=True))


def build_card_shipment_trace(card):
    from .services import shipment_line_piece_quantity

    chain = list(ProcessCard.objects.filter(tracking_id=card.tracking_id).order_by("created_at", "id"))
    card_ids = {item.pk for item in chain}
    card_nos = {item.card_no for item in chain}
    batches = list(QualityShipmentBatch.objects.filter(pk__in=batch_ids_for_cards(card_ids)).prefetch_related(
        "lines__order_allocations", "lines__process_card"
    ))
    attach_shipment_card_sources(batches)
    cases = list(QualityReworkCase.objects.filter(
        Q(process_card_id__in=card_ids) | Q(shipment_line__process_card_id__in=card_ids)
    ).order_by("created_at", "id"))
    reshipment_ids = {case.reshipment_batch_id for case in cases if case.reshipment_batch_id}
    events = []
    for batch in batches:
        lines = list(batch.lines.all())
        quantities_known = all(line.piece_quantity is not None or (
            line.unit_weight_g_snapshot or (line.process_card.unit_weight_g if line.process_card_id else None)
        ) for line in lines)
        refs = [ref for ref in batch.trace_card_sources if ref["card_id"] in card_ids or ref["card_no"] in card_nos]
        events.append({
            "key": f"shipment:{batch.pk}", "type": "reshipment" if batch.pk in reshipment_ids else "shipment",
            "id": batch.pk, "document_no": batch.shipment_no, "business_date": batch.shipment_date,
            "occurred_at": batch.created_at, "status": batch.status, "card_sources": refs,
            "batch_quantity": sum(shipment_line_piece_quantity(line) for line in lines) if lines and quantities_known else None,
            "quantity_evidence": "按历史单重折算" if any(line.piece_quantity is None for line in lines) and quantities_known else "已保存件数" if quantities_known else "件数及折算依据缺失",
            "batch_weight_kg": str(sum((line.net_weight_kg for line in lines), 0)),
            "allocation_scope": "整张出货单的已保存交付分摊，不等于每张卡各发一次",
            "allocations": [{"line_id": line.pk, "order_id": share.order_id,
                "order_no": share.order_no_snapshot, "item_no": share.item_no_snapshot,
                "piece_quantity": share.piece_quantity, "net_weight_kg": str(share.net_weight_kg)}
                for line in lines for share in line.order_allocations.all()],
            "url": f"/quality?shipment_batch_id={batch.pk}",
        })
    for case in cases:
        events.append({"key": f"return:{case.pk}", "type": "return", "id": case.pk,
            "document_no": case.case_no, "business_date": case.opened_on,
            "occurred_at": case.created_at, "status": case.status,
            "piece_quantity": case.affected_quantity, "net_weight_kg": str(case.affected_weight_kg) if case.affected_weight_kg is not None else None,
            "return_round": case.return_round, "shipment_batch_id": case.shipment_batch_id,
            "reshipment_batch_id": case.reshipment_batch_id,
            "url": f"/quality?tab=reworks&rework_case_id={case.pk}"})
    for revision in QualityShipmentBatchRevision.objects.filter(batch_id__in=[b.pk for b in batches]).select_related("batch"):
        events.append({"key": f"revision:{revision.pk}", "type": "correction", "id": revision.pk,
            "document_no": revision.batch.shipment_no, "business_date": timezone.localdate(revision.created_at),
            "occurred_at": revision.created_at, "status": revision.action,
            "reason": revision.reason, "operator_id": revision.operator_id,
            "scope": "整张关联出货单的更正，不代表单独更正此卡",
            "before_card_nos": sorted({ref["card_no"] for ref in snapshot_card_refs(revision.before_snapshot) if ref["card_no"]}),
            "after_card_nos": sorted({ref["card_no"] for ref in snapshot_card_refs(revision.after_snapshot) if ref["card_no"]}),
            "url": f"/quality?shipment_batch_id={revision.batch_id}"})
    events.sort(key=lambda event: (event["occurred_at"], event["key"]))
    return {"card_id": card.pk, "card_no": card.card_no,
        "cards": [{"id": item.pk, "card_no": item.card_no, "status": item.status} for item in chain],
        "events": events, "shipment_count": len(batches), "return_count": len(cases),
        "limitations": ["仅串联明确外键、物理包绑定、退回来源和更正快照，不按规格或号码段猜配。",
            "当前系统来源订单不等于经过纸卡核验的原始订单；缺原始凭据保持未知。",
            "此卡全部历史不受出货列表日期限制；整单分摊不可按关联卡数重复累计。"]}
