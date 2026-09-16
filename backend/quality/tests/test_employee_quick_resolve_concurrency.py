from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.contrib.auth import get_user_model
from django.db import close_old_connections
from django.test import TransactionTestCase
from rest_framework.test import APIClient

from quality.models import QualityEmployee


class QualityEmployeeQuickResolveConcurrencyTests(TransactionTestCase):
    """Exercise separate SQLite connections, not two calls on one test client."""

    reset_sequences = True

    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(
            username="quality-quick-resolve-concurrency",
            password="quality-password",
        )

    def _resolve(self, barrier, *, name="并发品检", purpose=QualityEmployee.Role.INSPECTOR):
        close_old_connections()
        try:
            client = APIClient()
            client.force_authenticate(
                get_user_model().objects.get(pk=self.user.pk)
            )
            barrier.wait(timeout=10)
            response = client.post(
                "/api/quality/employees/quick-resolve/",
                {"name": name, "purpose": purpose},
                format="json",
            )
            return response.status_code, response.json()
        finally:
            close_old_connections()

    def test_concurrent_quick_resolve_requests_return_one_employee(self):
        barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(self._resolve, (barrier, barrier)))

        self.assertEqual(sorted(status_code for status_code, _ in results), [200, 201])
        self.assertEqual(len({payload["id"] for _, payload in results}), 1)
        resolve_key = QualityEmployee.normalize_quick_resolve_key("并发品检")
        self.assertEqual(
            QualityEmployee.objects.filter(quick_resolve_key=resolve_key).count(),
            1,
        )

    def test_concurrent_requests_claim_one_existing_manual_employee(self):
        existing = QualityEmployee.objects.create(
            employee_no="QC-MANUAL-RACE",
            name="并发认领员工",
            role=QualityEmployee.Role.REWORKER,
        )
        barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(
                    self._resolve,
                    barrier,
                    name="并发认领员工",
                    purpose=purpose,
                )
                for purpose in (
                    QualityEmployee.Role.INSPECTOR,
                    QualityEmployee.Role.REWORKER,
                )
            ]
            results = [future.result() for future in futures]

        self.assertEqual([status_code for status_code, _ in results], [200, 200])
        self.assertEqual({payload["id"] for _, payload in results}, {existing.pk})
        existing.refresh_from_db()
        self.assertEqual(existing.role, QualityEmployee.Role.BOTH)
        self.assertEqual(
            existing.quick_resolve_key,
            QualityEmployee.normalize_quick_resolve_key("并发认领员工"),
        )
