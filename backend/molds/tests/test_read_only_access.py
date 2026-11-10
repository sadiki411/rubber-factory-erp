from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from rest_framework.test import APIClient

from erp.permissions import READ_ONLY_GROUP_NAME
from django.apps import apps
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.schemas.generators import EndpointEnumerator
import re


class ReadOnlyAccessTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="gpt-readonly-test",
            password="test-password",
        )
        self.user.groups.add(Group.objects.create(name=READ_ONLY_GROUP_NAME))
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_session_marks_account_as_read_only(self):
        response = self.client.get("/api/auth/session/")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["authenticated"])
        self.assertTrue(response.json()["user"]["read_only"])

    def test_read_only_account_can_read_business_data(self):
        response = self.client.get("/api/molds/")

        self.assertEqual(response.status_code, 200)

    def test_read_only_account_cannot_create_update_or_delete(self):
        create_response = self.client.post(
            "/api/processors/",
            {"code": "DENIED", "name": "不应创建"},
            format="json",
        )
        update_response = self.client.patch(
            "/api/processors/999999/",
            {"name": "不应修改"},
            format="json",
        )
        delete_response = self.client.delete("/api/processors/999999/")

        for response in (create_response, update_response, delete_response):
            self.assertEqual(response.status_code, 403)
            self.assertIn("只读测试账号", response.json()["detail"])

    def test_normal_authenticated_account_keeps_write_access(self):
        normal_user = get_user_model().objects.create_user(username="normal")
        self.client.force_authenticate(normal_user)

        response = self.client.post(
            "/api/processors/",
            {"code": "NORMAL", "name": "正常加工方"},
            format="json",
        )

        self.assertEqual(response.status_code, 201)

    def test_every_business_write_route_denies_read_only_before_loading_or_mutating(self):
        # Enumerate the actual router/action registry, including imports, void,
        # corrections, counters, yields and inventory transitions. Isolated DB.
        endpoints = EndpointEnumerator().get_api_endpoints()
        writes = [(path, method) for path, method, _ in endpoints
                  if path.startswith('/api/') and not path.startswith('/api/auth/')
                  and method in {'POST', 'PUT', 'PATCH', 'DELETE'}]
        self.assertGreater(len(writes), 100)
        before = {model._meta.label: list(model.objects.order_by('pk').values())
                  for model in apps.get_models()}
        with CaptureQueriesContext(connection) as queries:
            for path, method in writes:
                target = re.sub(r'\{[^}]+\}', '999999', path)
                target = re.sub(r'<[^>]+>', '999999', target)
                # UUID paths still need a syntactically valid route to reach auth.
                if '/imports/' in target or '/ledger-imports/' in target:
                    target = target.replace('/999999/', '/00000000-0000-0000-0000-000000000001/')
                with self.subTest(method=method, path=target):
                    response = self.client.generic(method, target, '{}', content_type='application/json')
                    self.assertEqual(response.status_code, 403, response.content)
                    self.assertIn('只读测试账号', response.json()['detail'])
        mutations = [q['sql'] for q in queries if q['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE'))]
        self.assertEqual(mutations, [])
        after = {model._meta.label: list(model.objects.order_by('pk').values())
                 for model in apps.get_models()}
        self.assertEqual(after, before)
