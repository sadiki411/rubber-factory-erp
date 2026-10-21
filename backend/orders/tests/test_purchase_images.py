import io
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from PIL import Image
from rest_framework.test import APITestCase

from molds.models import MoldModel
from orders.imports import commit_business_batch
from orders.models import BusinessImportBatch, BusinessRecordRevision, MaterialReceipt, ProductSpecification
from orders.purchase_images import _number_text, split_product_identity
from quality.models import QualityOrder


def photo(name="purchase.png"):
    buffer = io.BytesIO()
    Image.new("RGB", (750, 630), "white").save(buffer, "PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


def recognized(*, order_no="CG202610190009", specification="X115-004", material="EP6012", quantity=40000):
    return {
        "order_no": order_no, "order_date": "2026-10-19",
        "rows": [{"item_no": "1", "product_name": "平垫片", "specification": specification,
                  "material": material, "order_quantity": quantity, "due_date": "2026-10-31",
                  "unit_price": "0.1100", "amount": str(quantity * .11)}],
        "raw_text": "original OCR result", "warnings": ["请核对"],
    }


class PurchaseImageTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("purchase-importer", password="test")
        self.client.force_authenticate(self.user)
        self.media = tempfile.TemporaryDirectory()
        override = override_settings(MEDIA_ROOT=Path(self.media.name))
        override.enable()
        self.addCleanup(self.media.cleanup)
        self.addCleanup(override.disable)

    def recognize(self, drafts=None):
        drafts = drafts or [recognized()]
        with patch("orders.purchase_images.recognize_purchase_image", side_effect=drafts):
            response = self.client.post("/api/orders/imports/purchase-images/recognize/", {
                "files": [photo(f"order-{i}.png") for i in range(len(drafts))],
            }, format="multipart")
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def preview(self, draft, **kwargs):
        response = self.client.post("/api/orders/imports/purchase-images/preview/", {
            "token": draft["token"], "documents": draft["documents"], "confirmed": True, **kwargs,
        }, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def commit(self, preview):
        return commit_business_batch(BusinessImportBatch.objects.get(pk=preview["token"]), self.user)

    def test_recognition_is_only_a_draft_and_original_images_are_retained(self):
        original = photo().read()
        draft = self.recognize()
        self.assertTrue(draft["requires_human_confirmation"])
        self.assertEqual((QualityOrder.objects.count(), ProductSpecification.objects.count()), (0, 0))
        batch = BusinessImportBatch.objects.get(pk=draft["token"])
        with batch.original_file.open("rb") as saved:
            with ZipFile(saved) as archive:
                self.assertEqual(archive.read("01.png"), original)
        with self.assertRaisesMessage(ValueError, "核对识别内容"):
            self.commit(draft)

    def test_five_purchase_images_import_all_lines_without_inventing_history(self):
        data = [
            recognized(),
            recognized(order_no="CG202610190007", specification="X115-002", material="EP7521", quantity=30000),
            recognized(order_no="CG202610080004", specification="X002-184", material="EP4512", quantity=30000),
            recognized(order_no="CG202610060007", specification="X112-001", material="EP8018F", quantity=200000),
            recognized(order_no="CG202610060002", specification="X017-042", material="SBR70W02", quantity=100000),
        ]
        preview = self.preview(self.recognize(data))
        self.assertEqual(preview["error_count"], 0, preview["issues"])
        result = self.commit(preview)
        self.assertEqual((result["created"]["orders"], result["created"]["product_specifications"]), (5, 5))
        self.assertEqual(sum(QualityOrder.objects.values_list("order_quantity", flat=True)), 400000)
        for order in QualityOrder.objects.all():
            self.assertEqual(order.source_system, "艾斯霖")
            self.assertIsNotNone(order.product_specification_id)
            self.assertEqual(order.process_card_text, "")
            self.assertIsNone(order.process_card_count)
            self.assertEqual(order.raw_data["unit"], "PCS")
            self.assertEqual(order.status, "OPEN")
        from production.models import ProductionRun
        from quality.models import ProcessCard, QualityShipment, QualityShipmentBatch
        from inventory.models import InventoryBatch
        for model in [ProductionRun, ProcessCard, QualityShipment, QualityShipmentBatch, InventoryBatch]:
            self.assertEqual(model.objects.count(), 0)

    def test_reupload_with_different_image_name_is_idempotent(self):
        self.commit(self.preview(self.recognize()))
        second = self.preview(self.recognize())
        self.assertEqual(second["error_count"], 0, second["issues"])
        result = self.commit(second)
        self.assertEqual(result["created"]["orders"], 0)
        self.assertEqual(result["skipped"]["orders"], 1)
        self.assertEqual((QualityOrder.objects.count(), ProductSpecification.objects.count()), (1, 1))

    def test_existing_product_process_fields_and_manual_order_status_are_preserved(self):
        product = ProductSpecification.objects.create(
            specification="X115-004", material="EP6012", product_name="手工命名",
            primary_curing="60s", actual_cut_weight="21", cut_weight="20", mold_no="MANUAL-MOLD",
        )
        self.commit(self.preview(self.recognize()))
        order = QualityOrder.objects.get()
        self.assertEqual(order.product_specification_id, product.pk)
        order.status = "COMPLETED"
        order.production_required = False
        order.save()
        draft = self.recognize([recognized(quantity=42000)])
        self.commit(self.preview(draft))
        order.refresh_from_db()
        product.refresh_from_db()
        self.assertEqual((product.product_name, product.primary_curing, product.actual_cut_weight, product.cut_weight, product.mold_no), ("手工命名", "60s", "21", "20", "MANUAL-MOLD"))
        self.assertEqual((order.status, order.production_required, order.order_quantity), ("COMPLETED", False, 42000))

    def test_same_purchase_number_from_another_source_cannot_be_claimed(self):
        old = QualityOrder.objects.create(order_no="CG202610190009", item_no="1", specification="X115-004", material="EP6012", order_quantity=5, source_system="OTHER", created_by=self.user)
        self.commit(self.preview(self.recognize()))
        old.refresh_from_db()
        self.assertEqual((old.order_quantity, old.source_system), (5, "OTHER"))
        self.assertEqual(QualityOrder.objects.count(), 2)

    def test_purchase_images_do_not_claim_or_modify_historical_material_receipts(self):
        receipt = MaterialReceipt.objects.create(
            order_no="CG202610190009", item_no="1", source_system="OTHER",
            specification="OTHER-SPEC", material="OTHER-MATERIAL", weight_kg="12.5",
        )
        before = MaterialReceipt.objects.values().get(pk=receipt.pk)
        self.commit(self.preview(self.recognize()))
        self.assertEqual(MaterialReceipt.objects.values().get(pk=receipt.pk), before)
        self.assertFalse(BusinessRecordRevision.objects.filter(record_type="MATERIAL_RECEIPT").exists())

    def test_multiline_purchase_reuses_product_and_does_not_collapse_order_items(self):
        data = recognized()
        data["rows"].append({**data["rows"][0], "item_no": "2"})
        preview = self.preview(self.recognize([data]))
        self.commit(preview)
        self.assertEqual((QualityOrder.objects.count(), ProductSpecification.objects.count()), (2, 1))

    def test_duplicate_purchase_lines_block_the_entire_batch(self):
        preview = self.preview(self.recognize([recognized(), recognized()]))
        self.assertGreater(preview["error_count"], 0)
        with self.assertRaisesMessage(ValueError, "预检存在错误"):
            self.commit(preview)
        self.assertEqual(QualityOrder.objects.count(), 0)

    def test_ambiguous_existing_product_blocks_without_creating_duplicate(self):
        for name in ["one", "two"]:
            ProductSpecification.objects.create(specification="X115-004", material="EP6012", product_name=name)
        preview = self.preview(self.recognize())
        self.assertGreater(preview["error_count"], 0)
        with self.assertRaises(ValueError):
            self.commit(preview)
        self.assertEqual(ProductSpecification.objects.count(), 2)

    def test_explicit_product_choice_resolves_duplicates_without_modifying_either(self):
        first = ProductSpecification.objects.create(
            specification="x115-004", material="EP6012", product_name="三角垫", actual_cut_weight="21",
        )
        second = ProductSpecification.objects.create(
            specification="X115-004", material="EP6012", product_name="另一档案", actual_cut_weight="17",
        )
        before = list(ProductSpecification.objects.order_by("pk").values())
        draft = self.recognize()
        ambiguous = self.preview(draft)
        product_row = next(row for row in ambiguous["rows"] if row["record_type"] == "PRODUCT_SPECIFICATION")
        self.assertEqual([item["id"] for item in product_row["product_candidates"]], [first.pk, second.pk])
        draft["documents"][0]["rows"][0]["product_specification_id"] = second.pk
        preview = self.preview(draft)
        self.assertEqual(preview["error_count"], 0, preview["issues"])
        self.commit(preview)
        self.assertEqual(QualityOrder.objects.get().product_specification_id, second.pk)
        self.assertEqual(list(ProductSpecification.objects.order_by("pk").values()), before)

    def test_wrong_or_inactive_product_choice_blocks_instead_of_guessing(self):
        wrong = ProductSpecification.objects.create(specification="OTHER", material="EP6012")
        inactive = ProductSpecification.objects.create(specification="X115-004", material="EP6012", is_active=False)
        draft = self.recognize()
        for selected in (wrong.pk, inactive.pk, 999999):
            with self.subTest(selected=selected):
                draft["documents"][0]["rows"][0]["product_specification_id"] = selected
                preview = self.preview(draft)
                self.assertGreater(preview["error_count"], 0)
                self.assertIn("规格、材质", str(preview["issues"]))
                with self.assertRaises(ValueError):
                    self.commit(preview)
        self.assertEqual(QualityOrder.objects.count(), 0)

    def test_selected_product_is_revalidated_before_commit(self):
        product = ProductSpecification.objects.create(specification="X115-004", material="EP6012")
        draft = self.recognize()
        draft["documents"][0]["rows"][0]["product_specification_id"] = product.pk
        preview = self.preview(draft)
        self.assertEqual(preview["error_count"], 0)
        product.material = "CHANGED"
        product.save()
        with self.assertRaisesMessage(ValueError, "提交前复检失败"):
            self.commit(preview)
        self.assertEqual(QualityOrder.objects.count(), 0)

    def test_unique_existing_product_matches_case_insensitively(self):
        product = ProductSpecification.objects.create(specification="x115-004", material="ep6012")
        self.commit(self.preview(self.recognize()))
        self.assertEqual(QualityOrder.objects.get().product_specification_id, product.pk)
        self.assertEqual(ProductSpecification.objects.count(), 1)

    def test_manual_correction_is_saved_with_ocr_original_and_price_is_not_financial_fact(self):
        draft = self.recognize()
        draft["documents"][0]["rows"][0]["specification"] = "X115-005"
        self.commit(self.preview(draft))
        order = QualityOrder.objects.get()
        self.assertEqual(order.specification, "X115-005")
        self.assertEqual(order.raw_data["ocr_original"]["rows"][0]["specification"], "X115-004")
        self.assertEqual(order.raw_data["confirmed_row"]["unit_price"], "0.1100")
        self.assertEqual(BusinessRecordRevision.objects.filter(record_type="ORDER").count(), 1)

    def test_exact_mold_model_can_link_but_missing_model_is_not_created(self):
        model = MoldModel.objects.create(code="X115-004")
        self.commit(self.preview(self.recognize()))
        product = ProductSpecification.objects.get()
        self.assertEqual((product.mold_model_id, product.mold_no), (model.pk, model.code))
        self.assertEqual(MoldModel.objects.count(), 1)

    def test_confirmation_document_identity_and_ownership_are_enforced(self):
        draft = self.recognize()
        url = "/api/orders/imports/purchase-images/preview/"
        self.assertEqual(self.client.post(url, {**draft, "confirmed": False}, format="json").status_code, 400)
        self.assertEqual(self.client.post(url, {**draft, "confirmed": True, "documents": []}, format="json").status_code, 400)
        another = get_user_model().objects.create_user("other-user")
        self.client.force_authenticate(another)
        self.assertEqual(self.client.post(url, {**draft, "confirmed": True}, format="json").status_code, 404)

    def test_quantity_dates_and_money_must_validate_before_commit(self):
        draft = self.recognize()
        for field, value in [("order_quantity", 0), ("order_quantity", "not a number"), ("due_date", "2026-99-31")]:
            documents = [{**draft["documents"][0], "rows": [{**draft["documents"][0]["rows"][0], field: value}]}]
            response = self.client.post("/api/orders/imports/purchase-images/preview/", {"token": draft["token"], "documents": documents, "confirmed": True}, format="json")
            self.assertEqual(response.status_code, 400, response.data)
        draft["documents"][0]["rows"][0]["amount"] = "1"
        preview = self.preview(draft)
        self.assertGreater(preview["error_count"], 0)

    def test_file_validation_rejects_nonimages_and_too_many_uploads(self):
        for files in [[SimpleUploadedFile("bad.png", b"not an image")], [photo() for _ in range(11)]]:
            response = self.client.post("/api/orders/imports/purchase-images/recognize/", {"files": files}, format="multipart")
            self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(BusinessImportBatch.objects.count(), 0)

    def test_recognition_outage_does_not_leave_partial_order_or_preview(self):
        with patch("orders.purchase_images.recognize_purchase_image", side_effect=ValueError("识别不可用")):
            response = self.client.post("/api/orders/imports/purchase-images/recognize/", {"files": [photo()]}, format="multipart")
        self.assertEqual(response.status_code, 400)
        self.assertEqual((BusinessImportBatch.objects.count(), QualityOrder.objects.count()), (0, 0))

    def test_split_wrapped_product_identity_without_correcting_ambiguous_letters(self):
        self.assertEqual(split_product_identity("X017-042[SBR70W0\n2]"), ("X017-042", "SBR70W02"))
        self.assertEqual(split_product_identity("X112-001[EP8018F\n]"), ("X112-001", "EP8018F"))
        self.assertEqual(split_product_identity("X115-004 EPG012"), ("", ""))

    def test_ambiguous_decimal_comma_is_not_silently_tenfold_count(self):
        self.assertIsNone(_number_text("30000,0", integer=True))
        self.assertEqual(_number_text("30,000.0", integer=True), 30000)
        self.assertEqual(_number_text("30000.0", integer=True), 30000)
