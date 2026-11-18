"""Add labelled API fixtures to the existing LOCAL browser-only synthetic DB.

This is not browser interaction evidence, and refuses all other DBs/orders.
"""
import json
import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
db = root / '.codex_tmp/flow-review-rv2.sqlite3'
assert db.is_file() and '--disposable-browser-fixture' in sys.argv
os.environ.update(SQLITE_PATH=str(db), FLOW_CARDS_WRITE_ENABLED='1', DJANGO_SETTINGS_MODULE='erp.settings',
                  FLOW_CARDS_PRIVATE_ROOT=str(root / '.codex_tmp/flow-review-rv2.private'))
sys.path.insert(0, str(root / 'backend'))
import django
django.setup()
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from flowcards.models import InternalCard, CardEvent
from quality.models import QualityOrder, QualityEmployee
from quality.services import delivered_quantities_by_order

order = QualityOrder.objects.get(pk=5, order_no='RV-BROWSER-ORDER', material='ISOLATED')
user = get_user_model().objects.get(username='flow-review')
assert not user.is_staff and not user.is_superuser
cards = list(InternalCard.objects.filter(group__orders=order).order_by('sequence'))
assert len(cards) == 2
employee = QualityEmployee.objects.get(employee_no='RV-01')
assert not order.quality_shipment_batches.exists(), 'Refuse duplicate fixture'
client = APIClient(HTTP_HOST='localhost'); client.force_authenticate(user)

def post(path, body, status):
    response = client.post('/api/quality/' + path, body, format='json')
    assert response.status_code == status, (path, response.status_code, response.content)
    return response.json()

draft = post('shipment-batches/', {'order_id': order.pk, 'unit_weight_g': '10',
    'single_batch_net_weight_kg': '1', 'process_card_shipment_quantity': 100, 'product_batch_count': 1,
    'inspector_ids': [employee.pk], 'lines': [{'order_id': order.pk}]}, 201)
bid = draft['id']
post(f'shipment-batches/{bid}/confirm/', {'internal_sources': [
    {'card_id': str(c.pk), 'request_key': 'rv-browser-source-' + str(c.pk),
     'scope': 'SET_UNKNOWN', 'evidence': 'API预置隔离来源集合；不代表页面点击或精确份额'} for c in cards]}, 200)
before = delivered_quantities_by_order([order.pk])[order.pk]
returned = post('rework-cases/', {'origin': 'CUSTOMER_RETURN', 'shipment_batch_id': bid, 'shipment_unit_no': 1,
    'reason': '隔离API退回，不是正式生产', 'inspector_ids': [employee.pk]}, 201)
after = delivered_quantities_by_order([order.pk])[order.pk]
evidence = {'synthetic_only': True, 'created_by': 'API_FIXTURE_NOT_BROWSER', 'order_id': order.pk,
    'card_ids': [str(c.pk) for c in cards], 'batch_id': bid, 'shipment_no': draft['shipment_no'],
    'return_id': returned['id'], 'return_no': returned['case_no'], 'net_before_return': str(before),
    'net_after_return': str(after), 'events': list(CardEvent.objects.filter(cards__in=cards).distinct().values('id','kind','business_at'))}
path = root / 'docs/flowcards-20261118/rv-evidence/browser-fixture.json'
path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
print(json.dumps(evidence, ensure_ascii=False, default=str))
