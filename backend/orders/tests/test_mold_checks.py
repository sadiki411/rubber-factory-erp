import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import override_settings
from rest_framework.test import APITestCase

from molds.models import Machine, MoldAsset, MoldModel
from molds.tests.helpers import SeededRackMixin
from orders.imports import preview_business_workbook
from orders.models import BusinessImportBatch, BusinessRecordRevision, ProductSpecification
from orders.mold_checks import build_mold_check
from quality.models import QualityOrder
from .test_imports import factory_workbook, product_workbook, upload


class ImportMoldCheckTests(SeededRackMixin, APITestCase):
    def setUp(self):
        self.client.force_authenticate(self.user)
        media = tempfile.TemporaryDirectory()
        settings = override_settings(MEDIA_ROOT=Path(media.name))
        settings.enable()
        self.addCleanup(media.cleanup)
        self.addCleanup(settings.disable)
        self.model = MoldModel.objects.create(code="FACTORY-MODEL", product_name="测试模具")

    def preview(self, **kwargs):
        result = preview_business_workbook(upload("factory.xlsx", factory_workbook(include_criteria=False, **kwargs)), self.user)
        self.assertEqual(result["error_count"], 0, result["issues"])
        return result

    def choose(self, preview, model_id):
        return self.client.post(f'/api/orders/imports/{preview["token"]}/mold-check/', {
            "binding_key": preview["mold_check"]["rows"][0]["binding_key"], "mold_model_id": model_id,
        }, format="json")

    def commit(self, preview):
        check = preview.get("cut_length_check") or {}
        return self.client.post("/api/orders/imports/commit/", {
            "token": preview["token"], "cut_length_acknowledged": True, "cut_length_digest": check.get("digest", ""),
        }, format="json")

    def test_choice_is_draft_until_successful_commit_and_reused_by_new_order(self):
        preview = self.preview()
        response = self.choose(preview, self.model.pk)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["mold_check"]["rows"][0]["selection_source"], "MANUAL")
        self.assertFalse(ProductSpecification.objects.exists())
        self.assertFalse(QualityOrder.objects.exists())
        committed = self.commit(preview)
        self.assertEqual(committed.status_code, 200, committed.data)
        product = ProductSpecification.objects.get()
        self.assertEqual(product.mold_model_id, self.model.pk)
        self.assertTrue(BusinessRecordRevision.objects.filter(record_id=product.pk, changes__has_key="mold_model").exists())
        repeated = self.preview(order_no="NEW-ORDER")
        row = repeated["mold_check"]["rows"][0]
        self.assertEqual((row["product_id"], row["mold_model_id"], row["selection_source"]), (product.pk, self.model.pk, "REMEMBERED"))
        self.assertEqual(self.commit(repeated).status_code, 200)
        self.assertEqual(ProductSpecification.objects.count(), 1)
        self.assertEqual(set(QualityOrder.objects.values_list("product_specification_id", flat=True)), {product.pk})

    def test_unlinked_or_no_assets_only_warns_and_does_not_block_import(self):
        preview = self.preview()
        self.assertEqual(preview["mold_check"]["rows"][0]["status"], "UNLINKED")
        self.assertEqual(self.commit(preview).status_code, 200)
        repeated = self.preview(order_no="CHOOSE-NO-ASSETS")
        chosen = self.choose(repeated, self.model.pk)
        self.assertEqual(chosen.data["mold_check"]["rows"][0]["status"], "NO_ASSET")
        self.assertEqual(self.commit(repeated).status_code, 200)
        self.assertEqual(MoldAsset.objects.count(), 0)
        self.assertEqual(MoldModel.objects.count(), 1)

    def test_current_state_lists_all_assets_and_on_machine_is_not_missing(self):
        preview = self.preview()
        self.choose(preview, self.model.pk)
        machine = Machine.objects.create(code="M1", name="1号")
        mounted = MoldAsset.objects.create(asset_code="TEST-01", mold_model=self.model, status="ON_MACHINE", current_machine=machine)
        returned = MoldAsset.objects.create(asset_code="TEST-02", mold_model=self.model, status="OUTSOURCED")
        url = f'/api/orders/imports/{preview["token"]}/mold-check/'
        check = self.client.post(url, {}, format="json").data["mold_check"]
        self.assertEqual(check["rows"][0]["status"], "ON_MACHINE")
        self.assertEqual(check["alert_count"], 0)
        self.assertEqual(len(check["rows"][0]["assets"]), 2)
        self.assertEqual(check["rows"][0]["assets"][0]["location"], "M1")
        mounted.status = "OUTSOURCED"
        mounted.current_machine = None
        mounted.save()
        check = self.client.post(url, {}, format="json").data["mold_check"]
        self.assertEqual(check["rows"][0]["status"], "CUSTOMER_RETURNED")
        stock = self.create_mold("TEST-03", self.slot("J01", 1), model=self.model)
        check = self.client.post(url, {}, format="json").data["mold_check"]
        self.assertEqual(check["rows"][0]["status"], "IN_STOCK")
        self.assertIn(stock.current_slot.display_code, str(check))
        returned.refresh_from_db()
        self.assertEqual(returned.status, "OUTSOURCED")

    def test_latest_state_is_saved_at_commit_and_history_does_not_recompute(self):
        preview = self.preview()
        self.choose(preview, self.model.pk)
        asset = self.create_mold("TEST-HISTORY", self.slot("J01", 1), model=self.model)
        self.assertEqual(self.commit(preview).status_code, 200)
        url = f'/api/orders/imports/history/{preview["token"]}/'
        snapshot = self.client.get(url).data["mold_check"]
        self.assertEqual(snapshot["rows"][0]["status"], "IN_STOCK")
        asset.status, asset.current_slot = "OUTSOURCED", None
        asset.save()
        self.assertEqual(self.client.get(url).data["mold_check"], snapshot)
        self.assertEqual(self.client.post(f'/api/orders/imports/{preview["token"]}/mold-check/', {}, format="json").status_code, 400)

    def test_customer_exact_match_cannot_replace_saved_model(self):
        first = self.preview()
        self.choose(first, self.model.pk)
        self.assertEqual(self.commit(first).status_code, 200)
        customer = MoldModel.objects.create(code="TEST-MOLD-01", product_name="客户编号撞到另一型号")
        repeated = self.preview(order_no="CONFLICT-ORDER")
        row = repeated["mold_check"]["rows"][0]
        self.assertEqual(row["mold_model_id"], self.model.pk)
        self.assertTrue(row["notices"])
        self.assertEqual(self.commit(repeated).status_code, 200)
        self.assertEqual(ProductSpecification.objects.get().mold_model_id, self.model.pk)
        self.assertNotEqual(customer.pk, self.model.pk)

    def test_unchanged_import_can_save_manual_link_without_reimporting_quantity(self):
        preview = self.preview()
        self.assertEqual(self.commit(preview).status_code, 200)
        repeated = self.preview()
        self.assertTrue(all(row["action"] == "SKIP" for row in repeated["rows"]))
        self.choose(repeated, self.model.pk)
        self.assertEqual(self.commit(repeated).status_code, 200)
        self.assertEqual(QualityOrder.objects.count(), 1)
        self.assertEqual(QualityOrder.objects.get().order_quantity, 2400)
        self.assertEqual(ProductSpecification.objects.get().mold_model_id, self.model.pk)

    def test_clearing_draft_does_not_delete_saved_association(self):
        preview = self.preview()
        self.choose(preview, self.model.pk)
        response = self.choose(preview, None)
        self.assertEqual(response.data["mold_check"]["rows"][0]["status"], "UNLINKED")
        self.assertEqual(self.commit(preview).status_code, 200)
        self.assertIsNone(ProductSpecification.objects.get().mold_model_id)

    def test_invalid_selection_owner_and_read_only_are_guarded_without_poisoning_preview(self):
        preview = self.preview()
        for value in ("1", True, 99999):
            self.assertEqual(self.choose(preview, value).status_code, 400)
        other = get_user_model().objects.create_user(username="other")
        self.client.force_authenticate(other)
        self.assertEqual(self.choose(preview, self.model.pk).status_code, 404)
        self.user.groups.add(Group.objects.get_or_create(name="GPT只读测试")[0])
        self.client.force_authenticate(self.user)
        self.assertEqual(self.choose(preview, self.model.pk).status_code, 403)
        self.user.groups.clear()
        self.client.force_authenticate(self.user)
        self.assertEqual(BusinessImportBatch.objects.get(pk=preview["token"]).status, "PREVIEWED")
        self.assertEqual(self.commit(preview).status_code, 200)

    def test_inactivated_selection_does_not_block_order_or_get_written(self):
        preview = self.preview()
        self.choose(preview, self.model.pk)
        self.model.is_active = False
        self.model.save()
        response = self.commit(preview)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["mold_link_warnings"])
        self.assertIsNone(ProductSpecification.objects.get().mold_model_id)

    def test_no_mold_writes_on_length_confirmation_failure(self):
        preview = self.preview()
        self.choose(preview, self.model.pk)
        response = self.client.post("/api/orders/imports/commit/", {"token": preview["token"]}, format="json")
        self.assertEqual(response.status_code, 409)
        self.assertFalse(ProductSpecification.objects.exists())
        self.assertEqual(BusinessImportBatch.objects.get(pk=preview["token"]).status, "PREVIEWED")
        self.assertEqual(self.commit(preview).status_code, 200)

    def test_concurrent_manual_change_is_preserved_with_warning(self):
        first = self.preview()
        self.commit(first)
        product = ProductSpecification.objects.get()
        later = self.preview(order_no="CONCURRENT")
        self.choose(later, self.model.pk)
        changed = MoldModel.objects.create(code="OTHER-MODEL", product_name="另一型号")
        product.mold_model = changed
        product.save()
        response = self.commit(later)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["mold_link_warnings"])
        product.refresh_from_db()
        self.assertEqual(product.mold_model_id, changed.pk)

    def test_manual_choice_wins_over_customer_match_for_existing_unlinked_product(self):
        first = self.preview()
        self.assertEqual(self.commit(first).status_code, 200)
        customer = MoldModel.objects.create(code="TEST-MOLD-01", product_name="客户编号匹配型号")
        later = self.preview(order_no="CHOOSE-INSTEAD-OF-CUSTOMER")
        self.assertEqual(later["mold_check"]["rows"][0]["mold_model_id"], customer.pk)
        self.assertEqual(self.choose(later, self.model.pk).status_code, 200)
        response = self.commit(later)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(response.data["mold_link_warnings"])
        self.assertEqual(ProductSpecification.objects.get().mold_model_id, self.model.pk)

    def test_import_and_mold_link_roll_back_together_on_failure(self):
        first = self.preview()
        self.assertEqual(self.commit(first).status_code, 200)
        later = self.preview(order_no="ROLLBACK-CHOICE")
        self.choose(later, self.model.pk)
        with patch("orders.mold_checks.record_revision", side_effect=ValueError("模拟关联审计保存失败")):
            response = self.commit(later)
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIsNone(ProductSpecification.objects.get().mold_model_id)
        self.assertEqual(QualityOrder.objects.count(), 1)
        self.assertEqual(BusinessImportBatch.objects.get(pk=later["token"]).status, "FAILED")
        retry = self.preview(order_no="ROLLBACK-CHOICE")
        self.choose(retry, self.model.pk)
        self.assertEqual(self.commit(retry).status_code, 200)

    def test_stopped_model_is_not_treated_as_available_even_with_stock(self):
        preview = self.preview()
        self.choose(preview, self.model.pk)
        self.create_mold("INACTIVE-MODEL-ASSET", self.slot("J01", 1), model=self.model)
        self.commit(preview)
        self.model.is_active = False
        self.model.save()
        repeated = self.preview(order_no="STOPPED-MODEL")
        self.assertEqual(repeated["mold_check"]["rows"][0]["status"], "MODEL_INACTIVE")
        self.assertEqual(self.commit(repeated).status_code, 200)

    def test_multiple_product_candidates_are_not_guessed_from_saved_mold(self):
        first = self.preview()
        self.choose(first, self.model.pk)
        self.commit(first)
        ProductSpecification.objects.create(specification="TEST-SPEC-B", material=ProductSpecification.objects.get().material, mold_no="TEST-MOLD-01")
        repeated = preview_business_workbook(upload("ambiguous.xlsx", factory_workbook(include_criteria=False, order_no="AMBIGUOUS-PRODUCT")), self.user)
        row = repeated["mold_check"]["rows"][0]
        self.assertIsNone(row["product_id"])
        self.assertFalse(row["can_bind"])
        self.assertEqual(row["status"], "UNRESOLVED_PRODUCT")

    def test_non_order_import_has_no_mold_check(self):
        preview = preview_business_workbook(upload("product.xlsx", product_workbook()), self.user)
        self.assertIsNone(preview["mold_check"])

    def test_saved_mold_beyond_first_three_candidates_does_not_hide_ambiguity(self):
        from orders.imports import _product_match

        for index in range(4):
            ProductSpecification.objects.create(
                specification="MANY", material="N7200", mold_no="CUSTOMER-MOLD",
                mold_model=self.model if index == 3 else None,
            )
        record = {
            "source_type": BusinessImportBatch.SourceType.FACTORY_WORK_CONTACT,
            "source_key": "new-source", "specification": "MANY", "material": "N7200",
            "customer_product_no": "", "mold_no": "CUSTOMER-MOLD", "normalized_key": "",
        }
        self.assertIsNone(_product_match(record))
        self.assertTrue(record["ambiguous_product_identity"])

    def test_bulk_check_uses_fixed_queries(self):
        preview = self.preview()
        self.choose(preview, self.model.pk)
        self.commit(preview)
        preview = self.preview(order_no="BULK")
        records = BusinessImportBatch.objects.get(pk=preview["token"]).payload["rows"]
        order = next(row for row in records if row["record_type"] == "ORDER")
        records += [dict(order, row_key=f"extra-{index}", order_no=f"BULK-{index}") for index in range(500)]
        with self.assertNumQueries(3):
            result = build_mold_check(records)
        self.assertEqual(len(result["rows"]), 1)
        self.assertEqual(len(result["rows"][0]["orders"]), 501)
