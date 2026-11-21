from datetime import date
from io import BytesIO
import tempfile
from unittest.mock import patch
import uuid

from django.contrib.auth.models import Permission
from django.core.management import call_command
from django.test import override_settings
from openpyxl import load_workbook
from reportlab.platypus import Paragraph
from rest_framework.test import APITestCase

from flowcards import preparation as prep, services
from flowcards.models import InternalCard, PrintJob
from flowcards.pdf import render_cards
from orders import customer_inspection as ci
from erp.inspection_labels import ITEM_NOTES, inspection_item_label
from orders.imports import preview_business_workbook, commit_business_batch
from orders.models import BusinessImportBatch, CustomerInspectionReview, OrderInspectionSource, ProductInspectionCriterion, ProductSpecification
from quality.models import QualityOrder
from production.models import ProductionStation
from . import test_imports as fixtures


@override_settings(FLOW_CARDS_PREPARATION_ENABLED=True, FLOW_CARDS_WRITE_ENABLED=True)
class CustomerInspectionTests(APITestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        media = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(override_settings(MEDIA_ROOT=media))
        self.user = get_user_model().objects.create_user(username="xiangbang-synthetic")
        self.client.force_authenticate(self.user)

    def order(self, no="CI-1", customer="J", high=65, project="XP-1", include=True, day=2, bake=False, extra=()):
        book = load_workbook(BytesIO(fixtures.factory_workbook(
            order_no=no, project_no=project, include_criteria=include, issued_on=date(2026, 8, day),
            primary_curing="时间160-180min 温度175-180℃",
        )))
        book.worksheets[0]["L4"] = "时间120min温度150°C" if bake else "时间0min温度0°C"
        if include:
            sheet = book.worksheets[1]
            sheet.delete_rows(3)
            sheet.append([no, 1, project, customer, "6001", "F02", "HD", 61, 69, "度"])
            sheet.append([no, 1, project, customer, "2001", "F02", "HD", 61, high, "度"])
            sheet.append([no, 1, project, customer, "2001", "F02", "FLASH", 0, .24, "mm"])
            for category, item, lower, upper, unit in extra:
                sheet.append([no, 1, project, customer, category, "F02", item, lower, upper, unit])
            book.move_sheet(sheet, -1)
        result = preview_business_workbook(fixtures.upload(f"{no}.xlsx", fixtures.workbook_bytes(book)), self.user)
        self.assertEqual(result["error_count"], 0, result["issues"])
        batch = BusinessImportBatch.objects.get(pk=result["token"])
        commit_business_batch(batch, self.user, cut_length_acknowledged=True,
                              cut_length_digest=(result.get("cut_length_check") or {}).get("digest", ""))
        return QualityOrder.objects.get(order_no=no), batch, result

    def endpoint(self, order):
        return f"/api/orders/orders/{order.pk}/customer-inspection/"

    def read(self, order):
        response = self.client.get(self.endpoint(order))
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()["inspection"]

    def grant(self):
        self.user.user_permissions.add(Permission.objects.get(codename="maintain_product_standard"))
        self.client.force_authenticate(type(self.user).objects.get(pk=self.user.pk))

    def test_ingest_preserves_categories_and_exact_seconds_can_print_without_workflow(self):
        order, batch, preview = self.order()
        self.assertEqual([row["customer_code"] for row in preview["rows"] if row["record_type"] == "ORDER"], ["J"])
        self.assertEqual(order.customer_code, "J")
        data = self.read(order)
        self.assertEqual((data["source"], data["customer_code"], data["status"]), ("翔邦", "J", "PENDING"))
        self.assertEqual([(g["category"], g["rows"][0]["upper"]) for g in data["groups"]], [("2001", "65"), ("6001", "69")])
        self.assertEqual(data["groups"][0]["rows"][1]["lower"], "0")
        self.assertEqual(data["groups"][0]["rows"][1]["item"], "FLASH")
        self.assertEqual(data["reference"]["primary"]["time_lower"], "160")
        self.assertEqual(data["reference"]["primary"]["unit"], "s")
        self.assertEqual(data["reference"]["secondary"]["state"], "NOT_REQUIRED")
        with patch("orders.customer_inspection_pdf.Paragraph", wraps=Paragraph) as paragraph:
            raw = self.client.get(self.endpoint(order) + "print/")
        printed = [call.args[0] for call in paragraph.call_args_list]
        self.assertIn("HD（硬度）", printed)
        self.assertIn("FLASH（毛边，量法待确认）", printed)
        self.assertIn("J / 2001 / F02", printed)
        self.assertIn("J / 6001 / F02", printed)
        self.assertEqual(raw.status_code, 200, raw.content[:100])
        self.assertTrue(raw.content.startswith(b"%PDF"))
        self.assertFalse(InternalCard.objects.exists())
        self.assertFalse(PrintJob.objects.exists())
        history = self.client.get(f"/api/orders/imports/history/{batch.pk}/")
        self.assertEqual(history.status_code, 200)
        flash = next(r for r in history.json()["rows"] if r.get("inspection_item") == "FLASH")
        self.assertEqual([flash["inspection_lower"], flash["inspection_upper"], flash["inspection_unit"]], ["0", "0.24", "mm"])
        self.assertEqual(self.read(order)["digest"], data["digest"])

    def test_print_labels_do_not_rename_categories_or_unknown_items(self):
        self.assertEqual(ITEM_NOTES, {
            "HD": "硬度", "SG": "密度", "ID": "内径", "OD": "外径", "THK": "厚度",
            "C/S-A": "轴向线径，方向待核", "C/S-R": "径向线径，方向待核", "MISMATCH": "合模错位",
            "FLASHT": "毛边厚度", "FLASHLEN": "毛边伸出长度", "FLASH": "毛边，量法待确认",
            "[1]": "图纸尺寸1，位置见图纸", "[2]": "图纸尺寸2，位置见图纸", "[3]": "图纸尺寸3，位置见图纸",
        })
        for name in ("2001", "6001", "UNKNOWN", "HD（人工释义）", "原有中文"):
            self.assertEqual(inspection_item_label(name), name)
        self.assertEqual(inspection_item_label("ｈｄ"), "ｈｄ（硬度）")
        self.assertEqual(inspection_item_label("C/S-A"), "C/S-A（轴向线径，方向待核）")

    def adoption_body(self, order, data, **patch):
        return {"request_key":uuid.uuid4().hex, "product_id":order.product_specification_id, "applicable_order_id":order.pk,
                "customer_reference":{"key":data["adoption_key"]}, "customer_key":data["customer_code"], "source_kind":"CUSTOMER", "source":"本单原表",
                "whole_mold":"NOT_REQUIRED", "appearance_ids":list(services.AppearanceIssue.objects.values_list("pk", flat=True)),
                "dimension_count":0, "route":{"confirmed":True,"molding":True,"patrol":True,"deburr":True,"inspection":True},
                "process_parameters":{"mold_temperature_range":{"lower":"175","upper":"180","unit":"℃","state":"CONFIRMED"},
                                      "primary_curing_time":{"lower":"160","upper":"180","unit":"s","state":"CONFIRMED"}},
                "packaging":{"simple_entry":True}, "bake":{"state":"NOT_APPLICABLE"}, "criteria":[], **patch}

    def test_adopt_reuses_imported_6001_row_without_bake_or_history_changes(self):
        order, _, _ = self.order()
        data = self.read(order)
        self.grant()
        raw = ProductInspectionCriterion.objects.get(order=order, inspection_item="HD", category="6001")
        original_raw = raw.raw_data.copy()
        count = ProductInspectionCriterion.objects.count()
        body = self.adoption_body(order, data)
        resp = self.client.post("/api/flow-cards/standards/", body, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self.client.post(self.endpoint(order), {"action":"confirm","digest":data["digest"]}, format="json").status_code, 200)
        resp = self.client.post("/api/flow-cards/standards/", body, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        standard_id = resp.data["id"]
        criteria = resp.data["snapshot"]["criteria"]
        self.assertEqual([(c["id"], c["stage"], c["lower"], c["upper"]) for c in criteria], [(raw.pk, "MOLDING_HARDNESS", "61", "69")])
        self.assertEqual(criteria[0]["provenance"]["category"], "6001")
        self.assertEqual(ProductInspectionCriterion.objects.count(), count)
        raw.refresh_from_db()
        self.assertEqual(raw.raw_data, original_raw)
        self.assertEqual(raw.category, "6001")
        self.assertEqual(self.client.post(f"/api/flow-cards/standards/{standard_id}/activate/", {}, format="json").status_code, 200)
        ctx = prep.context({"order_id":order.pk,"order_plan":"1"}, self.user)
        self.assertEqual(ctx["auto_standard_id"], standard_id)
        group = prep.save_draft({"product_id":order.product_specification_id,"order_id":order.pk,"order_plan":True,"standard_id":standard_id,"planned_molds":80,"request_key":uuid.uuid4().hex},self.user)
        group = services.generate_group(group, self.user)
        with patch("reportlab.pdfgen.canvas.Canvas.drawString", autospec=True) as draw:
            render_cards([(group.cards.first(), 0)])
        labels = "\n".join(str(c.args[3]) for c in draw.call_args_list)
        self.assertIn("上模实测模温", labels)
        self.assertIn("下模实测模温", labels)
        self.assertIn("标准模温（上下模共用）：175～180", labels)
        self.assertIn("61～69 度", labels)
        self.assertNotIn("烤后硬度", labels)
        self.assertNotIn("接触温度", labels)
        self.assertEqual([g["category"] for g in group.snapshot["customer_inspections"][0]["groups"]], ["2001","6001"])
        stored = OrderInspectionSource.objects.get(order=order)
        stored.snapshot["reference"]["primary"]["temperature_upper"] = "183"
        stored.save(update_fields=["snapshot"])
        self.assertEqual(prep.context({"order_id":order.pk,"order_plan":"1"}, self.user)["auto_standard_id"], None)

    def test_two_hardness_stages_same_6001_and_2001_readonly_on_product(self):
        order, _, _ = self.order(bake=True, extra=[("6001", "C/S-A", 2.9, 3.1, "mm"),
            ("6001", "FLASHLEN", 0, .24, "mm"), ("2001", "C/S-R", 2.9, 3.1, "mm"),
            ("2001", "FLASHT", 0, .24, "mm"), ("2001", "MISMATCH", 0, .24, "mm")])
        other, _, _ = self.order("CI-J2", customer="L", project="XP-2")
        self.assertEqual(order.product_specification_id, other.product_specification_id)
        self.grant()
        data = self.read(order)
        self.assertEqual({r["key"] for r in data["selection"]["main"]}, {"HD", "C/S-A", "FLASHLEN"})
        self.assertEqual({r["key"] for r in data["selection"]["supplements"]}, {"C/S-R", "FLASH", "FLASHT", "MISMATCH"})
        result = self.client.post(self.endpoint(order), {"action":"confirm","digest":data["digest"]}, format="json")
        self.assertEqual(result.status_code, 200, result.data)
        review = CustomerInspectionReview.objects.get(order=order)
        self.assertEqual(review.snapshot["selection"], data["selection"])
        response = self.client.get(f"/api/orders/product-specifications/{order.product_specification_id}/customer-inspection/")
        scopes = {r["customer_code"]:r for r in response.data["results"]}
        self.assertEqual(scopes["J"]["selection"], data["selection"])
        self.assertEqual({r["key"] for r in scopes["L"]["selection"]["supplements"]}, {"FLASH"})
        bake = {"state":"APPLICABLE", "temperature_value":"150", "minutes_value":"120"}
        primary_raw = ProductInspectionCriterion.objects.get(order=order, category="6001", inspection_item="HD")
        manual_raw = ProductInspectionCriterion.objects.get(order=order, category="2001", inspection_item="FLASH")
        path = "/api/flow-cards/standards/"
        for invalid in [
            {"name":"HD", "stage":"MOLDING_HARDNESS", "customer_item":"HD", "upper":"65"},
            {"name":"FLASH", "stage":"DIMENSION", "criterion_id":manual_raw.pk},
        ]:
            rejected = self.client.post(path, self.adoption_body(order, data, bake=bake, criteria=[invalid]), format="json")
            self.assertEqual(rejected.status_code, 400, rejected.data)
        resp = self.client.post(path, self.adoption_body(order, data, bake=bake), format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        snap = resp.data["snapshot"]
        self.assertEqual(snap["customer_reference"]["selection"], data["selection"])
        self.assertTrue(snap["route"]["baked_hardness"])
        self.assertEqual([(c["stage"], c["upper"], c["id"]) for c in snap["criteria"] if c["name"] == "HD"],
                         [("MOLDING_HARDNESS","69",primary_raw.pk), ("BAKED_HARDNESS","69",primary_raw.pk)])
        axis = next(c for c in snap["criteria"] if c["name"] == "C/S-A")
        self.assertFalse(axis["follow_package"])
        self.assertTrue(all(c["provenance"]["category"] == "6001" for c in snap["criteria"]))
        self.assertEqual(self.client.post(f"{path}{resp.data['id']}/activate/", {}, format="json").status_code, 200)
        group = prep.save_draft({"product_id":order.product_specification_id,"order_id":order.pk,"order_plan":True,"standard_id":resp.data["id"],"planned_molds":80,"request_key":uuid.uuid4().hex}, self.user)
        group = services.generate_group(group, self.user)
        with patch("reportlab.pdfgen.canvas.Canvas.drawString", autospec=True) as draw:
            render_cards([(group.cards.first(), 0)])
        labels = [str(c.args[3]) for c in draw.call_args_list]
        self.assertIn("成型后硬度", labels)
        self.assertTrue(any("烤后硬度" in v for v in labels), labels)
        self.assertGreaterEqual(sum("61～69 度" in v for v in labels), 2)
        self.assertFalse(any("C/S-A" in v or "FLASHT" in v or "MISMATCH" in v for v in labels))

    def test_6001_conflicts_do_not_fall_back_and_2001_conflicts_never_become_limits(self):
        def row(category, item, hi, customer="J", project="XP"):
            return {"category": category, "inspection_item": item, "customer": customer, "project_no": project,
                    "version": "D01", "lower_limit": "50", "upper_limit": hi, "unit": "度"}
        conflict = ci.build_snapshot([row("6001","HD","65"), row("6001","硬度","68"),
                                      row("2001","HD","62"), row("2001","SG","60")], {})
        chosen = ci.selection(conflict)
        self.assertTrue(chosen["problems"])
        self.assertEqual(chosen["main"], [])
        self.assertEqual([r["key"] for r in chosen["supplements"]], ["SG"])
        invalid_ref = ci.build_snapshot([row("6001","HD","69"), row("2001","HD","bad"),
                                         row("2001","SG","bad")], {})
        self.assertTrue(invalid_ref["digest"])
        self.assertEqual([r["key"] for r in ci.selection(invalid_ref)["main"]], ["HD"])
        self.assertTrue(ci.selection(invalid_ref)["warnings"])
        other = ci.build_snapshot([row("6001","HD","69"), row("2001","SG","60", customer="L")], {})
        self.assertFalse(other["digest"])
        self.assertEqual(ci.selection(other)["supplements"], [])
        only_reference = ci.selection(ci.build_snapshot([row("2001","HD","62")], {}))
        self.assertEqual(only_reference["main"], [])
        self.assertIsNone(only_reference["supplements"][0]["stage"])

    def test_inherited_6001_adoption_is_scoped_and_reuses_the_same_reference_row(self):
        original, _, _ = self.order()
        self.grant()
        data = self.read(original)
        self.assertEqual(self.client.post(self.endpoint(original), {"action":"confirm","digest":data["digest"]}, format="json").status_code, 200)
        new, _, _ = self.order("CI-NO-SHEET", include=False, day=3)
        choice = self.read(new)["available"][0]
        resp = self.client.post(self.endpoint(new), {"action":"inherit","review_id":choice["id"]}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        chosen = self.read(new)
        self.assertEqual(chosen["status"], "INHERITED")
        self.assertEqual(chosen["selection"], data["selection"])
        used = set()
        for _ in range(2):
            resp = self.client.post("/api/flow-cards/standards/", self.adoption_body(new, chosen), format="json")
            self.assertEqual(resp.status_code, 201, resp.data)
            entry, = resp.data["snapshot"]["criteria"]
            used.add(entry["id"])
            self.assertEqual((entry["upper"], entry["order_id"]), ("69", new.pk))
        self.assertEqual(len(used), 1)
        self.assertEqual(ProductInspectionCriterion.objects.filter(order=new).count(), 1)
        self.assertEqual(ProductInspectionCriterion.objects.filter(order=original).count(), 3)

    def test_machine_suggestions_are_permissioned_independent_and_not_order_data(self):
        order, _, _ = self.order()
        path = f"/api/orders/product-specifications/{order.product_specification_id}/machine-temperatures/"
        first = ProductionStation.objects.create(code="MTEMP1", group="MTEMP", position_no=1)
        second = ProductionStation.objects.create(code="MTEMP2", group="MTEMP", position_no=2)
        self.assertEqual(self.client.put(path,{"station_id":first.pk,"temperature_c":"182"},format="json").status_code,403)
        self.grant()
        for station,value in ((first,"182.50"),(second,"179.2"),(first,"183")):
            resp = self.client.put(path,{"station_id":station.pk,"temperature_c":value},format="json")
            self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual({s["code"]:s["temperature_c"] for s in resp.data["stations"] if s["temperature_c"] is not None}, {"MTEMP1":"183","MTEMP2":"179.2"})
        self.assertEqual(self.client.put(path,{"station_id":first.pk,"temperature_c":"Infinity"},format="json").status_code,400)
        resp = self.client.put(path,{"station_id":first.pk,"temperature_c":None},format="json")
        self.assertEqual({s["code"]:s["temperature_c"] for s in resp.data["stations"] if s["temperature_c"] is not None}, {"MTEMP2":"179.2"})
        self.assertEqual(self.read(order)["reference"]["primary"]["temperature_upper"], "180")

    def test_content_approval_reuse_changed_limits_and_missing_sheet(self):
        first, _, _ = self.order()
        twin, _, _ = self.order("CI-2", day=3)
        different, _, _ = self.order("CI-3", customer="L", project="XP-2", day=3)
        self.assertEqual(first.product_specification_id, different.product_specification_id)
        self.assertNotEqual(first.customer_code, different.customer_code)
        self.assertEqual(ProductSpecification.objects.count(), 1)
        digest = self.read(first)["digest"]
        self.assertEqual(self.client.post(self.endpoint(first), {"action": "confirm", "digest": digest}).status_code, 403)
        self.grant()
        self.assertEqual(self.client.post(self.endpoint(first), {"action": "confirm", "digest": digest}, format="json").status_code, 200)
        self.assertEqual(self.read(twin)["status"], "CONFIRMED")
        self.assertEqual(self.read(different)["status"], "PENDING")
        changed, _, _ = self.order("CI-4", high=66, day=4)
        current = self.read(changed)
        self.assertEqual(current["status"], "PENDING")
        self.assertEqual(current["changes"][0]["incoming"], ["F02", "61", "66", "度"])
        self.assertNotEqual(current["digest"], digest)
        missing, _, _ = self.order("CI-5", include=False, day=5)
        data = self.read(missing)
        self.assertEqual(data["status"], "MISSING")
        self.assertEqual(data["customer_code"], "")
        self.assertEqual(len(data["available"]), 1)
        self.assertEqual(self.client.post(self.endpoint(missing), {"action": "inherit", "review_id": data["available"][0]["id"]}, format="json").status_code, 200)
        self.assertEqual(self.read(missing)["status"], "INHERITED")
        self.assertEqual(CustomerInspectionReview.objects.count(), 1)

    def test_existing_supplement_is_count_locked_and_idempotent(self):
        order, batch, _ = self.order()
        OrderInspectionSource.objects.all().delete()
        QualityOrder.objects.filter(pk=order.pk).update(customer_code="")
        order.refresh_from_db()
        baseline = (order.order_quantity, order.status, order.updated_at, ProductInspectionCriterion.objects.count())
        arguments = ["--sha", batch.sha256, "--expect-orders", "1", "--expect-criteria", "3"]
        call_command("supplement_xiangbang_inspection", *arguments)
        self.assertFalse(OrderInspectionSource.objects.exists())
        call_command("supplement_xiangbang_inspection", *arguments, "--apply")
        call_command("supplement_xiangbang_inspection", *arguments, "--apply")
        order.refresh_from_db()
        self.assertEqual(order.customer_code, "J")
        self.assertEqual((order.order_quantity, order.status, order.updated_at, ProductInspectionCriterion.objects.count()), baseline)
        self.assertEqual(self.read(order)["status"], "PENDING")

    def test_new_card_freezes_reference_keeps_trading_identity_and_original_layout(self):
        order, _, _ = self.order()
        self.grant()
        standard = services.confirm_standard({
            "request_key": uuid.uuid4().hex, "product_id": order.product_specification_id, "source": "隔离生产工艺",
            "source_kind": "FACTORY_PROCESS", "customer_key": "翔邦", "whole_mold": "NOT_REQUIRED",
            "appearance_ids": list(services.AppearanceIssue.objects.values_list("pk", flat=True)),
            "dimension_count": 0, "criteria": [],
            "route": {"confirmed": True, "molding": True, "patrol": True, "inspection": True},
        }, self.user)
        prep.activate_standard(standard, self.user)
        group = prep.save_draft({"product_id": order.product_specification_id, "order_id":order.pk, "order_plan":True, "planned_molds":200, "request_key":uuid.uuid4().hex}, self.user)
        group = services.generate_group(group, self.user)
        self.assertEqual(group.snapshot["orders"][0]["customer"], "翔邦")
        self.assertEqual(group.snapshot["orders"][0]["customer_code"], "J")
        self.assertEqual(group.snapshot["standard"]["snapshot"]["criteria"], [])
        self.assertEqual(len(group.snapshot["customer_inspections"][0]["groups"]), 2)
        self.assertEqual(group.snapshot["customer_inspections"][0]["groups"][0]["rows"][0]["item"], "HD")
        card = group.cards.order_by("sequence").first()
        with patch("reportlab.pdfgen.canvas.Canvas.drawString", autospec=True) as draw:
            render_cards([(card, 0)])
        self.assertTrue(any("客户 J" in str(c.args[3]) for c in draw.call_args_list))
        with patch("orders.customer_inspection_pdf.Paragraph", wraps=Paragraph) as paragraph:
            current = self.client.get(f"/api/flow-cards/cards/{card.pk}/customer-inspection/")
        self.assertEqual(current.status_code, 200)
        self.assertIn("HD（硬度）", [call.args[0] for call in paragraph.call_args_list])
        self.assertFalse(PrintJob.objects.exists())
        other, _, _ = self.order("CI-2", customer="L", project="XP-2", day=3)
        with self.assertRaisesMessage(services.ValidationError, "分别备卡"):
            prep.check_customer_inspections([order, other], ci.get_order_snapshots([order, other]))
