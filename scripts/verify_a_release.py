"""Read-only A1/A3/A4 audit of an explicitly selected private SQLite snapshot.

From backend: SQLITE_PATH=<snapshot> ERP_A_READONLY_AUDIT=1 python manage.py
shell -c "import runpy; runpy.run_path('../scripts/verify_a_release.py')"
No test business events, mutations, guessed allocations or migrations.
"""
import ast
from datetime import date
from copy import deepcopy
import hashlib
import json
import os
import math
import statistics
import subprocess
import time

from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIRequestFactory, force_authenticate

from analytics.services import build_dashboard
from quality import views
from quality.models import QualityOrder, QualityShipmentBatch, QualityShipmentLine, QualityReworkCase

if os.environ.get('ERP_A_READONLY_AUDIT') != '1' or connection.vendor != 'sqlite':
    raise RuntimeError('Explicit read-only SQLite audit opt-in required')
connection.ensure_connection()
connection.connection.execute('PRAGMA query_only=ON')
assert connection.connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
user = get_user_model().objects.filter(is_active=True, is_superuser=True).first()
if not user:
    raise RuntimeError('Existing administrator required; audit will not create an account')
factory = APIRequestFactory()
params = {'date_from': '2026-10-01', 'date_to': '2026-10-13'}

def invoke(view, path, values):
    request = factory.get(path, values, HTTP_HOST='localhost')
    force_authenticate(request, user=user)
    response = view(request)
    assert response.status_code == 200, response.data
    response.render()
    return response.data

def emit(name, data):
    print(name, json.dumps(data, ensure_ascii=False, default=str), flush=True)

baseline = os.environ.get('ERP_A_BASELINE', '66cf2b4eac5485a610aaee9a4f683bb0906a04d6')
source = subprocess.check_output(['git', 'show', f'{baseline}:backend/quality/views.py'], encoding='utf-8')
node = next(node for node in ast.parse(source).body if isinstance(node, ast.ClassDef) and node.name == 'QualitySummaryView')
namespace = dict(vars(views))
exec(compile(ast.Module(body=[node], type_ignores=[]), '<baseline-quality-summary>', 'exec'), namespace)
before = invoke(namespace['QualitySummaryView'].as_view(), '/api/quality/summary/', params)
with CaptureQueriesContext(connection) as queries:
    after = invoke(views.QualitySummaryView.as_view(), '/api/quality/summary/', params)
def without_daily_event_counts(payload):
    comparable = deepcopy(payload)
    for row in comparable['daily_trend']:
        for key in ('inspection_record_count', 'rework_record_count'):
            row.pop(key, None)
    return comparable

assert without_daily_event_counts(before) == without_daily_event_counts(after), 'Quantities or other summary fields changed: do not publish until explained'
encoded = json.dumps(after, sort_keys=True, default=str).encode()
dashboard = build_dashboard(date_from=date(2026, 10, 1), date_to=date(2026, 10, 13))
factory_quantity = dashboard['quality']['total']['shipped_quantity']
assert factory_quantity == after['totals']['shipped_quantity']
attribution = dashboard['quality_attribution']
for key in ('inspection_record_count', 'rework_record_count'):
    assert sum(row[key] for row in after['daily_trend']) == after['totals'][key], key
emit('SUMMARY_EQUALITY', {'baseline': baseline,
                         'full_response_equal': before == after,
                         'all_except_daily_event_counts_equal': True,
                         'intentional_metadata_change': 'daily inspection/rework event counts, previously always zero',
                         'json_sha256': hashlib.sha256(encoded).hexdigest(),
                         'query_count': len(queries), 'payload_json_bytes': len(encoded)})
if os.environ.get('ERP_A_TIMING') == '1':
    # Interleaved before/after, identical snapshot and interpreter, no cache
    # layer. A first request is not a cold-cache P95.
    samples = {'before': [], 'after': []}
    for index in range(100):
        pairs = [('before', namespace['QualitySummaryView']), ('after', views.QualitySummaryView)]
        if index % 2:
            pairs.reverse()
        for name, view in pairs:
            started = time.perf_counter()
            result = invoke(view.as_view(), '/api/quality/summary/', params)
            samples[name].append((time.perf_counter() - started) * 1000)
            assert without_daily_event_counts(result) == without_daily_event_counts(after)
    for name, times in samples.items():
        emit('SNAPSHOT_BENCH', {'stage': name, 'samples': 100, 'failures': 0,
                               'p50_ms': round(statistics.median(times), 2),
                               'p95_ms': round(sorted(times)[math.ceil(.95*len(times))-1], 2),
                               'max_ms': round(max(times), 2), 'all_except_daily_event_counts_equal': True})
emit('QUANTITY', {'quality': after['totals']['shipped_quantity'], 'analytics': factory_quantity,
                  'attribution': attribution,
                  'row_counts': {'orders': QualityOrder.objects.count(), 'batches': QualityShipmentBatch.objects.count(),
                                 'lines': QualityShipmentLine.objects.count(), 'returns': QualityReworkCase.objects.count()}})
sample = QualityShipmentBatch.objects.filter(shipment_no='QS-20261012-67FBA828').first()
if sample:
    preview = invoke(views.QualityReworkCaseViewSet.as_view({'get': 'return_allocation_preview'}),
                     '/api/quality/rework-cases/return-allocation-preview/',
                     {'shipment_batch_id': sample.pk, 'shipment_unit_no': 1})
    rows = preview['order_allocations']
    assert sorted((row['order_no'], row['item_no'], row['piece_quantity'], str(row['net_weight_kg'])) for row in rows) == [
        ('04-A001-2608290001', '8', 687, '0.101'),
        ('04-A001-2609140004', '89', 10417, '1.529'),
    ], rows
    emit('A3_PREVIEW', {'shipment_no': sample.shipment_no, 'unit_no': 1, 'rows': rows})
else:
    emit('A3_PREVIEW', {'status': 'SAMPLE_NOT_IN_SNAPSHOT'})
emit('A1_EVENTS', [{'employee_id': row['employee_id'], 'rework_records': row.get('rework_record_count'),
                    'rework_quantity': row.get('reworked_quantity')} for row in dashboard['quality_employee_performance']])
emit('DONE', {'read_only': True, 'historical_mutations': 0})
