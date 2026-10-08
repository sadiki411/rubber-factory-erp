from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from rest_framework.test import APIClient

from erp.permissions import READ_ONLY_GROUP_NAME


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
