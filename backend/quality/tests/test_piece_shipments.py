from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from unittest.mock import patch
import uuid

from django.contrib.auth import get_user_model
from django.db import IntegrityError, close_old_connections, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from quality.models import (
    ProcessCard, QualityEmployee, QualityOrder, QualityShipment,
    QualityShipmentBatch, QualityShipmentLine, ReturnRework,
)
from quality.serializers import QualityShipmentSerializer
from quality.services import delivered_quantities_by_order
from orders.models import BusinessRecordRevision


class PieceShipmentSetup:
    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(username="piece-shipping")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.order = QualityOrder.objects.create(
            order_no="ASL-PO-001", item_no="10", product_name="包装完成产品",
            specification="20x30", order_quantity=100, source_system="艾斯霖",
            created_by=self.user,
        )

    def shipment_payload(self, number="PIECE-1", quantity=100, order=None):
        return {
            "client_request_id": str(uuid.uuid4()),
            "shipment_no": number, "order_id": (order or self.order).pk,
            "shipment_date": timezone.localdate().isoformat(),
            "inspection_quantity": quantity, "qualified_quantity": quantity,
            "defective_quantity": 0, "shipped_quantity": quantity,
        }

    def return_payload(self, shipment_id, quantity=20):
        return {
            "client_request_id": str(uuid.uuid4()),
            "shipment_id": shipment_id, "rework_date": timezone.localdate().isoformat(),
            "reason_category": "PACKAGING", "reason": "包装退回",
            "returned_quantity": quantity,
        }

    def ship(self, number="PIECE-1", quantity=100, order=None):
        response = self.client.post(
            "/api/quality/shipments/", self.shipment_payload(number, quantity, order), format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        return response.data["id"]

    def returned(self, shipment_id, quantity=20):
        response = self.client.post(
            "/api/quality/reworks/", self.return_payload(shipment_id, quantity), format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        return response.data["id"]

    def balance(self):
        return delivered_quantities_by_order([self.order.pk])[self.order.pk]


class PieceShipmentTests(PieceShipmentSetup, TestCase):
    def purchase_candidates(self):
        response = self.client.get('/api/quality/shipments/candidates/', {
            'q': self.order.order_no, 'page_size': 1000,
        })
        self.assertEqual(response.status_code, 200, response.data)
        return {row['order_id']: row for row in response.data['results']}

    def test_purchase_candidates_follow_partial_full_return_and_reship_balance(self):
        self.assertEqual(self.purchase_candidates()[self.order.pk]['remaining_quantity'], 100)
        self.ship(quantity=60)
        self.assertEqual(self.purchase_candidates()[self.order.pk]['remaining_quantity'], 40)
        second = self.ship('SECOND', 40)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, QualityOrder.Status.COMPLETED)
        self.assertNotIn(self.order.pk, self.purchase_candidates())
        self.returned(second, 20)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, QualityOrder.Status.OPEN)
        self.assertEqual(self.purchase_candidates()[self.order.pk]['remaining_quantity'], 20)
        self.ship('AFTER-RETURN', 20)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, QualityOrder.Status.COMPLETED)
        self.assertNotIn(self.order.pk, self.purchase_candidates())

    def test_purchase_candidates_exclude_full_balance_even_if_status_is_stale(self):
        self.ship()
        QualityOrder.objects.filter(pk=self.order.pk).update(status=QualityOrder.Status.OPEN)
        self.assertNotIn(self.order.pk, self.purchase_candidates())
        self.assertEqual(QualityOrder.objects.get(pk=self.order.pk).status, QualityOrder.Status.OPEN)
        # The read endpoint must not repair historical statuses as a side effect.

    def test_purchase_candidates_exclude_manually_completed_and_cancelled_orders(self):
        for status in (QualityOrder.Status.COMPLETED, QualityOrder.Status.CANCELLED):
            QualityOrder.objects.filter(pk=self.order.pk).update(status=status)
            self.assertNotIn(self.order.pk, self.purchase_candidates())

    def test_purchase_candidate_balance_refreshes_after_shipment_and_return_corrections(self):
        shipment_id = self.ship()
        return_id = self.returned(shipment_id, 20)
        self.assertEqual(self.purchase_candidates()[self.order.pk]['remaining_quantity'], 20)
        response = self.client.patch(f'/api/quality/reworks/{return_id}/', {'returned_quantity': 30})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.purchase_candidates()[self.order.pk]['remaining_quantity'], 30)
        response = self.client.patch(f'/api/quality/shipments/{shipment_id}/', {'shipped_quantity': 90})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.purchase_candidates()[self.order.pk]['remaining_quantity'], 40)

    def test_system_generates_unique_piece_numbers_when_omitted_or_blank(self):
        numbers = []
        for blank in (False, True):
            payload = self.shipment_payload(quantity=10)
            payload.pop("shipment_no")
            if blank:
                payload["shipment_no"] = ""
            response = self.client.post("/api/quality/shipments/", payload)
            self.assertEqual(response.status_code, 201, response.data)
            numbers.append(response.data["shipment_no"])
        self.assertTrue(all(number.startswith("QS-COUNT-") for number in numbers))
        self.assertNotEqual(*numbers)

    def test_analytics_displays_neutral_piece_shipment_name(self):
        employee = QualityEmployee.objects.create(employee_no="P-1", name="实录人员", role="INSPECTOR")
        payload = self.shipment_payload()
        payload["inspector_id"] = employee.pk
        response = self.client.post("/api/quality/shipments/", payload)
        self.assertEqual(response.status_code, 201, response.data)
        response = self.client.get("/api/analytics/quality-employee-details/", {
            "quality_employee_id": employee.pk,
            "date_from": timezone.localdate().isoformat(),
            "date_to": timezone.localdate().isoformat(),
        })
        self.assertEqual(response.status_code, 200, response.data)
        self.assertContains(response, "件数出货")
        self.assertNotContains(response, "历史出货")

    def test_direct_ship_return_reship_without_employees_cards_or_weight(self):
        shipment_id = self.ship()
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "COMPLETED")
        self.returned(shipment_id, 30)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "OPEN")
        self.assertEqual(self.balance(), 70)
        self.ship("PIECE-RESHIP", 30)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "COMPLETED")
        self.assertEqual(self.balance(), 100)
        self.assertEqual(QualityEmployee.objects.count(), 0)
        self.assertEqual(ProcessCard.objects.count(), 0)
        self.assertEqual(QualityShipmentBatch.objects.count(), 0)
        self.assertIsNone(QualityShipment.objects.get(pk=shipment_id).inspector_id)
        result = ReturnRework.objects.get()
        self.assertIsNone(result.responsible_inspector_id)
        self.assertIsNone(result.rework_employee_id)
        self.assertEqual(result.shipment.order_id, self.order.pk)
        for url in ("/api/quality/summary/", "/api/quality/shipment-ledger/", "/api/analytics/dashboard/"):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, (url, response.data))
            if url == "/api/quality/summary/":
                attribution = response.data["shipment_attribution"]
                self.assertEqual(attribution["unassigned_employee_quantity"], 130)
                self.assertEqual(attribution["employee_attributed_quantity"], 0)

    def test_create_over_balance_rolls_back_all_facts(self):
        self.ship(quantity=90)
        response = self.client.post("/api/quality/shipments/", self.shipment_payload("OVER", 11))
        self.assertEqual(response.status_code, 400)
        self.assertIn("shipped_quantity", response.data)
        self.assertEqual(QualityShipment.objects.count(), 1)
        self.assertEqual(self.balance(), 90)

    def test_edit_excludes_own_old_net_quantity(self):
        shipment_id = self.ship(quantity=60)
        self.ship("SECOND", 40)
        url = f"/api/quality/shipments/{shipment_id}/"
        self.assertEqual(self.client.patch(url, {"notes": "不变更数量"}).status_code, 200)
        self.assertEqual(self.client.patch(url, {"shipped_quantity": 50}).status_code, 200)
        self.assertEqual(self.balance(), 90)
        response = self.client.patch(url, {"shipped_quantity": 61, "qualified_quantity": 61, "inspection_quantity": 61})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(QualityShipment.objects.get(pk=shipment_id).shipped_quantity, 50)

    def test_edit_with_returns_uses_net_not_gross_shipments(self):
        shipment_id = self.ship()
        self.returned(shipment_id, 20)
        self.ship("RESHIP", 20)
        response = self.client.patch(f"/api/quality/shipments/{shipment_id}/", {"notes": "净交付仍100"})
        self.assertEqual(response.status_code, 200, response.data)
        response = self.client.patch(f"/api/quality/shipments/{shipment_id}/", {"shipped_quantity": 19})
        self.assertEqual(response.status_code, 400)

    def test_return_correction_cannot_reclaim_already_reshipped_balance(self):
        shipment_id = self.ship()
        return_id = self.returned(shipment_id, 20)
        self.ship("RESHIP", 20)
        url = f"/api/quality/reworks/{return_id}/"
        response = self.client.patch(url, {"returned_quantity": 19})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(ReturnRework.objects.get(pk=return_id).returned_quantity, 20)
        self.assertEqual(self.client.patch(url, {"returned_quantity": 30}).status_code, 200)
        self.assertEqual(self.balance(), 90)

    def test_cumulative_returns_cannot_exceed_source_shipment(self):
        shipment_id = self.ship()
        self.returned(shipment_id, 60)
        response = self.client.post("/api/quality/reworks/", self.return_payload(shipment_id, 41))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(ReturnRework.objects.count(), 1)

    def test_only_selected_order_is_consumed(self):
        other = QualityOrder.objects.create(
            order_no="OTHER-PO", specification=self.order.specification,
            order_quantity=100, source_system="艾斯霖", created_by=self.user,
        )
        shipment_id = self.ship(quantity=60)
        self.assertEqual(QualityShipment.objects.get(pk=shipment_id).order_id, self.order.pk)
        self.assertEqual(delivered_quantities_by_order([other.pk])[other.pk], 0)
        response = self.client.post("/api/quality/shipments/", self.shipment_payload("NO-SPILL", 41))
        self.assertEqual(response.status_code, 400)
        self.assertFalse(other.shipments.exists())

    def test_returned_records_keep_original_order_and_shipment(self):
        first = self.ship(quantity=60)
        second = self.ship("SECOND", 40)
        return_id = self.returned(first)
        other = QualityOrder.objects.create(
            order_no="OTHER-PO", specification="20x30", order_quantity=100,
            source_system="艾斯霖", created_by=self.user,
        )
        self.assertEqual(self.client.patch(f"/api/quality/shipments/{first}/", {"order_id": other.pk}).status_code, 400)
        self.assertEqual(self.client.patch(f"/api/quality/reworks/{return_id}/", {"shipment_id": second}).status_code, 400)

    def test_stale_patch_reloads_current_shipment(self):
        shipment_id = self.ship(quantity=60)
        stale = QualityShipmentSerializer(QualityShipment.objects.get(pk=shipment_id), data={"notes": "仅改备注"}, partial=True)
        self.assertTrue(stale.is_valid(), stale.errors)
        self.client.patch(f"/api/quality/shipments/{shipment_id}/", {"shipped_quantity": 50})
        stale.save()
        self.assertEqual(QualityShipment.objects.get(pk=shipment_id).shipped_quantity, 50)

    def test_balance_read_occurs_inside_transaction_after_order_lock(self):
        from quality import serializers
        original_lock = serializers._lock_piece_orders
        locked = []

        def lock(ids):
            result = original_lock(ids)
            locked.extend(order.pk for order in result)
            return result

        def balances(ids):
            ids = list(ids)
            self.assertTrue(connection.in_atomic_block)
            self.assertTrue(set(ids).issubset(locked))
            return delivered_quantities_by_order(ids)

        with patch.object(serializers, "_lock_piece_orders", side_effect=lock), patch.object(serializers, "delivered_quantities_by_order", side_effect=balances):
            self.ship()

    def test_confirmed_weighted_delivery_consumes_same_balance(self):
        for status, quantity in (("CONFIRMED", 60), ("DRAFT", 90), ("VOID", 90)):
            batch = QualityShipmentBatch.objects.bulk_create([QualityShipmentBatch(
                shipment_no=f"WEIGHT-{status}", status=status,
                shipment_date=timezone.localdate(), created_by=self.user,
            )])[0]
            QualityShipmentLine.objects.bulk_create([QualityShipmentLine(
                batch=batch, order=self.order, piece_quantity=quantity, net_weight_kg=Decimal("1"),
            )])
        self.ship(quantity=40)
        response = self.client.post("/api/quality/shipments/", self.shipment_payload("OVER", 1))
        self.assertEqual(response.status_code, 400)

    def test_old_sources_keep_existing_overage_and_required_employee_rules(self):
        employee = QualityEmployee.objects.create(employee_no="BOTH", name="旧业务", role="BOTH")
        for source in ("翔邦", "", "艾斯霖其他"):
            self.order.source_system = source
            self.order.save()
            payload = self.shipment_payload(f"OLD-{source}", 110)
            payload.pop("client_request_id")
            self.assertEqual(self.client.post("/api/quality/shipments/", payload).status_code, 400)
            payload["inspector_id"] = employee.pk
            response = self.client.post("/api/quality/shipments/", payload)
            self.assertEqual(response.status_code, 201, response.data)
            returned = self.return_payload(response.data["id"])
            returned.pop("client_request_id")
            self.assertEqual(self.client.post("/api/quality/reworks/", returned).status_code, 400)
            returned["rework_employee_id"] = employee.pk
            self.assertEqual(self.client.post("/api/quality/reworks/", returned).status_code, 201)


