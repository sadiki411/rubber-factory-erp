"""Two incremental, clearly synthetic samples; refuses any existing database."""
import argparse
import hashlib
import json
import os
import secrets
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser()
p.add_argument('--database', default=str(ROOT / '.codex_tmp/flow-second-review.sqlite3'))
args = p.parse_args()
db = Path(args.database).resolve()
if db.exists() or db.suffix != '.sqlite3' or db.parent != (ROOT / '.codex_tmp').resolve():
    raise SystemExit('Refusing existing/non-disposable database')
db.parent.mkdir(parents=True, exist_ok=True)
os.environ.update(SQLITE_PATH=str(db), FLOW_CARDS_WRITE_ENABLED='1', DJANGO_SETTINGS_MODULE='erp.settings', FLOW_CARDS_PRIVATE_ROOT=str(db.with_suffix('.private')))
sys.path.insert(0, str(ROOT / 'backend'))
import django
django.setup()
from django.core.management import call_command
from django.contrib.auth import get_user_model
from orders.models import ProductSpecification
from quality.models import QualityOrder, QualityEmployee
from flowcards import services as s
from flowcards.models import AppearanceIssue
from rest_framework.test import APIClient

call_command('migrate', interactive=False, verbosity=0)
user = get_user_model().objects.create_user('flow-review', password=os.getenv('FLOW_REVIEW_LOCAL_PASSWORD') or secrets.token_urlsafe(32))
employee = QualityEmployee.objects.create(employee_no='SECOND-01', name='隔离作业员', role='BOTH')
api = APIClient(HTTP_HOST='localhost'); api.force_authenticate(user)
appearance = list(AppearanceIssue.objects.values_list('pk', flat=True))[:4]
out = ROOT / 'output/pdf'; out.mkdir(parents=True, exist_ok=True)
evidence = {'synthetic_only': True, 'renderer': s.RENDERER, 'employee_id': employee.pk, 'database': str(db), 'samples': []}
for index, extra_id in enumerate([False, True], 1):
    product = ProductSpecification.objects.create(specification='100×3', material='DEMO', customer_product_no=f'ISOLATED-{index}', product_name='隔离示例，非客户参数')
    order = QualityOrder.objects.create(order_no=f'SYNTH-ONLY-{index}', item_no='01', specification=product.specification, material=product.material,
        product_specification=product, product_name=product.product_name, source_system='翔邦', order_quantity=2000, created_by=user)
    rows = [{'name': name, 'stage': stage, 'lower': low, 'upper': high, 'unit': unit, 'method': '隔离已确认示例测法（非客户要求）', 'limit_mode': mode, 'upper_inclusive': True}
        for name, stage, low, high, unit, mode in [('上模温度', 'UPPER_TEMP', '170', '180', '℃', 'RANGE'), ('下模温度', 'LOWER_TEMP', '168', '178', '℃', 'RANGE'),
            ('成型硬度', 'MOLDING_HARDNESS', '65', '75', '示例量表', 'RANGE'), ('烤后硬度', 'BAKED_HARDNESS', '66', '74', '示例量表', 'RANGE'), ('线径', 'DIMENSION', '', '3.05', 'mm', 'UPPER')]]
    if extra_id:
        rows.append({'name': '内径', 'stage': 'DIMENSION', 'lower': '99.8', 'upper': '100.2', 'unit': 'mm', 'method': '隔离客户例外加测，非实际公差', 'limit_mode': 'RANGE'})
    st = s.confirm_standard({'request_key': uuid.uuid4().hex, 'product_id': product.pk, 'source': '隔离样张，所有数值仅验软件，不是客户参数；' + ('模拟客户明确加测内径' if extra_id else '模拟常规O圈仅测线径上限'),
        'appearance_ids': appearance, 'dimension_count': 2 if extra_id else 1, 'criteria': rows, 'whole_mold': 'REQUIRED' if extra_id else 'NOT_REQUIRED',
        'bake': {'state': 'APPLICABLE', 'temperature_lower': 150, 'temperature_upper': 160, 'minutes_lower': 120, 'minutes_upper': 150},
        'hardness': {'MOLDING_HARDNESS': {'object': 'PRODUCT', 'scale': '示例量表'}, 'BAKED_HARDNESS': {'object': 'COUPON', 'scale': '示例量表'}}}, user)
    group = s.generate_group(s.create_group({'request_key': uuid.uuid4().hex, 'product_id': product.pk, 'order_ids': [order.pk], 'standard_id': st.pk,
        'planned_molds': 100, 'molds_per_card': 100, 'whole_mold': st.whole_mold, 'whole_mold_basis': '隔离明确选择，不对应实际客户',
        'plan_basis': '隔离示例，仅审阅纸样，不计生产数量'}, user), user)
    card = group.cards.get()
    response = api.post('/api/flow-cards/print-jobs/', {'request_key': uuid.uuid4().hex, 'card_ids': [str(card.pk)]}, format='json')
    if response.status_code != 201: raise RuntimeError(response.content)
    job = response.json(); response = api.get(job['pdf_url'])
    raw = b''.join(response.streaming_content); response.close()
    tag = '客户例外_线径加内径' if extra_id else '常规O型圈_仅线径上限'
    path = out / f'工序流转卡_二轮_{tag}.pdf'; path.write_bytes(raw)
    evidence['samples'].append({'tag': tag, 'product_id': product.pk, 'order_id': order.pk, 'standard_id': st.pk, 'card_id': str(card.pk), 'card_no': card.card_no,
        'group_id': str(group.pk), 'print_job_id': job['id'], 'pdf': str(path), 'sha256': hashlib.sha256(raw).hexdigest()})
path = db.with_suffix('.json'); path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(evidence, ensure_ascii=False, indent=2))
