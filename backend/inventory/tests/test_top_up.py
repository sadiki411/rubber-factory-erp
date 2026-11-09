import copy
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import close_old_connections
from django.test import TransactionTestCase
from rest_framework.test import APIClient, APITestCase

from inventory.models import (InventoryBatch, InventoryContainer, InventoryLocation,
                              InventoryProduct, InventoryTopUp, InventoryTransaction)
from inventory.services import top_up_product_identity
from quality.models import QualityEmployee, QualityShipment


class TopUpFixture:
    def seed(self):
        self.user = get_user_model().objects.create_user(username="topup-user")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.location = InventoryLocation.objects.create(
            code="TEST-L01-P01", rack_code="TEST", rack_type="LARGE", level_no=1,
            position_no=1, allows_basket=True, allows_bag=True)
        self.old_qc = QualityEmployee.objects.create(employee_no="TOP-01", name="原品检员", role="INSPECTOR")
        self.new_qc = QualityEmployee.objects.create(employee_no="TOP-02", name="本次品检员", role="INSPECTOR")
        result = self.client.post("/api/inventory/receipts/", {
            "product": {"product_code": "P-100", "product_name": "圈", "specification": "100A", "material": "NBR"},
            "container_type": "BASKET", "location_id": self.location.pk,
            "quantity": 4000, "bag_count": 20, "pieces_per_bag": 200,
            "quality_status": "PASSED", "inspector_id": self.old_qc.pk,
            "received_on": "2026-10-01", "batch_no": "TOP-ORIGINAL",
        }, format="json")
        self.assertEqual(result.status_code, 201, result.content)
        self.container = InventoryContainer.objects.select_related("batch__product").get(pk=result.json()["id"])
        self.url = f"/api/inventory/containers/{self.container.pk}/top-up/"

    def body(self, **overrides):
        self.container.refresh_from_db()
        data = {
            "request_id": str(uuid.uuid4()), "quantity": 500,
            "expected_updated_at": self.container.updated_at.isoformat(),
            "expected_quantity": self.container.quantity, "expected_location_id": self.container.location_id,
            "product_identity": top_up_product_identity(self.container.batch.product),
            "quality_status": "PASSED", "received_on": "2026-11-09",
            "inspector_id": self.new_qc.pk, "inspected_on": "2026-11-08", "notes": "新增加的货",
        }
        data.update(overrides)
        return data

    def post(self, data=None):
        return self.client.post(self.url, data or self.body(), format="json")