class PieceShipmentConcurrencyTests(PieceShipmentSetup, TransactionTestCase):
    def concurrent(self, requests):
        barrier = Barrier(len(requests))

        def run(request):
            close_old_connections()
            try:
                client = APIClient()
                client.force_authenticate(self.user)
                barrier.wait(timeout=10)
                method, url, payload = request
                response = getattr(client, method)(url, payload, format="json")
                return response.status_code, response.data
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=len(requests)) as pool:
            return list(pool.map(run, requests))

    def test_concurrent_creates_cannot_overship(self):
        results = self.concurrent([
            ("post", "/api/quality/shipments/", self.shipment_payload(number, 60))
            for number in ("RACE-1", "RACE-2")
        ])
        self.assertEqual(sorted(status for status, _ in results), [201, 400], results)
        self.assertEqual(self.balance(), 60)

    def test_same_shipment_number_reentry_creates_only_one(self):
        results = self.concurrent([
            ("post", "/api/quality/shipments/", self.shipment_payload("REENTRY", 60))
            for _ in range(2)
        ])
        self.assertEqual(sorted(status for status, _ in results), [201, 400], results)
        self.assertEqual(QualityShipment.objects.count(), 1)

    def test_concurrent_edits_cannot_overship(self):
        ids = [self.ship(number, 40) for number in ("EDIT-1", "EDIT-2")]
        results = self.concurrent([
            ("patch", f"/api/quality/shipments/{pk}/", {"inspection_quantity": 60, "qualified_quantity": 60, "shipped_quantity": 60})
            for pk in ids
        ])
        self.assertEqual(sorted(status for status, _ in results), [200, 400], results)
        self.assertEqual(self.balance(), 100)

    def test_concurrent_returns_cannot_double_release_balance(self):
        shipment_id = self.ship()
        results = self.concurrent([
            ("post", "/api/quality/reworks/", self.return_payload(shipment_id, 60))
            for _ in range(2)
        ])
        self.assertEqual(sorted(status for status, _ in results), [201, 400], results)
        self.assertEqual(self.balance(), 40)

    def test_concurrent_same_key_shipments_replay_one_record_with_no_number(self):
        for blank in (False, True):
            with self.subTest(blank=blank):
                payload = self.shipment_payload(quantity=40)
                payload.pop("shipment_no")
                if blank:
                    payload["shipment_no"] = ""
                request = ("post", "/api/quality/shipments/", payload)
                results = self.concurrent([request, request])
                self.assertEqual([status for status, _ in results], [201, 201], results)
                self.assertEqual(results[0][1]["id"], results[1][1]["id"])
        self.assertEqual(QualityShipment.objects.count(), 2)
        self.assertEqual(self.balance(), 80)

    def test_concurrent_same_key_returns_replay_one_release(self):
        shipment_id = self.ship()
        request = ("post", "/api/quality/reworks/", self.return_payload(shipment_id, 60))
        results = self.concurrent([request, request])
        self.assertEqual([status for status, _ in results], [201, 201], results)
        self.assertEqual(results[0][1]["id"], results[1][1]["id"])
        self.assertEqual(ReturnRework.objects.count(), 1)
        self.assertEqual(self.balance(), 40)

    def test_concurrent_same_key_different_orders_rejects_loser(self):
        other = QualityOrder.objects.create(
            order_no="ASL-OTHER", specification="20x30", order_quantity=100,
            source_system="艾斯霖", created_by=self.user,
        )
        first = self.shipment_payload(quantity=40)
        first.pop("shipment_no")
        second = {**first, "order_id": other.pk}
        results = self.concurrent([
            ("post", "/api/quality/shipments/", payload) for payload in (first, second)
        ])
        self.assertEqual(sorted(status for status, _ in results), [201, 400], results)
        self.assertIn("client_request_id", next(data for status, data in results if status == 400))
        self.assertEqual(QualityShipment.objects.count(), 1)
        self.assertEqual(sum(delivered_quantities_by_order([self.order.pk, other.pk]).values()), 40)

    def test_concurrent_same_key_different_return_parameters_rejects_loser(self):
        shipment_id = self.ship()
        first = self.return_payload(shipment_id, 20)
        second = {**first, "returned_quantity": 30}
        results = self.concurrent([
            ("post", "/api/quality/reworks/", payload) for payload in (first, second)
        ])
        self.assertEqual(sorted(status for status, _ in results), [201, 400], results)
        self.assertIn("client_request_id", next(data for status, data in results if status == 400))
        self.assertEqual(ReturnRework.objects.count(), 1)
        self.assertEqual(self.balance(), 100 - ReturnRework.objects.get().returned_quantity)


