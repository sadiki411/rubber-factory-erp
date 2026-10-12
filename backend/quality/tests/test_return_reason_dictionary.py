from importlib import import_module
from types import SimpleNamespace

from django.apps import apps
from django.db import connection
from django.test import TestCase

from quality.models import DefectReason
from .helpers import QualityTestMixin, response_results


class ReturnReasonDictionaryTests(QualityTestMixin, TestCase):
    def test_requested_reasons_are_available_in_active_api(self):
        response = self.client.get("/api/quality/defect-reasons/", {"active": "true", "page_size": 200})
        self.assertEqual(response.status_code, 200, response.content)
        reasons = response_results(response)
        for name in ("拔破", "空洞", "脏", "变形"):
            with self.subTest(name=name):
                matching = [reason for reason in reasons if reason["name"] == name]
                self.assertEqual(len(matching), 1)
                self.assertTrue(matching[0]["is_active"])

    def test_migration_is_idempotent_and_preserves_existing_reason_identity(self):
        seed = import_module("quality.migrations.0017_add_return_defect_reasons").add_return_reasons
        deformation = DefectReason.objects.get(code="DEFORMATION")
        deformation.notes = "保留原有说明"
        deformation.is_active = False
        deformation.save()
        count = DefectReason.objects.count()
        editor = SimpleNamespace(connection=connection)
        seed(apps, editor)
        seed(apps, editor)
        deformation.refresh_from_db()
        self.assertTrue(deformation.is_active)
        self.assertEqual(deformation.notes, "保留原有说明")
        self.assertEqual(DefectReason.objects.count(), count)
        self.assertEqual(DefectReason.objects.get(name="变形").pk, deformation.pk)

    def test_migration_reuses_manual_reason_with_the_same_name(self):
        seed = import_module("quality.migrations.0017_add_return_defect_reasons").add_return_reasons
        reason = DefectReason.objects.get(code="VOID")
        reason.code = "MANUAL-VOID"
        reason.is_system = False
        reason.save()
        seed(apps, SimpleNamespace(connection=connection))
        self.assertEqual(DefectReason.objects.filter(name="空洞").count(), 1)
        self.assertEqual(DefectReason.objects.get(name="空洞").pk, reason.pk)
        self.assertFalse(DefectReason.objects.filter(code="VOID").exists())