class InventoryTopUpTests(TopUpFixture, APITestCase):
    def setUp(self):
        self.seed()

    def test_adds_delta_with_independent_inspector_and_preserves_original(self):
        batch_before = InventoryBatch.objects.values().get(pk=self.container.batch_id)
        old_entry = InventoryTransaction.objects.values().get(container=self.container)
        response = self.post()
        self.assertEqual(response.status_code, 200, response.content)
        self.container.refresh_from_db()
        self.assertEqual(self.container.quantity, 4500)
        self.assertEqual(self.container.bag_count, 20)
        self.assertEqual(self.container.pieces_per_bag, 200)
        self.assertEqual(response.json()["top_up_receipt"]["inspector_name"], "本次品检员")
        self.assertEqual(response.json()["top_up_receipt"]["received_on"], "2026-11-09")
        self.assertEqual(response.json()["top_up_receipt"]["quantity"], 500)
        self.assertEqual(InventoryBatch.objects.values().get(pk=self.container.batch_id), batch_before)
        self.assertEqual(InventoryTransaction.objects.values().get(pk=old_entry["id"]), old_entry)
        self.assertEqual(InventoryBatch.objects.count(), 1)
        self.assertEqual(InventoryContainer.objects.count(), 1)
        self.assertEqual(InventoryProduct.objects.count(), 1)
        self.assertEqual(QualityShipment.objects.count(), 0)
        summary = self.client.get("/api/inventory/summary/").json()
        self.assertEqual(summary["total_quantity"], 4500)
        self.assertEqual(summary["available_quantity"], 4500)

    def test_same_request_replays_after_outbound_and_inspector_deactivation(self):
        data = self.body()
        self.assertEqual(self.post(data).status_code, 200)
        outbound = self.client.post("/api/inventory/outbounds/", {
            "lines": [{"container_id": self.container.pk, "quantity": 1000}],
        }, format="json")
        self.assertEqual(outbound.status_code, 201, outbound.content)
        self.new_qc.is_active = False
        self.new_qc.save()
        replay = self.post(data)
        self.assertEqual(replay.status_code, 200, replay.content)
        self.assertTrue(replay.json()["replayed"])
        self.assertEqual(replay.json()["quantity"], 3500)
        self.assertEqual(InventoryTopUp.objects.count(), 1)
        self.assertEqual(InventoryTransaction.objects.filter(transaction_type="RECEIPT").count(), 2)

    def test_request_key_cannot_be_reused_for_changed_content_or_other_user(self):
        data = self.body()
        self.assertEqual(self.post(data).status_code, 200)
        changed = {**data, "quantity": 900}
        self.assertEqual(self.post(changed).status_code, 409)
        self.client.force_authenticate(get_user_model().objects.create_user(username="someone-else"))
        self.assertEqual(self.post(data).status_code, 409)
        self.container.refresh_from_db()
        self.assertEqual(self.container.quantity, 4500)

    def test_stale_quantity_version_location_and_product_identity_do_not_add(self):
        for field, value in (("expected_quantity", 3999), ("expected_updated_at", "2026-01-01T00:00:00Z"),
                             ("expected_location_id", self.location.pk + 1)):
            with self.subTest(field=field):
                self.assertEqual(self.post(self.body(**{field: value})).status_code, 409)
        data = self.body()
        data["product_identity"]["material"] = "EPDM"
        self.assertEqual(self.post(data).status_code, 409)
        self.assertEqual(InventoryTopUp.objects.count(), 0)

    def test_existing_outbound_invalidates_open_dialog(self):
        data = self.body()
        result = self.client.post("/api/inventory/outbounds/", {"lines": [{"container_id": self.container.pk, "quantity": 100}]}, format="json")
        self.assertEqual(result.status_code, 201, result.content)
        self.assertEqual(self.post(data).status_code, 409)
        self.container.refresh_from_db()
        self.assertEqual(self.container.quantity, 3900)
        self.assertEqual(InventoryTopUp.objects.count(), 0)

    def test_mixing_quality_states_is_rejected(self):
        self.assertEqual(self.post(self.body(quality_status="WAITING", inspector_id=None)).status_code, 409)
        self.assertEqual(InventoryTopUp.objects.count(), 0)

    def test_waiting_can_top_up_but_cannot_be_shipped_as_qualified(self):
        InventoryBatch.objects.filter(pk=self.container.batch_id).update(quality_status="WAITING", inspector=None)
        response = self.post(self.body(quality_status="WAITING", inspector_id=None))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["quantity"], 4500)
        result = self.client.post("/api/inventory/outbounds/", {"lines": [{"container_id": self.container.pk, "quantity": 100}]}, format="json")
        self.assertEqual(result.status_code, 400, result.content)
        self.assertEqual(self.client.get("/api/inventory/summary/").json()["waiting_inspection_quantity"], 4500)

    def test_passed_requires_this_time_inspector_and_valid_positive_integer(self):
        for fields in ({"inspector_id": None}, {"inspector_id": 999999}, {"quantity": 0}, {"quantity": -1},
                       {"quantity": 1.5}, {"quantity": 2147483648}, {"received_on": ""}, {"bag_count": 0}):
            with self.subTest(fields=fields):
                response = self.post(self.body(**fields))
                self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(InventoryTopUp.objects.count(), 0)

    def test_overflow_inactive_product_location_and_closed_container_are_rejected(self):
        self.assertEqual(self.post(self.body(quantity=2147483647)).status_code, 400)
        for model, pk in ((InventoryProduct, self.container.batch.product_id), (InventoryLocation, self.location.pk)):
            model.objects.filter(pk=pk).update(is_active=False)
            self.assertEqual(self.post().status_code, 400)
            model.objects.filter(pk=pk).update(is_active=True)
        InventoryContainer.objects.filter(pk=self.container.pk).update(status="CLOSED")
        self.assertEqual(self.post().status_code, 400)
        self.assertEqual(InventoryTopUp.objects.count(), 0)

    def test_optional_bags_are_absolute_after_count_and_original_per_bag_not_recomputed(self):
        response = self.post(self.body(bag_count=22))
        self.assertEqual(response.status_code, 200, response.content)
        self.container.refresh_from_db()
        self.assertEqual((self.container.bag_count, self.container.pieces_per_bag), (22, 200))
        receipt = InventoryTopUp.objects.get()
        self.assertEqual((receipt.bag_count_before, receipt.bag_count_after), (20, 22))

    def test_failure_to_save_receipt_rolls_back_balance_and_transaction(self):
        before = InventoryTransaction.objects.count()
        with patch("inventory.services.InventoryTopUp.objects.create", side_effect=ValueError("模拟保存失败")):
            self.assertEqual(self.post().status_code, 400)
        self.container.refresh_from_db()
        self.assertEqual(self.container.quantity, 4000)
        self.assertEqual(InventoryTransaction.objects.count(), before)
        self.assertEqual(InventoryTopUp.objects.count(), 0)

    def test_history_snapshots_survive_rename_and_public_history_excludes_private_fields(self):
        self.assertEqual(self.post().status_code, 200)
        self.new_qc.name = "改名后"
        self.new_qc.save()
        InventoryProduct.objects.filter(pk=self.container.batch.product_id).update(specification="后来更正")
        history = self.client.get(self.url.replace("top-up/", "top-up-history/")).json()
        self.assertEqual(history["results"][0]["inspector_name"], "本次品检员")
        self.assertEqual(history["results"][0]["product_snapshot"]["specification"], "100A")
        public = APIClient().get(f"/api/inventory/public/locations/{self.location.code}/history/")
        self.assertEqual(public.status_code, 200, public.content)
        row = next(row for row in public.json()["results"] if row["operation_label"] == "追加入库")
        self.assertEqual(row["quantity_change"], 500)
        self.assertEqual(row["specification"], "100A")
        for private in ("inspector", "created_by", "notes", "request_id", "payload_hash"):
            self.assertNotIn(private, str(row))

    def test_read_only_cannot_post_but_can_read_and_anonymous_cannot_read_private_history(self):
        readonly = get_user_model().objects.create_user(username="readonly")
        readonly.groups.add(Group.objects.get_or_create(name="GPT只读测试")[0])
        self.client.force_authenticate(readonly)
        self.assertEqual(self.post().status_code, 403)
        self.assertEqual(self.client.get(self.url.replace("top-up/", "top-up-history/")).status_code, 200)
        self.client.force_authenticate(None)
        self.assertIn(self.post().status_code, (401, 403))
        self.assertIn(self.client.get(self.url.replace("top-up/", "top-up-history/")).status_code, (401, 403))

    def test_history_paginated_without_losing_old_entries(self):
        for _ in range(31):
            response = self.post()
            self.assertEqual(response.status_code, 200, response.content)
        first = self.client.get(self.url.replace("top-up/", "top-up-history/")).json()
        second = self.client.get(self.url.replace("top-up/", "top-up-history/") + "?page=2").json()
        self.assertEqual((first["count"], len(first["results"]), len(second["results"])), (31, 30, 1))
        self.assertFalse({row["id"] for row in first["results"]} & {row["id"] for row in second["results"]})