class PieceRequestIdempotencyTests(PieceShipmentSetup, TestCase):
    def post(self, url, payload, expected=201):
        response = self.client.post(url, payload, format="json")
        self.assertEqual(response.status_code, expected, response.data)
        return response

    def test_aslin_create_requires_non_null_valid_uuid_for_both_endpoints(self):
        shipment_id = self.ship(quantity=20)
        for url, base in (
            ("/api/quality/shipments/", self.shipment_payload("MISSING", 10)),
            ("/api/quality/reworks/", self.return_payload(shipment_id, 1)),
        ):
            for value in ("missing", None, "", "not-a-uuid"):
                with self.subTest(url=url, value=value):
                    payload = {**base, "client_request_id": value}
                    if value == "missing":
                        payload.pop("client_request_id")
                    response = self.post(url, payload, 400)
                    self.assertIn("client_request_id", response.data)
        self.assertEqual(QualityShipment.objects.count(), 1)
        self.assertFalse(ReturnRework.objects.exists())

    def test_retry_without_or_with_blank_number_returns_original_and_does_not_audit_twice(self):
        for number in ("omitted", "", "EXPLICIT-NUMBER"):
            with self.subTest(number=number):
                payload = self.shipment_payload(number, 20)
                if number == "omitted":
                    payload.pop("shipment_no")
                first = self.post("/api/quality/shipments/", payload).data
                revisions = BusinessRecordRevision.objects.count()
                retry = self.post("/api/quality/shipments/", dict(reversed(list(payload.items())))).data
                self.assertEqual((retry["id"], retry["shipment_no"], retry["created_at"], retry["updated_at"]),
                                 (first["id"], first["shipment_no"], first["created_at"], first["updated_at"]))
                self.assertEqual(BusinessRecordRevision.objects.count(), revisions)
                stored = QualityShipment.objects.get(pk=first["id"])
                self.assertEqual(str(stored.client_request_id), payload["client_request_id"])
                self.assertEqual(len(stored.request_fingerprint), 64)
        self.assertEqual(QualityShipment.objects.count(), 3)
        self.assertEqual(self.balance(), 60)

    def test_return_retry_does_not_release_balance_twice(self):
        shipment_id = self.ship()
        payload = self.return_payload(shipment_id, 60)
        first = self.post("/api/quality/reworks/", payload).data
        revisions = BusinessRecordRevision.objects.count()
        retry = self.post("/api/quality/reworks/", payload).data
        self.assertEqual(first["id"], retry["id"])
        self.assertEqual(ReturnRework.objects.count(), 1)
        self.assertEqual(BusinessRecordRevision.objects.count(), revisions)
        self.assertEqual(self.balance(), 40)
        self.assertEqual(len(ReturnRework.objects.get().request_fingerprint), 64)
        self.ship("RESHIP", 60)
        self.post("/api/quality/reworks/", payload)
        self.assertEqual(self.balance(), 100)

    def test_distinct_keys_allow_legitimate_identical_batches(self):
        payload = self.shipment_payload(quantity=20)
        payload.pop("shipment_no")
        first = self.post("/api/quality/shipments/", payload).data
        second = self.post("/api/quality/shipments/", {**payload, "client_request_id": str(uuid.uuid4())}).data
        self.assertNotEqual(first["id"], second["id"])
        self.assertNotEqual(first["shipment_no"], second["shipment_no"])
        returned = self.return_payload(first["id"], 5)
        self.post("/api/quality/reworks/", returned)
        self.post("/api/quality/reworks/", {**returned, "client_request_id": str(uuid.uuid4())})
        self.assertEqual((QualityShipment.objects.count(), ReturnRework.objects.count(), self.balance()), (2, 2, 30))

    def test_same_key_changed_parameters_rejected_for_shipment_and_return(self):
        other = QualityOrder.objects.create(
            order_no="ASL-OTHER", specification="20x30", order_quantity=100,
            source_system="艾斯霖", created_by=self.user,
        )
        shipment = self.shipment_payload(quantity=60)
        first = self.post("/api/quality/shipments/", shipment).data
        changes = ({"notes": "different"}, {"order_id": other.pk},
                   {"shipped_quantity": 59}, {"shipment_no": "DIFFERENT"}, {"inspector_ids": []})
        for change in changes:
            response = self.post("/api/quality/shipments/", {**shipment, **change}, 400)
            self.assertIn("client_request_id", response.data)
        second_id = self.ship("SECOND", 40)
        returned = self.return_payload(first["id"], 20)
        self.post("/api/quality/reworks/", returned)
        for change in ({"notes": "different"}, {"returned_quantity": 21}, {"shipment_id": second_id}, {"reason": "different"}):
            response = self.post("/api/quality/reworks/", {**returned, **change}, 400)
            self.assertIn("client_request_id", response.data)
        self.assertEqual((QualityShipment.objects.count(), ReturnRework.objects.count(), self.balance()), (2, 1, 80))

    def test_replay_requires_same_owner_even_for_another_admin(self):
        shipment = self.shipment_payload(quantity=60)
        saved = self.post("/api/quality/shipments/", shipment).data
        returned = self.return_payload(saved["id"], 20)
        self.post("/api/quality/reworks/", returned)
        other = get_user_model().objects.create_superuser(username="other-admin", password="test-only")
        self.client.force_authenticate(other)
        for url, payload in (("/api/quality/shipments/", shipment), ("/api/quality/reworks/", returned)):
            response = self.post(url, payload, 400)
            self.assertIn("client_request_id", response.data)
            self.assertIn("其他用户", str(response.data))
        self.assertEqual((QualityShipment.objects.count(), ReturnRework.objects.count(), self.balance()), (1, 1, 40))

    def test_original_fingerprint_survives_edits_and_not_recomputed_from_model(self):
        shipment = self.shipment_payload(quantity=60)
        saved = self.post("/api/quality/shipments/", shipment).data
        returned = self.return_payload(saved["id"], 20)
        returned_row = self.post("/api/quality/reworks/", returned).data
        for url, payload, saved_row, model in (
            ("/api/quality/shipments/", shipment, saved, QualityShipment),
            ("/api/quality/reworks/", returned, returned_row, ReturnRework),
        ):
            original_hash = model.objects.get(pk=saved_row["id"]).request_fingerprint
            response = self.client.patch(f"{url}{saved_row['id']}/", {"notes": "later correction", "request_fingerprint": "forged"}, format="json")
            self.assertEqual(response.status_code, 200, response.data)
            replay = self.post(url, payload).data
            self.assertEqual((replay["id"], replay["notes"]), (saved_row["id"], "later correction"))
            self.post(url, {**payload, "notes": "later correction"}, 400)
            self.assertEqual(model.objects.get(pk=saved_row["id"]).request_fingerprint, original_hash)
            self.assertNotIn("request_fingerprint", replay)

    def test_patch_and_put_cannot_replace_or_clear_key(self):
        shipment = self.shipment_payload(quantity=60)
        saved = self.post("/api/quality/shipments/", shipment).data
        returned = self.return_payload(saved["id"], 20)
        saved_return = self.post("/api/quality/reworks/", returned).data
        for url, row, payload in (
            ("/api/quality/shipments/", saved, shipment),
            ("/api/quality/reworks/", saved_return, returned),
        ):
            endpoint = f"{url}{row['id']}/"
            for value in (None, str(uuid.uuid4())):
                response = self.client.patch(endpoint, {"client_request_id": value}, format="json")
                self.assertEqual(response.status_code, 400, response.data)
                self.assertIn("client_request_id", response.data)
                response = self.client.put(endpoint, {**payload, "client_request_id": value}, format="json")
                self.assertEqual(response.status_code, 400, response.data)
            response = self.client.patch(endpoint, {"client_request_id": payload["client_request_id"]}, format="json")
            self.assertEqual(response.status_code, 200, response.data)

    def test_failed_request_rolls_back_key_and_retry_can_succeed(self):
        shipment_id = self.ship(quantity=90)
        payload = self.shipment_payload("WILL-RETRY", 20)
        self.post("/api/quality/shipments/", payload, 400)
        self.assertFalse(QualityShipment.objects.filter(client_request_id=payload["client_request_id"]).exists())
        self.returned(shipment_id, 10)
        self.post("/api/quality/shipments/", payload)
        self.assertEqual(self.balance(), 100)

    def test_retries_do_not_duplicate_order_ledger_summary_or_analytics(self):
        shipment = self.shipment_payload(quantity=100)
        shipment.pop("shipment_no")
        first = self.post("/api/quality/shipments/", shipment).data
        # Full order has no remaining capacity: replay must precede balance checks.
        self.assertEqual(self.post("/api/quality/shipments/", shipment).data["id"], first["id"])
        returned = self.return_payload(first["id"], 60)
        return_row = self.post("/api/quality/reworks/", returned).data
        reship = self.shipment_payload("RESHIP-IDEMP", 60)
        reship_row = self.post("/api/quality/shipments/", reship).data
        for url, payload, row in (
            ("/api/quality/shipments/", shipment, first),
            ("/api/quality/reworks/", returned, return_row),
            ("/api/quality/shipments/", reship, reship_row),
        ):
            self.assertEqual(self.post(url, payload).data["id"], row["id"])
        self.assertEqual((self.balance(), QualityShipment.objects.count(), ReturnRework.objects.count()), (100, 2, 1))
        detail = self.client.get(f"/api/orders/orders/{self.order.pk}/").data
        self.assertEqual((detail["weighted_shipped_quantity"], detail["weighted_remaining_quantity"]), (100, 0))
        summary = self.client.get("/api/quality/summary/").data
        dashboard = self.client.get("/api/analytics/dashboard/").data
        for totals in (summary["totals"], dashboard["quality"]["total"]):
            self.assertEqual((totals["shipped_quantity"], totals["returned_quantity"]), (160, 60))
        ledger = self.client.get("/api/quality/shipment-ledger/").data["results"]
        self.assertEqual(sorted(row["source_id"] for row in ledger), sorted([first["id"], reship_row["id"]]))

    def test_existing_unkeyed_records_remain_editable_but_cannot_acquire_a_key(self):
        self.order.source_system = "翔邦"
        self.order.save()
        person = QualityEmployee.objects.create(employee_no="UNKEYED", name="原记录", role="BOTH")
        shipment = {**self.shipment_payload(quantity=60), "inspector_id": person.pk}
        shipment.pop("client_request_id")
        saved = self.post("/api/quality/shipments/", shipment).data
        returned = {**self.return_payload(saved["id"], 20), "rework_employee_id": person.pk}
        returned.pop("client_request_id")
        return_row = self.post("/api/quality/reworks/", returned).data
        self.order.source_system = "艾斯霖"
        self.order.save()
        for url, row in (("/api/quality/shipments/", saved), ("/api/quality/reworks/", return_row)):
            endpoint = f"{url}{row['id']}/"
            response = self.client.patch(endpoint, {"notes": "old row remains editable"}, format="json")
            self.assertEqual(response.status_code, 200, response.data)
            self.assertIsNone(response.data["client_request_id"])
            response = self.client.patch(endpoint, {"client_request_id": str(uuid.uuid4())}, format="json")
            self.assertEqual(response.status_code, 400, response.data)

    def test_old_source_optional_key_is_compatible_and_keyed_retries_work(self):
        self.order.source_system = "翔邦"
        self.order.save()
        person = QualityEmployee.objects.create(employee_no="IDEMP-OLD", name="旧来源实录", role="BOTH")
        shipment = {**self.shipment_payload(quantity=20), "inspector_id": person.pk}
        first = self.post("/api/quality/shipments/", shipment).data
        self.assertEqual(self.post("/api/quality/shipments/", shipment).data["id"], first["id"])
        returned = {**self.return_payload(first["id"], 5), "rework_employee_id": person.pk}
        saved_return = self.post("/api/quality/reworks/", returned).data
        self.assertEqual(self.post("/api/quality/reworks/", returned).data["id"], saved_return["id"])
        no_key = {**returned}
        no_key.pop("client_request_id")
        self.post("/api/quality/reworks/", no_key)
        self.post("/api/quality/reworks/", no_key)
        self.assertEqual(ReturnRework.objects.count(), 3)


