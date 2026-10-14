from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
import time
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import OperationalError, close_old_connections, connections
from django.test import TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from analytics.services import build_dashboard
from quality.models import (
    DefectReason, ProcessCard, ProcessCardUnitBinding, QualityEmployee, QualityOrder,
    QualityReturnAllocation, QualityReworkCase, QualityShipmentBatch,
    QualityShipmentLine, QualityShipmentOrderAllocation,
)
from quality.services import delivered_quantities_by_order, is_sqlite_lock_error
from quality.views import QualityWriteBusy, quality_write_retry


class QualityConcurrentReturnTests(TransactionTestCase):
    """Isolation evidence for two clients submitting the same physical unit.

    Uses the runner's isolated SQLite test database (shared memory by default).
    A barrier releases the HTTP clients together. SQLite may serialize writers;
    this is not evidence about production filesystem or network lock latency.
    """

    reset_sequences = True

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="concurrent-quality")
        self.employee = QualityEmployee.objects.create(
            employee_no="QC-CONCURRENT", name="并发测试品检", role=QualityEmployee.Role.INSPECTOR
        )
        self.order = QualityOrder.objects.create(
            order_no="CONCURRENT-ORDER", item_no="1", product_name="测试产品",
            specification="240", material="N7200", order_quantity=1000,
            order_date=timezone.localdate(), created_by=self.user,
        )
        self.batch = QualityShipmentBatch.objects.create(
            shipment_no="CONCURRENT-SHIPMENT", shipment_date=timezone.localdate(),
            status=QualityShipmentBatch.Status.CONFIRMED, order=self.order,
            inspector=self.employee, unit_weight_g=Decimal("1.00000"),
            single_batch_net_weight_kg=Decimal("1.000"), product_batch_count=1,
            pieces_per_batch=1000, created_by=self.user,
        )
        self.batch.inspectors.add(self.employee)
        QualityShipmentLine.objects.create(
            batch=self.batch, order=self.order, net_weight_kg=Decimal("1.000"),
            piece_quantity=1000, unit_weight_g_snapshot=Decimal("1.00000"),
            single_batch_net_weight_kg=Decimal("1.000"), process_card_shipment_quantity=1000,
            product_batch_count=1, pieces_per_batch=1000,
            specification_snapshot="240", material_snapshot="N7200",
        )
        self._allocation(self.batch)
        self._bind(self.batch, "CONCURRENT-CARD-1")

    def _allocation(self, batch):
        line = batch.lines.get()
        QualityShipmentOrderAllocation.objects.create(
            shipment_line=line, order=batch.order, sequence=1, piece_start=0,
            piece_end=1000, piece_quantity=1000, net_weight_kg=Decimal("1.000"),
            order_no_snapshot=batch.order.order_no, item_no_snapshot="1",
            specification_snapshot="240", material_snapshot="N7200",
        )

    def _bind(self, batch, code):
        card = ProcessCard.objects.create(
            card_no=code, order=batch.order, quantity=1000,
            unit_weight_g=Decimal("1.00000"), status=ProcessCard.Status.SHIPPED,
            created_by=self.user,
        )
        ProcessCardUnitBinding.objects.create(
            process_card=card, shipment_batch=batch, shipment_unit_no=1,
            piece_quantity=1000, net_weight_kg=Decimal("1.000"),
            created_by=self.user,
        )

    def _second_batch(self, status=QualityShipmentBatch.Status.CONFIRMED):
        order = QualityOrder.objects.create(
            order_no="CONCURRENT-ORDER-2", item_no="1", product_name="测试产品",
            specification="240", material="N7200", order_quantity=1000,
            order_date=timezone.localdate(), created_by=self.user,
        )
        batch = QualityShipmentBatch.objects.create(
            shipment_no="CONCURRENT-SHIPMENT-2", shipment_date=timezone.localdate(),
            status=status, order=order, inspector=self.employee,
            unit_weight_g=Decimal("1.00000"), single_batch_net_weight_kg=Decimal("1.000"),
            product_batch_count=1, pieces_per_batch=1000, created_by=self.user,
        )
        batch.inspectors.add(self.employee)
        QualityShipmentLine.objects.create(
            batch=batch, order=order, net_weight_kg=Decimal("1.000"), piece_quantity=1000,
            unit_weight_g_snapshot=Decimal("1.00000"),
            single_batch_net_weight_kg=Decimal("1.000"), process_card_shipment_quantity=1000,
            product_batch_count=1, pieces_per_batch=1000,
            specification_snapshot="240", material_snapshot="N7200",
        )
        if status == QualityShipmentBatch.Status.CONFIRMED:
            self._allocation(batch)
            self._bind(batch, "CONCURRENT-CARD-2")
        return batch

    def _submit(self, barrier, path, payload):
        close_old_connections()
        client = APIClient()
        client.force_authenticate(self.user)
        try:
            barrier.wait(timeout=10)
            started = time.perf_counter()
            response = client.post(path, payload, format="json")
            print(f"CONCURRENCY path={path} status={response.status_code} elapsed_ms={(time.perf_counter()-started)*1000:.2f} error={response.data if response.status_code >= 400 else '-'}")
            return response.status_code
        finally:
            connections.close_all()

    def _race(self, requests):
        barrier = Barrier(len(requests))
        with patch("quality.views.is_sqlite_lock_error", wraps=is_sqlite_lock_error) as locks:
            with ThreadPoolExecutor(max_workers=len(requests)) as pool:
                futures = [pool.submit(self._submit, barrier, path, data) for path, data in requests]
                statuses = sorted(future.result(timeout=30) for future in futures)
            print(f"CONCURRENCY statuses={statuses} lock_errors_checked={locks.call_count}")
        return statuses

    def _return_payload(self, batch):
        return {"origin": "CUSTOMER_RETURN", "shipment_batch_id": batch.pk,
                "shipment_unit_no": 1, "reason_category": "APPEARANCE",
                "reason": "隔离并发测试"}

    def _assert_return_totals(self, expected):
        cases = QualityReworkCase.objects.exclude(status=QualityReworkCase.Status.CANCELLED)
        self.assertEqual(cases.count(), expected)
        self.assertEqual(QualityReturnAllocation.objects.count(), expected)
        balances = delivered_quantities_by_order(QualityOrder.objects.values_list("pk", flat=True))
        confirmed = QualityShipmentBatch.objects.filter(status="CONFIRMED").count()
        self.assertEqual(sum(balances.values()), (confirmed - expected) * 1000)
        today = timezone.localdate()
        dashboard = build_dashboard(date_from=today, date_to=today)
        self.assertEqual(dashboard["quality"]["total"]["returned_quantity"], expected * 1000)
        self.assertEqual(sum(row["responsible_return_quantity"] for row in dashboard["quality_employee_performance"]), expected * 1000)

    def test_same_physical_unit_concurrent_submits_once(self):
        request = ("/api/quality/rework-cases/", self._return_payload(self.batch))
        self.assertEqual(self._race([request, request]), [201, 400])
        self._assert_return_totals(1)

    def test_same_card_bulk_concurrent_submits_once(self):
        request = ("/api/quality/rework-cases/bulk-scan-return/",
                   {"cards": [{"card_no": "CONCURRENT-CARD-1"}], "reason": "隔离并发退货"})
        statuses = self._race([request, request])
        self.assertEqual(statuses.count(201), 1)
        # A bounded SQLite collision may report explicit not-committed 503,
        # rather than reach the business duplicate check within the retry
        # budget. It must remain safe to retry once writers have finished.
        self.assertIn(next(status for status in statuses if status != 201), (400, 503))
        client = APIClient()
        client.force_authenticate(self.user)
        retry = client.post(*request, format="json")
        self.assertEqual(retry.status_code, 400, retry.content)
        self._assert_return_totals(1)

    def test_different_physical_units_concurrent_returns(self):
        other = self._second_batch()
        self.assertEqual(self._race([
            ("/api/quality/rework-cases/", self._return_payload(self.batch)),
            ("/api/quality/rework-cases/", self._return_payload(other)),
        ]), [201, 201])
        self._assert_return_totals(2)

    def test_different_batches_concurrent_confirmation(self):
        self.batch.status = QualityShipmentBatch.Status.DRAFT
        self.batch.save(update_fields=["status"])
        QualityShipmentOrderAllocation.objects.filter(shipment_line__batch=self.batch).delete()
        other = self._second_batch(QualityShipmentBatch.Status.DRAFT)
        statuses = self._race([
            (f"/api/quality/shipment-batches/{batch.pk}/confirm/", {})
            for batch in (self.batch, other)
        ])
        self.assertEqual(statuses, [200, 200])
        self.assertEqual(QualityShipmentBatch.objects.filter(status="CONFIRMED").count(), 2)
        self.assertEqual(sum(delivered_quantities_by_order([self.order.pk, other.order_id]).values()), 2000)
        self.assertEqual(QualityShipmentOrderAllocation.objects.count(), 2)

    def test_bulk_validation_failure_rolls_back_earlier_card(self):
        client = APIClient()
        client.force_authenticate(self.user)
        response = client.post("/api/quality/rework-cases/bulk-scan-return/", {
            "cards": [{"card_no": "CONCURRENT-CARD-1"}, {"card_no": "UNKNOWN-CARD"}],
            "reason": "验证失败应整批回滚",
        }, format="json")
        self.assertEqual(response.status_code, 400, response.content)
        self._assert_return_totals(0)

    def test_scanned_return_freezes_primary_reason_and_inspector_in_one_validated_save(self):
        client = APIClient()
        client.force_authenticate(self.user)
        reason = DefectReason.objects.get(code="OTHER")
        original = QualityReworkCase.full_clean
        with patch.object(QualityReworkCase, "full_clean", autospec=True, side_effect=original) as validation:
            response = client.post("/api/quality/rework-cases/scan-return/", {
                "card_no": "CONCURRENT-CARD-1", "primary_reason_id": reason.pk,
                "reason": "单次完整校验，责任仍沿原出货",
            }, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(validation.call_count, 1)
        case = QualityReworkCase.objects.get(pk=response.data["id"])
        self.assertEqual(case.primary_reason_id, reason.pk)
        self.assertEqual(case.responsible_inspector_id, self.employee.pk)
        self.assertEqual(list(case.inspectors.values_list("pk", flat=True)), [self.employee.pk])
        self._assert_return_totals(1)

    def test_retry_rolls_back_whole_transaction_before_replaying(self):
        attempts = []

        @quality_write_retry
        def operation():
            attempts.append(1)
            QualityOrder.objects.filter(pk=self.order.pk).update(notes="transaction test")
            if len(attempts) == 1:
                raise OperationalError("database is locked")
            return QualityOrder.objects.get(pk=self.order.pk).notes

        with patch("quality.views.time.sleep"):
            self.assertEqual(operation(), "transaction test")
        self.assertEqual(len(attempts), 2)

    def test_exhausted_lock_is_503_and_does_not_commit(self):
        @quality_write_retry
        def operation():
            QualityOrder.objects.filter(pk=self.order.pk).update(notes="must rollback")
            raise OperationalError("database table is locked")

        with patch("quality.views.time.sleep"), self.assertRaises(QualityWriteBusy) as caught:
            operation()
        self.assertEqual(caught.exception.status_code, 503)
        self.order.refresh_from_db()
        self.assertNotEqual(self.order.notes, "must rollback")
