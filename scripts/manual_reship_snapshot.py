"""Read-only stage evidence from the one new synthetic UI database only."""
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
run = sys.argv[3] if len(sys.argv) > 3 else ''
if run not in ('', '-verified', '-recording'):
    raise SystemExit('Unsupported isolated review run')
DB = ROOT / f'.codex_tmp/manual-reship-20261119{run}.sqlite3'
OUT = ROOT / 'output/manual-reship-20261119' / (run.lstrip('-') or 'first-run')
tag, stage = sys.argv[1:3]
if tag not in ('SCAN', 'MANUAL') or stage not in ('initial', 'first-100', 'returned-0', 'reshipped-100'):
    raise SystemExit('Invalid synthetic evidence stage')
db = sqlite3.connect(DB.as_uri() + '?mode=ro', uri=True)
db.row_factory = sqlite3.Row
db.execute('PRAGMA query_only=ON')
def rows(sql, params=()):
    return [dict(row) for row in db.execute(sql, params)]
orders = rows('SELECT id,order_no,item_no,order_quantity,status FROM quality_qualityorder ORDER BY id')
if len(orders) != 4 or any(not o['order_no'].startswith('SYNTH-') for o in orders):
    raise SystemExit('Refuse anything except this new four-order synthetic fixture')
ids = [o['id'] for o in orders if o['order_no'].startswith(f'SYNTH-{tag}-')]
marks = ','.join('?' for _ in ids)
allocations = rows(f'''SELECT a.*,b.shipment_no,b.status,b.shipment_date FROM quality_qualityshipmentorderallocation a
    JOIN quality_qualityshipmentline l ON l.id=a.shipment_line_id
    JOIN quality_qualityshipmentbatch b ON b.id=l.batch_id WHERE a.order_id IN ({marks}) ORDER BY a.id''', ids)
returns = rows(f'''SELECT a.* FROM quality_qualityreturnallocation a
    JOIN quality_qualityshipmentorderallocation s ON s.id=a.shipment_order_allocation_id
    WHERE s.order_id IN ({marks}) ORDER BY a.id''', ids)
cases = rows(f'''SELECT c.id,c.case_no,c.status,c.process_card_id,c.return_round,c.is_current_return,
    c.shipment_batch_id,c.shipment_unit_no,c.reshipment_batch_id,c.affected_quantity,c.affected_weight_kg
    FROM quality_qualityreworkcase c WHERE c.shipment_batch_id IN
    (SELECT l.batch_id FROM quality_qualityshipmentline l JOIN quality_qualityshipmentorderallocation a
    ON a.shipment_line_id=l.id WHERE a.order_id IN ({marks})) ORDER BY c.id''', ids)
balances = {str(i): 0 for i in ids}
for a in allocations:
    if a['status'] == 'CONFIRMED':
        balances[str(a['order_id'])] += a['piece_quantity']
source_orders = {a['id']: a['order_id'] for a in allocations}
active_cases = {c['id'] for c in cases if c['status'] != 'CANCELLED'}
for r in returns:
    if r['case_id'] in active_cases:
        balances[str(source_orders[r['shipment_order_allocation_id']])] -= r['piece_quantity']
data = {'synthetic_only': True, 'captured_at': datetime.now(timezone.utc).isoformat(), 'tag': tag, 'stage': stage,
        'orders': [o for o in orders if o['id'] in ids], 'net_by_order': balances, 'net_total': sum(balances.values()),
        'shipment_allocations': allocations, 'return_allocations': returns, 'return_cases': cases,
        'return_allocation_sha256': hashlib.sha256(json.dumps(returns, sort_keys=True).encode()).hexdigest(),
        'bindings': rows('SELECT * FROM quality_processcardunitbinding ORDER BY id'),
        'batch_inspectors': rows('SELECT * FROM quality_qualityshipmentbatch_inspectors ORDER BY id')}
expected = 0 if stage in ('initial', 'returned-0') else 100
assert data['net_total'] == expected, data
OUT.mkdir(parents=True, exist_ok=True)
(OUT / f'{tag.lower()}-{stage}.json').write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({'tag': tag, 'stage': stage, 'net_total': data['net_total'], 'net_by_order': balances,
                  'returns': len(cases), 'return_allocation_sha256': data['return_allocation_sha256']}))
