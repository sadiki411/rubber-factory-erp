from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient
from erp.permissions import READ_ONLY_GROUP_NAME
from production.models import ProductionRun, ProductionDailyLog


class CompactHistoryTests(TestCase):
    def test_compact_list_is_paginated_read_only_and_never_serializes_all_logs(self):
        user = get_user_model().objects.create_user(username='compact-reader')
        user.groups.add(Group.objects.get_or_create(name=READ_ONLY_GROUP_NAME)[0])
        client = APIClient()
        client.force_authenticate(user)
        for index in range(12):
            run = ProductionRun.objects.create(order_no=f'HISTORY-{index}', specification='TEST', material='N7200', cavities=10, planned_mold_count=10, order_quantity=100, status='COMPLETED', is_ledger_only=True, created_by=user)
            ProductionDailyLog.objects.create(run=run, produced_mold_count=10, cavities_snapshot=10)
        with CaptureQueriesContext(connection) as queries:
            response = client.get('/api/production/runs/history-compact/', {'page_size': 10, 'status': 'COMPLETED', 'is_ledger_only': True})
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        self.assertEqual(data['count'], 12)
        self.assertEqual(len(data['results']), 10)
        self.assertLessEqual(len(queries), 4)
        self.assertFalse(any(q['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE')) for q in queries))
        for row in data['results']:
            self.assertNotIn('daily_logs', row)
            self.assertEqual(row['production_record_count'], 1)
            self.assertEqual(row['recorded_mold_count'], 10)
        second = client.get('/api/production/runs/history-compact/', {'page_size': 10, 'page': 2}).json()
        self.assertEqual(len(second['results']), 2)
        self.assertFalse({x['id'] for x in data['results']} & {x['id'] for x in second['results']})
        pk = second['results'][0]['id']
        detail = client.get(f'/api/production/runs/{pk}/')
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(len(detail.json()['daily_logs']), 1)