class PieceRequestMigrationTests(TransactionTestCase):
    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_fresh_0017_to_0018_preserves_history_and_enforces_unique_nullable_keys(self):
        before = ("quality", "0017_add_return_defect_reasons")
        after = ("quality", "0018_piece_shipment_optional_employees")
        executor = MigrationExecutor(connection)
        executor.migrate([before])
        apps = executor.loader.project_state([before]).apps
        user = apps.get_model("auth", "User").objects.create(username="idempotency-migration")
        employee = apps.get_model("quality", "QualityEmployee").objects.create(employee_no="IDEMP-MIG", name="历史", role="BOTH")
        order = apps.get_model("quality", "QualityOrder").objects.create(
            order_no="IDEMP-MIG-ORDER", specification="20x30", order_quantity=100, created_by_id=user.pk,
        )
        shipment = apps.get_model("quality", "QualityShipment").objects.create(
            shipment_no="IDEMP-MIG-SHIP", shipment_date=timezone.localdate(),
            order_id=order.pk, inspector_id=employee.pk, inspection_quantity=100,
            qualified_quantity=100, shipped_quantity=100, created_by_id=user.pk,
        )
        returned = apps.get_model("quality", "ReturnRework").objects.create(
            shipment_id=shipment.pk, rework_date=timezone.localdate(), reason_category="OTHER",
            responsible_inspector_id=employee.pk, rework_employee_id=employee.pk,
            returned_quantity=5, created_by_id=user.pk,
        )
        executor = MigrationExecutor(connection)
        executor.migrate([after])
        apps = executor.loader.project_state([after]).apps
        for name, row_id in (("QualityShipment", shipment.pk), ("ReturnRework", returned.pk)):
            model = apps.get_model("quality", name)
            row = model.objects.get(pk=row_id)
            self.assertIsNone(row.client_request_id)
            self.assertEqual(row.request_fingerprint, "")
            self.assertEqual(row.created_by_id, user.pk)
            key = uuid.uuid4()
            model.objects.filter(pk=row.pk).update(client_request_id=key, request_fingerprint="a" * 64)
            values = {field.attname: getattr(row, field.attname) for field in model._meta.fields if not field.primary_key}
            if name == "QualityShipment":
                values["shipment_no"] = "IDEMP-MIG-DUP"
            values.update(client_request_id=key, request_fingerprint="b" * 64)
            with self.assertRaises(IntegrityError), transaction.atomic():
                model.objects.create(**values)
            values.update(client_request_id=None, request_fingerprint="")
            model.objects.create(**values)
            if name == "QualityShipment":
                values["shipment_no"] = "IDEMP-MIG-NULL2"
            model.objects.create(**values)
            self.assertEqual(model.objects.filter(client_request_id__isnull=True).count(), 2)
