"""T01/T02/T05/T07 evidence. SELECT-only; no login or business write.

Run from backend with Django's configured DB. Output contains narrowly scoped
business evidence, not passwords, user credentials or a database dump.
"""
import json
import hashlib
import importlib.util
import os
import sys

sys.path.insert(0, os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'erp.settings')
import django
django.setup()
from django.db import connection, transaction
from django.db.models import Q
from orders.overview import build_order_overview
from orders.services import production_totals_by_order
from production.models import ProductionRun, ProductionRecordAudit, ProductionImportBatch
from quality.models import ProcessCard, QualityOrder, QualityShipmentBatch, QualityShipmentLine, QualityShipmentOrderAllocation, QualityReworkCase, QualityEmployee
from quality.tracing import attach_shipment_card_sources, build_card_shipment_trace
from quality.services import delivered_quantities_by_order, shipment_line_piece_quantity

with connection.cursor() as cursor:
    cursor.execute('PRAGMA query_only=ON')

result = {'read_only': True, 'scope': '当前数据库一致性只读事务；不是原始扫码证据或现场纸账核对', 'production_leads': []}
with transaction.atomic():
    models = {'orders': QualityOrder, 'batches': QualityShipmentBatch, 'lines': QualityShipmentLine,
              'allocations': QualityShipmentOrderAllocation, 'employees': QualityEmployee, 'return_cases': QualityReworkCase}
    result['current_counts'] = {name: model.objects.count() for name, model in models.items()}
    order_ids = list(QualityOrder.objects.values_list('id', flat=True))
    balances = {}
    result['net_deliveries'] = delivered_quantities_by_order(order_ids, breakdown=balances) if os.environ.get('ERP_COMPARE_BASELINE_SERVICES') else delivered_quantities_by_order(order_ids)
    if os.environ.get('ERP_COMPARE_BASELINE_SERVICES'):
        # Load a saved original source in the SAME read transaction. This is
        # a diagnostic shadow process, not replacement of the running app.
        spec = importlib.util.spec_from_file_location('quality.integration_baseline', os.environ['ERP_COMPARE_BASELINE_SERVICES'])
        baseline = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(baseline)
        old = baseline.delivered_quantities_by_order(order_ids)
        result['net_delivery_comparison'] = {
            'orders_checked': len(order_ids),
            'identical': old == result['net_deliveries'],
            'differences': {pk: {'before': old[pk], 'after': result['net_deliveries'][pk]}
                            for pk in order_ids if old[pk] != result['net_deliveries'][pk]},
        }
        result['balance_breakdowns'] = {pk: balances[pk] for pk in [90, 162, 186] if pk in balances}
        assert result['net_delivery_comparison']['identical'], 'Net delivery changed in shadow verification'
    for order_no, item_no in [('04-A001-2608070001', '2'), ('04-A001-2609030001', '20')]:
        orders = list(QualityOrder.objects.filter(order_no=order_no, item_no=item_no))
        lead = {'order_no': order_no, 'item_no': item_no, 'orders': [], 'conclusion': '待依据核对'}
        for order in orders:
            runs = ProductionRun.objects.filter(Q(order=order) | Q(order_links__order=order)).distinct().prefetch_related('daily_logs', 'order_links__order').order_by('id')
            run_rows = []
            for run in runs:
                logs = list(run.daily_logs.order_by('sequence_no', 'id').values())
                run_rows.append({
                    'id': run.pk, 'continuation_of_id': run.continuation_of_id, 'segment_no': run.segment_no,
                    'status': run.status, 'cavities': run.cavities, 'created_at': run.created_at,
                    'updated_at': run.updated_at, 'notes': run.notes,
                    'mold_count': run.produced_mold_count, 'theoretical_quantity': run.theoretical_quantity,
                    'qualified_production_quantity': run.qualified_production_quantity,
                    'order_distribution': [{'order_id': link.order_id, 'quantity': quantity} for link, quantity in run.order_distribution()],
                    'logs': logs,
                    'audits': list(ProductionRecordAudit.objects.filter(run=run).values()),
                })
            lead['orders'].append({'id': order.pk, 'specification': order.specification, 'material': order.material,
                                   'production_summary': production_totals_by_order([order.pk])[order.pk], 'runs': run_rows})
        result['production_leads'].append(lead)
    result['import_candidates'] = [{
        'id': batch.pk, 'original_name': batch.original_name, 'status': batch.status,
        'created_at': batch.created_at, 'committed_at': batch.committed_at,
        'match': [number for number in ['04-A001-2608070001', '04-A001-2609030001']
                  if number in json.dumps(batch.payload, ensure_ascii=False)],
    } for batch in ProductionImportBatch.objects.all()
      if any(number in json.dumps(batch.payload, ensure_ascii=False) for number in ['04-A001-2608070001', '04-A001-2609030001'])]
    result['orders'] = {str(pk): build_order_overview(pk, section='production', page_size=20) for pk in [90, 162, 186] if QualityOrder.objects.filter(pk=pk).exists()}
    batches = list(QualityShipmentBatch.objects.filter(shipment_no__in=['QS-20261102-1D306A64', 'QS-20261102-48751B22']).prefetch_related('lines__order_allocations__order'))
    attach_shipment_card_sources(batches)
    result['shipment_samples'] = [{
        'id': batch.pk, 'shipment_no': batch.shipment_no, 'card_sources': batch.trace_card_sources,
        'lines': [{'id': line.pk, 'quantity': line.piece_quantity, 'weight_kg': line.net_weight_kg,
                   'allocations': [{'id': share.pk, 'order_id': share.order_id, 'order_no': share.order.order_no,
                                    'item_no': share.order.item_no, 'quantity': share.piece_quantity, 'weight_kg': share.net_weight_kg}
                                   for share in line.order_allocations.all()]} for line in batch.lines.all()],
    } for batch in batches]
    result['nine_round_cases'] = list(QualityReworkCase.objects.filter(process_card__card_no='04-M003-2607090266').values('id', 'return_round', 'status', 'shipment_batch_id', 'reshipment_batch_id'))
    card = ProcessCard.objects.filter(card_no='04-M003-2607090266').first()
    if card:
        trace = build_card_shipment_trace(card)
        result['nine_round_trace'] = trace
        expected = {case['id'] for case in result['nine_round_cases']}
        actual = {event['id'] for event in trace['events'] if event['type'] == 'return'}
        assert expected <= actual, 'Existing return rounds missing from lifecycle'
    result['order90_physical_share_differences'] = []
    for line in QualityShipmentLine.objects.filter(batch__status='CONFIRMED', order_allocations__order_id=90).distinct().select_related('batch', 'process_card').prefetch_related('order_allocations__order'):
        shares = list(line.order_allocations.all())
        own = sum(share.piece_quantity for share in shares if share.order_id == 90)
        physical = shipment_line_piece_quantity(line)
        if own != physical:
            result['order90_physical_share_differences'].append({
                'line_id': line.pk, 'batch_id': line.batch_id, 'shipment_no': line.batch.shipment_no,
                'physical': physical, 'own': own, 'difference': physical - own,
                'allocations': [{'order_id': share.order_id, 'order_no': share.order.order_no, 'item_no': share.order.item_no,
                                 'quantity': share.piece_quantity} for share in shares],
            })
    result['business_fingerprints'] = {}
    with connection.cursor() as cursor:
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = [row[0] for row in cursor.fetchall() if row[0].startswith(('quality_', 'production_', 'orders_', 'inventory_', 'molds_', 'analytics_'))]
        for table in tables:
            digest, count = hashlib.sha256(), 0
            cursor.execute(f'SELECT * FROM "{table}" ORDER BY rowid')
            for row in cursor:
                digest.update(json.dumps(row, ensure_ascii=False, default=str).encode())
                count += 1
            result['business_fingerprints'][table] = {'rows': count, 'sha256': digest.hexdigest()}
print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
