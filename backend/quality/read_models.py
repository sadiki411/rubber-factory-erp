"""Request-scoped read projections; never persisted or used for validation.

The mobile scan endpoints share a worker with the historical ledgers. Build
card-chain facts once per response instead of querying them for every field
and every repeated nested representation of the same card.
"""
from collections import defaultdict
from decimal import Decimal

from django.db.models import Model, prefetch_related_objects

from .models import (
    ProcessCard, ProcessCardUnitBinding, QualityOrder, QualityReworkCase,
    QualityShipmentBatch, QualityShipmentLine,
)


def _graph(roots):
    found = defaultdict(list)
    seen = set()
    pending = list(roots)
    while pending:
        obj = pending.pop()
        if not isinstance(obj, Model) or id(obj) in seen:
            continue
        seen.add(id(obj))
        found[type(obj)].append(obj)
        pending.extend(obj._state.fields_cache.values())
        for values in getattr(obj, '_prefetched_objects_cache', {}).values():
            pending.extend(values)
    return found


def prepare_quality_read_context(roots, context):
    """Hydrate only the selected response graph, with bounded SQL round trips."""
    graph = _graph(roots)
    cards = graph[ProcessCard]
    if cards:
        prefetch_related_objects(
            cards, 'replaces', 'replaced_by', 'order', 'product_specification',
            'unit_binding__shipment_batch__inspector',
            'unit_binding__shipment_batch__inspectors',
            'unit_binding__shipment_batch__lines__order_allocations__order',
            'unit_binding__shipment_batch__lines__process_card__order',
            'unit_binding__shipment_batch__lines__order',
        )
    graph = _graph(roots)
    batches = graph[QualityShipmentBatch]
    if batches:
        prefetch_related_objects(batches, 'order', 'product_specification', 'inspectors', 'inspector')
    lines = _graph(roots)[QualityShipmentLine]
    if lines:
        prefetch_related_objects(lines, 'product_specification')
    orders = _graph(roots)[QualityOrder]
    if orders:
        prefetch_related_objects(orders, 'created_by', 'product_specification__mold_model')
    graph = _graph(roots)
    cards = graph[ProcessCard]
    tracking_ids = {obj.tracking_id for obj in cards}
    if not tracking_ids:
        return
    totals = {key: {
        'pieces': 0, 'weight': Decimal('0'), 'linked_batches': set(),
        'occurrences': {}, 'returned_pieces': 0, 'returned_weight': Decimal('0'),
        'unweighed_return_pieces': 0, 'rework_count': 0, 'current_return': None,
    } for key in tracking_ids}
    for line in QualityShipmentLine.objects.filter(
        process_card__tracking_id__in=tracking_ids,
        batch__status=QualityShipmentBatch.Status.CONFIRMED,
    ).order_by().values('process_card__tracking_id', 'batch_id', 'piece_quantity', 'net_weight_kg'):
        facts = totals[line['process_card__tracking_id']]
        facts['pieces'] += int(line['piece_quantity'] or 0)
        facts['weight'] += Decimal(line['net_weight_kg'] or 0)
        facts['linked_batches'].add(line['batch_id'])
    for binding in ProcessCardUnitBinding.objects.filter(
        process_card__tracking_id__in=tracking_ids,
        shipment_batch__status=QualityShipmentBatch.Status.CONFIRMED,
    ).values('process_card__tracking_id', 'shipment_batch_id', 'shipment_unit_no', 'piece_quantity', 'net_weight_kg'):
        facts = totals[binding['process_card__tracking_id']]
        if binding['shipment_batch_id'] not in facts['linked_batches']:
            facts['occurrences'][(binding['shipment_batch_id'], binding['shipment_unit_no'])] = (
                int(binding['piece_quantity']), Decimal(binding['net_weight_kg']),
            )
    # Preserve model default ordering: legacy duplicate source occurrences use
    # the same first historical row as ProcessCard._tracking_outbound_totals.
    for case in QualityReworkCase.objects.filter(
        process_card__tracking_id__in=tracking_ids,
    ).exclude(status=QualityReworkCase.Status.CANCELLED).values(
        'id', 'process_card__tracking_id', 'origin', 'shipment_batch_id',
        'shipment_unit_no', 'affected_quantity', 'affected_weight_kg',
        'is_current_return', 'return_round', 'case_no', 'status', 'opened_on',
    ):
        facts = totals[case['process_card__tracking_id']]
        facts['rework_count'] += 1
        if case['origin'] != QualityReworkCase.Origin.CUSTOMER_RETURN:
            continue
        facts['returned_pieces'] += int(case['affected_quantity'] or 0)
        if case['affected_weight_kg'] is not None:
            facts['returned_weight'] += Decimal(case['affected_weight_kg'])
        else:
            facts['unweighed_return_pieces'] += int(case['affected_quantity'] or 0)
        if case['is_current_return']:
            current = facts['current_return']
            if current is None or (case['return_round'] or 0, case['id']) > (current['return_round'] or 0, current['id']):
                facts['current_return'] = case
        if case['shipment_batch_id'] not in facts['linked_batches']:
            key = (case['shipment_batch_id'], case['shipment_unit_no'] if case['shipment_unit_no'] is not None else f"legacy-{case['id']}")
            facts['occurrences'].setdefault(key, (int(case['affected_quantity'] or 0), Decimal(case['affected_weight_kg'] or 0)))
    for facts in totals.values():
        facts['pieces'] += sum(row[0] for row in facts['occurrences'].values())
        facts['weight'] += sum((row[1] for row in facts['occurrences'].values()), Decimal('0'))
    context['_card_read_totals'] = totals


def card_read_metrics(card, context):
    facts = context.get('_card_read_totals', {}).get(card.tracking_id)
    if facts is None:
        return None
    returned_weight = facts['returned_weight'] + Decimal(facts['unweighed_return_pieces']) * Decimal(card.unit_weight_g or 0) / Decimal('1000')
    delivered_weight = max(Decimal('0'), facts['weight'] - returned_weight)
    return {
        'shipped_net_weight_kg': facts['weight'],
        'delivered_net_weight_kg': delivered_weight,
        'remaining_weight_kg': max(Decimal('0'), card.max_allowed_weight_kg - delivered_weight),
        'shipped_piece_quantity': facts['pieces'],
        'returned_piece_quantity': facts['returned_pieces'],
        'delivered_piece_quantity': max(0, facts['pieces'] - facts['returned_pieces']),
        'rework_count': facts['rework_count'],
        'current_return': facts['current_return'],
    }
