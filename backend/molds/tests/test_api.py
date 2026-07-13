import base64
import os
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework import serializers as drf_serializers
from rest_framework.test import APIClient

from molds.models import (
    Machine,
    MoldAsset,
    MoldModel,
    MoldMovement,
    Processor,
    Rack,
    RackZone,
)
from molds.serializers import MoldAssetSerializer
from molds.services import archive_mold, switch_zone_stacking, transition_mold
from production.models import ProductionRun, ProductionStation

from .helpers import SeededRackMixin


class AuthenticationAndCsrfTests(SeededRackMixin, TestCase):
    def setUp(self):
        self.csrf_client = APIClient(enforce_csrf_checks=True)

    def _csrf_token(self):
        response = self.csrf_client.get("/api/auth/session/")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["authenticated"])
        return self.csrf_client.cookies["csrftoken"].value

    def test_business_api_requires_shared_account_session(self):
        response = APIClient().get("/api/molds/")
        self.assertIn(response.status_code, (401, 403))

    def test_login_requires_csrf_and_establishes_session(self):
        token = self._csrf_token()
        rejected = self.csrf_client.post(
            "/api/auth/login/",
            {"username": "shared", "password": "shared-password"},
            format="json",
        )
        self.assertEqual(rejected.status_code, 403)

        logged_in = self.csrf_client.post(
            "/api/auth/login/",
            {"username": "shared", "password": "shared-password"},
            format="json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(logged_in.status_code, 200)
        self.assertTrue(logged_in.json()["authenticated"])
        self.assertEqual(logged_in.json()["user"]["username"], "shared")

        session = self.csrf_client.get("/api/auth/session/")
        self.assertTrue(session.json()["authenticated"])

    def test_bad_credentials_do_not_create_session(self):
        token = self._csrf_token()
        response = self.csrf_client.post(
            "/api/auth/login/",
            {"username": "shared", "password": "wrong"},
            format="json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.csrf_client.get("/api/auth/session/").json()["authenticated"])

    def test_authenticated_unsafe_requests_still_require_csrf(self):
        token = self._csrf_token()
        self.csrf_client.post(
            "/api/auth/login/",
            {"username": "shared", "password": "shared-password"},
            format="json",
            HTTP_X_CSRFTOKEN=token,
        )
        token = self.csrf_client.cookies["csrftoken"].value
        rejected = self.csrf_client.post(
            "/api/processors/",
            {"code": "OUT-01", "name": "外协一厂"},
            format="json",
        )
        self.assertEqual(rejected.status_code, 403)

        accepted = self.csrf_client.post(
            "/api/processors/",
            {"code": "OUT-01", "name": "外协一厂"},
            format="json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(accepted.status_code, 201)

    def test_logout_requires_csrf_and_clears_session(self):
        token = self._csrf_token()
        self.csrf_client.post(
            "/api/auth/login/",
            {"username": "shared", "password": "shared-password"},
            format="json",
            HTTP_X_CSRFTOKEN=token,
        )
        token = self.csrf_client.cookies["csrftoken"].value
        self.assertEqual(self.csrf_client.post("/api/auth/logout/").status_code, 403)
        response = self.csrf_client.post("/api/auth/logout/", HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 204)
        self.assertFalse(self.csrf_client.get("/api/auth/session/").json()["authenticated"])

    def test_health_endpoint_is_available_without_login(self):
        response = APIClient().get("/api/health/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "database": "ok"})


class MoldApiTests(SeededRackMixin, TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.model = MoldModel.objects.create(code="ABC-100", product_name="汽车密封圈")
        self.stock = self.create_mold(
            "ABC-100-01", self.slot("J01", 1, position=1), model=self.model
        )
        self.machine = Machine.objects.create(code="MC-01", name="一号机")
        self.machine_mold = MoldAsset.objects.create(
            asset_code="ABC-100-02",
            mold_model=self.model,
            status=MoldAsset.Status.ON_MACHINE,
            current_machine=self.machine,
        )

    def test_search_model_returns_every_physical_mold_with_current_state(self):
        response = self.client.get("/api/molds/", {"q": "ABC-100"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["count"], 2)
        results = {item["asset_code"]: item for item in payload["results"]}
        self.assertEqual(results["ABC-100-01"]["status"], MoldAsset.Status.IN_STOCK)
        self.assertEqual(results["ABC-100-01"]["slot"]["display_code"], self.stock.current_slot.display_code)
        self.assertIsNone(results["ABC-100-01"]["machine"])
        self.assertEqual(results["ABC-100-02"]["status"], MoldAsset.Status.ON_MACHINE)
        self.assertEqual(results["ABC-100-02"]["machine"]["code"], "MC-01")
        self.assertIsNone(results["ABC-100-02"]["slot"])

    def test_search_accepts_asset_code_and_product_name(self):
        by_asset = self.client.get("/api/molds/", {"q": "100-02"}).json()
        self.assertEqual([item["asset_code"] for item in by_asset["results"]], ["ABC-100-02"])
        by_product = self.client.get("/api/molds/", {"q": "密封圈"}).json()
        self.assertEqual(by_product["count"], 2)

    def test_direct_status_patch_cannot_change_domain_state(self):
        response = self.client.patch(
            f"/api/molds/{self.stock.pk}/",
            {"status": MoldAsset.Status.OUTSOURCED},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.status, MoldAsset.Status.IN_STOCK)
        self.assertIsNotNone(self.stock.current_slot)

    def test_action_returns_409_warnings_then_moves_after_confirmation(self):
        upper = self.slot("J05", 1, zone="A", capacity=2, position=1, stack=2)
        switch_zone_stacking(upper.zone, True)
        url = f"/api/molds/{self.stock.pk}/actions/move/"
        warning = self.client.post(url, {"slot_id": upper.pk}, format="json")
        self.assertEqual(warning.status_code, 409)
        self.assertTrue(warning.json()["requires_confirmation"])
        self.assertTrue(any("上叠位置下方没有模具" in text for text in warning.json()["warnings"]))
        self.stock.refresh_from_db()
        self.assertNotEqual(self.stock.current_slot, upper)

        confirmed = self.client.post(
            url,
            {"slot_id": upper.pk, "confirm_warnings": True, "note": "已确认"},
            format="json",
        )
        self.assertEqual(confirmed.status_code, 200)
        self.assertEqual(confirmed.json()["slot"]["id"], upper.pk)
        movement = MoldMovement.objects.get(mold=self.stock)
        self.assertEqual(movement.note, "已确认")

    def test_create_on_upper_stack_uses_same_confirmable_warning_flow(self):
        upper = self.slot("J05", 1, zone="A", capacity=2, position=1, stack=2)
        switch_zone_stacking(upper.zone, True)
        payload = {
            "asset_code": "UPPER-NEW-01",
            "mold_model_id": self.model.pk,
            "slot_id": upper.pk,
        }
        warning = self.client.post("/api/molds/", payload, format="multipart")
        self.assertEqual(warning.status_code, 409)
        self.assertTrue(warning.json()["requires_confirmation"])
        self.assertFalse(MoldAsset.objects.filter(asset_code="UPPER-NEW-01").exists())

        payload["confirm_warnings"] = "true"
        confirmed = self.client.post("/api/molds/", payload, format="multipart")
        self.assertEqual(confirmed.status_code, 201)
        self.assertEqual(confirmed.json()["slot"]["id"], upper.pk)

    def test_create_accepts_manual_model_code_and_generates_asset_code(self):
        target = self.slot("J01", 1, zone="B", position=1)
        response = self.client.post(
            "/api/molds/",
            {
                "model_code": "MANUAL-200",
                "product_name": "手工录入产品",
                "slot_id": target.pk,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.json())
        self.assertEqual(response.json()["asset_code"], "MANUAL-200-01")
        self.assertEqual(response.json()["mold_model"]["code"], "MANUAL-200")
        self.assertEqual(response.json()["slot"]["id"], target.pk)
        mold = MoldAsset.objects.select_related("mold_model", "current_slot").get(
            asset_code="MANUAL-200-01"
        )
        self.assertEqual(mold.mold_model.product_name, "手工录入产品")
        self.assertEqual(mold.current_slot_id, target.pk)
        movement = MoldMovement.objects.get(mold=mold, action=MoldMovement.Action.CREATE)
        self.assertEqual(movement.to_slot_id, target.pk)
        self.assertEqual(movement.operator, self.user)

    def test_manual_model_reuses_existing_model_and_increments_asset_code(self):
        existing = MoldModel.objects.create(code="REUSE-300", product_name="已有产品")
        MoldAsset.objects.create(
            asset_code="REUSE-300-01",
            mold_model=existing,
            status=MoldAsset.Status.IN_STOCK,
            current_slot=self.slot("J01", 2, zone="A", position=1),
        )
        target = self.slot("J01", 2, zone="B", position=1)

        response = self.client.post(
            "/api/molds/",
            {
                "model_code": "reuse-300",
                "product_name": "更新后的产品名",
                "slot_id": target.pk,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.json())
        self.assertEqual(response.json()["asset_code"], "REUSE-300-02")
        self.assertEqual(MoldModel.objects.filter(code__iexact="REUSE-300").count(), 1)
        created = MoldAsset.objects.get(asset_code="REUSE-300-02")
        self.assertEqual(created.mold_model_id, existing.pk)
        existing.refresh_from_db()
        self.assertEqual(existing.product_name, "更新后的产品名")

    def test_generated_asset_code_keeps_chinese_model_text_and_product_defaults_to_model(self):
        target = self.slot("J01", 3, zone="B", position=1)
        response = self.client.post(
            "/api/molds/",
            {"model_code": "密封圈 / 100", "slot_id": target.pk},
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.json())
        self.assertEqual(response.json()["asset_code"], "密封圈-100-01")
        model = MoldModel.objects.get(code="密封圈 / 100")
        self.assertEqual(model.product_name, "密封圈 / 100")

    def test_create_requires_model_id_or_manual_model_code(self):
        response = self.client.post(
            "/api/molds/",
            {"slot_id": self.slot("J01", 3, zone="A", position=1).pk},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("model_code", response.json())

    def test_explicit_duplicate_asset_code_returns_asset_code_error(self):
        response = self.client.post(
            "/api/molds/",
            {
                "asset_code": self.stock.asset_code,
                "model_code": "DUPLICATE-MODEL",
                "slot_id": self.slot("J01", 4, zone="A", position=1).pk,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("asset_code", response.json())
        self.assertNotIn("slot_id", response.json())

    def test_asset_code_can_be_renamed_or_cleared_to_regenerate_with_edit_history(self):
        renamed = self.client.patch(
            f"/api/molds/{self.stock.pk}/",
            {"asset_code": "RENAMED-01"},
            format="json",
        )
        self.assertEqual(renamed.status_code, 200, renamed.json())
        self.assertEqual(renamed.json()["asset_code"], "RENAMED-01")
        rename_history = MoldMovement.objects.get(
            mold=self.stock,
            action=MoldMovement.Action.EDIT,
        )
        self.assertIn("ABC-100-01 → RENAMED-01", rename_history.note)

        response = self.client.patch(
            f"/api/molds/{self.stock.pk}/",
            {"asset_code": ""},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.json())
        self.assertEqual(response.json()["asset_code"], "ABC-100-01")
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.asset_code, "ABC-100-01")
        self.assertEqual(
            MoldMovement.objects.filter(
                mold=self.stock,
                action=MoldMovement.Action.EDIT,
            ).count(),
            2,
        )

    def test_asset_code_conflict_is_case_insensitive(self):
        response = self.client.patch(
            f"/api/molds/{self.stock.pk}/",
            {"asset_code": "abc-100-02"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("asset_code", response.json())
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.asset_code, "ABC-100-01")

    def test_product_name_can_be_cleared_and_resets_to_model_code(self):
        response = self.client.patch(
            f"/api/molds/{self.stock.pk}/",
            {"product_name": ""},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.json())
        self.model.refresh_from_db()
        self.assertEqual(self.model.product_name, self.model.code)
        movement = MoldMovement.objects.get(
            mold=self.stock,
            action=MoldMovement.Action.EDIT,
        )
        self.assertIn("产品名称：汽车密封圈 → ABC-100", movement.note)

    def test_is_active_and_initial_location_cannot_bypass_domain_operations(self):
        ignored = self.client.patch(
            f"/api/molds/{self.stock.pk}/",
            {"is_active": False},
            format="json",
        )
        self.assertEqual(ignored.status_code, 200)
        self.stock.refresh_from_db()
        self.assertTrue(self.stock.is_active)
        self.assertIsNotNone(self.stock.current_slot)
        self.assertFalse(MoldMovement.objects.filter(mold=self.stock).exists())

        rejected = self.client.patch(
            f"/api/molds/{self.stock.pk}/",
            {"slot_id": self.slot("J01", 2, zone="A", position=1).pk},
            format="json",
        )
        self.assertEqual(rejected.status_code, 400)
        self.assertIn("status", rejected.json())

    def test_stale_edit_cannot_revive_a_concurrently_deleted_mold(self):
        stale = MoldAsset.objects.select_related("mold_model").get(pk=self.stock.pk)
        serializer = MoldAssetSerializer(
            stale,
            data={"note": "过期页面修改"},
            partial=True,
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        archive_mold(self.stock, self.user)

        with self.assertRaisesMessage(drf_serializers.ValidationError, "已删除"):
            serializer.save()

        self.stock.refresh_from_db()
        self.assertFalse(self.stock.is_active)
        self.assertIsNone(self.stock.current_slot)
        self.assertEqual(
            MoldMovement.objects.filter(
                mold=self.stock,
                action=MoldMovement.Action.DELETE,
            ).count(),
            1,
        )

    def test_stale_edit_preserves_a_concurrent_status_transition(self):
        stale = MoldAsset.objects.select_related("mold_model").get(pk=self.stock.pk)
        serializer = MoldAssetSerializer(
            stale,
            data={"note": "状态变化后补充备注"},
            partial=True,
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        transition_mold(
            self.stock,
            MoldMovement.Action.LOAD_MACHINE,
            self.user,
            machine=self.machine,
        )

        updated = serializer.save()

        updated.refresh_from_db()
        self.assertEqual(updated.status, MoldAsset.Status.ON_MACHINE)
        self.assertEqual(updated.current_machine_id, self.machine.pk)
        self.assertIsNone(updated.current_slot)
        self.assertEqual(updated.notes, "状态变化后补充备注")

    def test_saved_main_image_can_be_cleared(self):
        image_bytes = base64.b64decode(
            "R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw=="
        )
        with tempfile.TemporaryDirectory() as media_root, self.settings(MEDIA_ROOT=media_root):
            uploaded = self.client.patch(
                f"/api/molds/{self.stock.pk}/",
                {
                    "image": SimpleUploadedFile(
                        "mold.gif", image_bytes, content_type="image/gif"
                    )
                },
                format="multipart",
            )
            self.assertEqual(uploaded.status_code, 200, uploaded.json())
            self.stock.refresh_from_db()
            saved_path = self.stock.main_image.path
            self.assertTrue(os.path.exists(saved_path))

            with self.captureOnCommitCallbacks(execute=True):
                cleared = self.client.patch(
                    f"/api/molds/{self.stock.pk}/",
                    {"remove_image": "true"},
                    format="multipart",
                )
            self.assertEqual(cleared.status_code, 200, cleared.json())
            self.stock.refresh_from_db()
            self.assertFalse(self.stock.main_image)
            self.assertFalse(os.path.exists(saved_path))

    def test_delete_soft_deletes_releases_slot_and_allows_code_reuse(self):
        original_slot_id = self.stock.current_slot_id
        response = self.client.delete(
            f"/api/molds/{self.stock.pk}/",
            {"note": "货架误录，清空格位"},
            format="json",
        )

        self.assertEqual(response.status_code, 204)
        self.stock.refresh_from_db()
        self.assertFalse(self.stock.is_active)
        self.assertIsNone(self.stock.current_slot)
        self.assertIsNone(self.stock.current_machine)
        self.assertIsNone(self.stock.current_processor)
        movement = MoldMovement.objects.get(
            mold=self.stock,
            action=MoldMovement.Action.DELETE,
        )
        self.assertEqual(movement.from_slot_id, original_slot_id)
        self.assertIsNone(movement.to_slot_id)
        self.assertEqual(movement.note, "货架误录，清空格位")

        self.assertEqual(
            self.client.get(f"/api/molds/{self.stock.pk}/").status_code,
            404,
        )
        archived = self.client.get(
            f"/api/molds/{self.stock.pk}/", {"include_inactive": "true"}
        )
        self.assertEqual(archived.status_code, 200)
        self.assertFalse(archived.json()["is_active"])
        self.assertEqual(archived.json()["location_text"], "已删除")
        history = self.client.get(
            f"/api/molds/{self.stock.pk}/history/", {"include_inactive": "true"}
        )
        self.assertEqual(history.status_code, 200)
        self.assertEqual(history.json()[0]["action"], MoldMovement.Action.DELETE)

        replacement = self.client.post(
            "/api/molds/",
            {
                "asset_code": "abc-100-01",
                "mold_model_id": self.model.pk,
                "slot_id": original_slot_id,
            },
            format="json",
        )
        self.assertEqual(replacement.status_code, 201, replacement.json())
        self.assertEqual(replacement.json()["slot"]["id"], original_slot_id)
        self.assertEqual(
            MoldAsset.objects.filter(asset_code__iexact="ABC-100-01").count(),
            2,
        )
        self.assertEqual(
            self.client.delete(
                f"/api/molds/{self.stock.pk}/?include_inactive=true"
            ).status_code,
            404,
        )

    def test_delete_releases_machine_and_blocks_active_production(self):
        deleted = self.client.delete(f"/api/molds/{self.machine_mold.pk}/")
        self.assertEqual(deleted.status_code, 204)
        self.machine_mold.refresh_from_db()
        self.assertFalse(self.machine_mold.is_active)
        self.assertIsNone(self.machine_mold.current_machine)

        station = ProductionStation.objects.create(
            code="1",
            group=ProductionStation.Group.A,
            position_no=1,
            machine=self.machine,
        )
        ProductionRun.objects.create(
            station=station,
            order_no="ACTIVE-DELETE-01",
            specification="测试规格",
            mold=self.stock,
            order_quantity=100,
            planned_mold_count=10,
            status=ProductionRun.Status.PLANNED,
            created_by=self.user,
        )
        blocked = self.client.delete(f"/api/molds/{self.stock.pk}/")
        self.assertEqual(blocked.status_code, 400)
        self.assertIn("活动生产订单", str(blocked.json()))
        self.stock.refresh_from_db()
        self.assertTrue(self.stock.is_active)
        self.assertIsNotNone(self.stock.current_slot)

    def test_delete_lower_stack_requires_confirmation(self):
        lower = self.slot("J05", 1, zone="A", capacity=2, position=1, stack=1)
        upper = self.slot("J05", 1, zone="A", capacity=2, position=1, stack=2)
        switch_zone_stacking(lower.zone, True)
        lower_mold = self.create_mold("DELETE-LOWER-01", lower, allows_stacking=True)
        upper_mold = self.create_mold("DELETE-UPPER-01", upper)

        warning = self.client.delete(f"/api/molds/{lower_mold.pk}/")
        self.assertEqual(warning.status_code, 409)
        self.assertTrue(warning.json()["requires_confirmation"])
        lower_mold.refresh_from_db()
        self.assertTrue(lower_mold.is_active)
        self.assertEqual(lower_mold.current_slot_id, lower.pk)

        confirmed = self.client.delete(
            f"/api/molds/{lower_mold.pk}/",
            {"confirm_warnings": True},
            format="json",
        )
        self.assertEqual(confirmed.status_code, 204)
        lower_mold.refresh_from_db()
        upper_mold.refresh_from_db()
        self.assertFalse(lower_mold.is_active)
        self.assertEqual(upper_mold.current_slot_id, upper.pk)

    def test_create_supports_on_machine_and_customer_return_initial_states(self):
        on_machine = self.client.post(
            "/api/molds/",
            {
                "model_code": "INITIAL-MACHINE",
                "initial_status": MoldAsset.Status.ON_MACHINE,
                "machine_id": self.machine.pk,
            },
            format="json",
        )
        self.assertEqual(on_machine.status_code, 201, on_machine.json())
        self.assertEqual(on_machine.json()["status"], MoldAsset.Status.ON_MACHINE)
        self.assertEqual(on_machine.json()["machine"]["id"], self.machine.pk)
        self.assertIsNone(on_machine.json()["slot"])

        returned = self.client.post(
            "/api/molds/",
            {
                "model_code": "INITIAL-RETURNED",
                "initial_status": MoldAsset.Status.OUTSOURCED,
            },
            format="json",
        )
        self.assertEqual(returned.status_code, 201, returned.json())
        self.assertEqual(returned.json()["status_label"], "客户收回")
        self.assertEqual(returned.json()["location_text"], "客户收回")
        self.assertIsNone(returned.json()["slot"])
        self.assertIsNone(returned.json()["machine"])
        self.assertIsNone(returned.json()["processor"])

        for asset_code in (on_machine.json()["asset_code"], returned.json()["asset_code"]):
            mold = MoldAsset.objects.get(asset_code=asset_code)
            movement = MoldMovement.objects.get(mold=mold, action=MoldMovement.Action.CREATE)
            self.assertEqual(movement.to_status, mold.status)
            self.assertEqual(movement.to_machine_id, mold.current_machine_id)

    def test_history_endpoint_returns_location_changes(self):
        response = self.client.post(
            f"/api/molds/{self.stock.pk}/actions/send-out/",
            {"note": "客户收回"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status_label"], "客户收回")
        self.assertIsNone(response.json()["processor"])
        history = self.client.get(f"/api/molds/{self.stock.pk}/history/")
        self.assertEqual(history.status_code, 200)
        self.assertEqual(len(history.json()), 1)
        item = history.json()[0]
        self.assertEqual(item["action"], MoldMovement.Action.SEND_OUT)
        self.assertEqual(item["from_slot"]["id"], self.stock.current_slot_id)
        self.assertIsNone(item["to_processor"])
        self.assertEqual(item["action_label"], "客户收回")
        self.assertEqual(item["operator_name"], "shared")

    def test_noop_machine_and_customer_return_actions_are_rejected(self):
        same_machine = self.client.post(
            f"/api/molds/{self.machine_mold.pk}/actions/load-machine/",
            {"machine_id": self.machine.pk},
            format="json",
        )
        self.assertEqual(same_machine.status_code, 400)
        self.assertIn("不能重复上机", str(same_machine.json()))
        self.assertFalse(MoldMovement.objects.filter(mold=self.machine_mold).exists())

        second_machine = Machine.objects.create(code="MC-02", name="二号机")
        changed_machine = self.client.post(
            f"/api/molds/{self.machine_mold.pk}/actions/load-machine/",
            {"machine_id": second_machine.pk},
            format="json",
        )
        self.assertEqual(changed_machine.status_code, 200, changed_machine.json())
        self.assertEqual(changed_machine.json()["machine"]["id"], second_machine.pk)

        first_return = self.client.post(
            f"/api/molds/{self.stock.pk}/actions/send-out/",
            {},
            format="json",
        )
        self.assertEqual(first_return.status_code, 200)
        repeated_return = self.client.post(
            f"/api/molds/{self.stock.pk}/actions/send-out/",
            {},
            format="json",
        )
        self.assertEqual(repeated_return.status_code, 400)
        self.assertIn("无需重复操作", str(repeated_return.json()))
        self.assertEqual(
            MoldMovement.objects.filter(
                mold=self.stock,
                action=MoldMovement.Action.SEND_OUT,
            ).count(),
            1,
        )


class RackLayoutApiTests(SeededRackMixin, TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.rack = Rack.objects.get(code="J01")
        self.zone = RackZone.objects.get(
            level__rack=self.rack, level__level_no=1, code="A"
        )

    @staticmethod
    def _zone(payload, level_no, zone_code):
        level = next(item for item in payload["levels"] if item["level_no"] == level_no)
        return next(item for item in level["zones"] if item["code"] == zone_code)

    def test_stacking_toggle_controls_upper_slots_in_layout_and_slot_list(self):
        layout_url = f"/api/racks/{self.rack.pk}/layout/"
        stacking_url = (
            f"/api/racks/{self.rack.pk}/zones/{self.zone.pk}/stacking/"
        )

        initial = self.client.get(layout_url)
        self.assertEqual(initial.status_code, 200)
        initial_zone = self._zone(initial.json(), 1, "A")
        self.assertTrue(initial_zone["supports_stacking"])
        self.assertFalse(initial_zone["stacking_enabled"])
        self.assertEqual({slot["stack_level"] for slot in initial_zone["slots"]}, {1})
        initial_slots = self.client.get("/api/slots/", {"rack_id": self.rack.pk})
        self.assertEqual(initial_slots.status_code, 200)
        self.assertFalse(
            any(
                slot["zone_id"] == self.zone.pk and slot["stack_level"] == 2
                for slot in initial_slots.json()
            )
        )

        enabled = self.client.post(stacking_url, {"enabled": True}, format="json")
        self.assertEqual(enabled.status_code, 200)
        enabled_zone = self._zone(enabled.json(), 1, "A")
        self.assertTrue(enabled_zone["stacking_enabled"])
        self.assertEqual({slot["stack_level"] for slot in enabled_zone["slots"]}, {1, 2})
        enabled_slots = self.client.get("/api/slots/", {"rack_id": self.rack.pk})
        self.assertTrue(
            any(
                slot["zone_id"] == self.zone.pk and slot["stack_level"] == 2
                for slot in enabled_slots.json()
            )
        )

        disabled = self.client.post(stacking_url, {"enabled": False}, format="json")
        self.assertEqual(disabled.status_code, 200)
        disabled_zone = self._zone(disabled.json(), 1, "A")
        self.assertFalse(disabled_zone["stacking_enabled"])
        self.assertEqual({slot["stack_level"] for slot in disabled_zone["slots"]}, {1})

    def test_rack_summary_counts_only_current_visible_slots(self):
        response = self.client.get("/api/racks/")
        self.assertEqual(response.status_code, 200)
        racks = {item["code"]: item for item in response.json()}
        self.assertEqual(racks["J01"]["active_slot_count"], 24)
        self.assertEqual(racks["J06"]["active_slot_count"], 42)

        switch_zone_stacking(self.zone, True)
        response = self.client.get("/api/racks/")
        racks = {item["code"]: item for item in response.json()}
        self.assertEqual(racks["J01"]["active_slot_count"], 26)

    def test_upper_occupant_blocks_disabling_stacking_through_api(self):
        stacking_url = (
            f"/api/racks/{self.rack.pk}/zones/{self.zone.pk}/stacking/"
        )
        self.assertEqual(
            self.client.post(stacking_url, {"enabled": True}, format="json").status_code,
            200,
        )
        upper = self.slot("J01", 1, zone="A", position=1, stack=2)
        self.create_mold("API-UPPER-001", upper)

        blocked = self.client.post(stacking_url, {"enabled": False}, format="json")
        self.assertEqual(blocked.status_code, 400)
        self.assertIn("上叠位仍有模具", str(blocked.json()))
        self.zone.refresh_from_db()
        self.assertTrue(self.zone.stacking_enabled)

    def test_inactive_j06_storage_zone_rejects_stacking_toggle(self):
        rack = Rack.objects.get(code="J06")
        inactive = RackZone.objects.get(
            level__rack=rack, level__level_no=9, code="A"
        )
        response = self.client.post(
            f"/api/racks/{rack.pk}/zones/{inactive.pk}/stacking/",
            {"enabled": True},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("停用区域", str(response.json()))