class InventoryTopUpConcurrencyTests(TopUpFixture, TransactionTestCase):
    def setUp(self):
        self.seed()

    def simultaneous(self, bodies):
        gate = threading.Barrier(2)

        def call(data):
            close_old_connections()
            try:
                client = APIClient()
                client.force_authenticate(get_user_model().objects.get(pk=self.user.pk))
                gate.wait(timeout=10)
                result = client.post(self.url, data, format="json")
                return result.status_code, result.json()
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(call, data) for data in bodies]
            return [future.result(timeout=30) for future in futures]

    def test_identical_concurrent_requests_add_once(self):
        data = self.body()
        results = self.simultaneous([data, copy.deepcopy(data)])
        self.assertEqual(sorted(code for code, _ in results), [200, 200], results)
        self.assertEqual(sorted(row["replayed"] for _, row in results), [False, True])
        self.container.refresh_from_db()
        self.assertEqual(self.container.quantity, 4500)
        self.assertEqual(InventoryTopUp.objects.count(), 1)

    def test_distinct_concurrent_requests_on_same_version_cannot_overwrite(self):
        results = self.simultaneous([self.body(), self.body()])
        self.assertEqual(sorted(code for code, _ in results), [200, 409], results)
        self.container.refresh_from_db()
        self.assertEqual(self.container.quantity, 4500)
        self.assertEqual(InventoryTopUp.objects.count(), 1)
