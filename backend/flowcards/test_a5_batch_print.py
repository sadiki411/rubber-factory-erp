"""Synthetic coverage of A5, shared cards and frozen cross-order printing."""
import re
import tempfile
from uuid import uuid4
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone
from reportlab.lib.pagesizes import A5, landscape

from orders.models import ProductSpecification
from quality.models import QualityOrder
from quality.tests.helpers import QualityTestMixin
from .models import CardGroup, InternalCard, PrintJob
from .paper import A4_TWO_UP, A5_SINGLE
from .services import RENDERER, PREPARATION_RENDERER


def a5_pages(raw):
    boxes = [tuple(float(n) for n in box) for box in re.findall(rb"/MediaBox\s*\[\s*0\s+0\s+([\d.]+)\s+([\d.]+)\s*\]", raw)]
    assert boxes
    for w, h in boxes:
        assert abs(w - landscape(A5)[0]) < .01 and abs(h - landscape(A5)[1]) < .01
    return len(boxes)


@override_settings(FLOW_CARDS_WRITE_ENABLED=True)
class A5BatchPrintTests(QualityTestMixin, TestCase):
    base = "/api/flow-cards/print-jobs/"

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.product = ProductSpecification.objects.create(specification="SYNTH-DN125", material="SYNTH-E6514")
        common = dict(specification=cls.product.specification, material=cls.product.material,
                      product_specification=cls.product, source_system="翔邦", customer_code="SYNTH-CUSTOMER",
                      order_quantity=100, created_by=cls.user)
        cls.first = QualityOrder.objects.create(**common, order_no="SYNTH-PRINT-001", item_no="01")
        cls.second = QualityOrder.objects.create(**common, order_no="SYNTH-PRINT-002", item_no="09")
        cls.other = QualityOrder.objects.create(**{**common, "material": "SYNTH-OTHER"},
                                                order_no="SYNTH-PRINT-003", item_no="02")

    def group(self, orders, *, renderer=PREPARATION_RENDERER, width=190, height=130, marker="", **changes):
        snap = {"product": {"id": self.product.pk, "specification": self.product.specification, "material": self.product.material},
                "orders": [{"id":o.pk,"order_no":o.order_no,"item_no":o.item_no,"customer":"翔邦",
                            "customer_code":o.customer_code} for o in orders],
                "template": {"renderer":renderer,"version": 3 if renderer==RENDERER else 8,"width_mm":width,"height_mm":height},
                "appearance": [], "route": {"inspection": True}, "whole_mold": "NOT_REQUIRED",
                **changes}
        group = CardGroup.objects.create(product=self.product, status="GENERATED", snapshot=snap,
                                         plan_basis=marker or "合成测试", request_key=uuid4().hex, request_digest="synthetic",
                                         created_by=self.user)
        group.orders.set(orders)
        return group

    def card(self, group, sequence, total=2, **changes):
        return InternalCard.objects.create(group=group, card_no="FC-SYNTH-" + uuid4().hex[:12].upper(),
                                           sequence=sequence, denominator=total, planned_molds=100,
                                           original_date=timezone.localdate(), **changes)

    def scope(self, *orders):
        return {"specification": self.product.specification.lower(), "material": self.product.material,
                "order_ids": [order.pk for order in orders or (self.first,self.second)]}

    def plan(self, **overrides):
        return self.client.post(self.base + "order-plan/", {"order_scope": self.scope(), "paper_layout": A5_SINGLE, **overrides}, format="json")

    def test_exact_options_and_shared_cards_only_once(self):
        linked = self.group([self.first, self.second])
        card = self.card(linked, 1)
        self.card(linked, 2, status="VOID")
        rows = self.client.get(self.base + "order-options/", self.scope()).json()["results"]
        self.assertEqual({row["id"] for row in rows}, {self.first.pk, self.second.pk})
        self.assertTrue(all(row["has_cards"] for row in rows))
        plan = self.plan()
        self.assertEqual(plan.status_code, 200, plan.content)
        self.assertEqual([[c["id"] for c in packet] for packet in plan.json()["packets"]], [[str(card.pk)]])
        invalid = self.plan(order_scope=self.scope(self.first,self.other))
        self.assertEqual(invalid.status_code, 400, invalid.content)
        self.assertFalse(PrintJob.objects.exists())

    def test_a5_multiple_original_versions_and_no_counter_write_from_pdf(self):
        old = self.group([self.first], renderer=RENDERER, width=200, height=135)
        new = self.group([self.second])
        cards = [self.card(old, 1), self.card(new, 1), self.card(new, 2)]
        requested = [str(card.pk) for card in cards]
        before = list(InternalCard.objects.order_by("pk").values("pk", "reprint_count", "printed_at"))
        synthetic = {"order_scope": self.scope(), "paper_layout": A5_SINGLE, "card_ids": requested, "second_inspection": True}
        preview = self.client.post(self.base + "preview/", synthetic, format="json")
        self.assertEqual(preview.status_code, 200, preview.content[:240])
        self.assertEqual(a5_pages(preview.content), 3)
        self.assertFalse(PrintJob.objects.exists())
        self.assertEqual(self.plan().json()["card_count"], 3)
        self.assertEqual(len(self.plan(paper_layout=A4_TWO_UP).json()["packets"]), 2)
        with tempfile.TemporaryDirectory() as root, override_settings(FLOW_CARDS_PRIVATE_ROOT=root):
            response = self.client.post(self.base, {**synthetic, "request_key": uuid4().hex}, format="json")
            self.assertEqual(response.status_code, 201, response.content)
            job = response.json()
            self.assertEqual(job["paper_layout"], A5_SINGLE)
            self.assertEqual({c["card_id"] for c in job["items"]}, set(requested))
            self.assertEqual(a5_pages(b"".join(self.client.get(job["pdf_url"]).streaming_content)), 3)
            self.assertEqual(list(InternalCard.objects.order_by("pk").values("pk", "reprint_count", "printed_at")), before)
            self.assertEqual(self.plan().json()["counts"]["pending"], 3)
            done = self.client.post(self.base+job["id"]+"/confirm/", {"outcomes": {requested[0]:"SUCCESS"}}, format="json")
            self.assertEqual(done.status_code, 200, done.content)
            cards[0].refresh_from_db()
            self.assertIsNotNone(cards[0].printed_at)
            self.assertIsNone(InternalCard.objects.get(pk=cards[1].pk).printed_at)
            self.assertEqual(cards[0].reprint_count, 0)

    def test_customer_partition_reprint_consent_and_large_batch(self):
        one = self.group([self.first])
        self.second.customer_code = "SYNTH-OTHER-CUSTOMER"
        self.second.save(update_fields=["customer_code"])
        two = self.group([self.second])
        InternalCard.objects.bulk_create([InternalCard(group=one, card_no=f"FC-SYNTH-LARGE-{i}", sequence=i, denominator=101,
                                                      planned_molds=100, original_date=timezone.localdate()) for i in range(1,102)])
        printed = self.card(two, 1, printed_at=timezone.now())
        first = one.cards.first()
        plan = self.plan(include_reprints=True).json()
        self.assertEqual(sorted(len(packet) for packet in plan["packets"]), [1,1,100])
        self.assertEqual(plan["counts"]["reprint"], 1)
        self.assertEqual(self.plan().json()["card_count"], 101)
        opts = {"order_scope":self.scope(), "card_ids":[str(first.pk),str(printed.pk)], "include_reprints":True, "paper_layout":A5_SINGLE}
        refused = self.client.post(self.base+"preview/", opts, format="json")
        self.assertEqual(refused.status_code, 400, refused.content)
        cannot_reprint = self.client.post(self.base+"preview/", {**opts,"card_ids":[str(printed.pk)],"include_reprints":False}, format="json")
        self.assertEqual(cannot_reprint.status_code, 400, cannot_reprint.content)

    def test_variable_appearance_from_standard_to_preparation_and_paper(self):
        from django.contrib.auth.models import Permission
        from reportlab.pdfgen.canvas import Canvas
        from . import preparation as prep, services as s
        from .models import AppearanceIssue

        self.user.user_permissions.add(Permission.objects.get(codename="maintain_product_standard"))
        with override_settings(FLOW_CARDS_PREPARATION_ENABLED=True):
            names = ["黑点", "拔破", "划伤", "飞边", "色差", "压痕"]
            ids = []
            for name in names:
                response = self.client.post("/api/flow-cards/appearance/", {"name": name, "reuse_existing":True}, format="json")
                self.assertIn(response.status_code,(200,201),response.content)
                ids.append(response.json()["id"])
            again = self.client.post("/api/flow-cards/appearance/", {"name": names[0], "reuse_existing":True}, format="json")
            self.assertEqual(again.json()["id"],ids[0])
            metadata = {"product_id":self.product.pk, "source":"合成外观标准", "route":{"confirmed":True,"patrol":True,"inspection":True},
                        "whole_mold":"NOT_REQUIRED","criteria":[]}
            standard = s.confirm_standard({**metadata,"request_key":uuid4().hex,"appearance_ids":ids}, self.user)
            standard = prep.activate_standard(standard,self.user)
            original = self.group([self.first], appearance=standard.snapshot["appearance"])
            self.card(original,1,1)
            body = {"order_id":self.second.pk,"order_plan":True,"product_id":self.product.pk,"standard_id":standard.pk,"planned_molds":100}
            new_ids = [*ids[:1],*ids[2:]]
            preview = self.client.post("/api/flow-cards/groups/preview/", {**body,"appearance_ids":[]},format="json")
            self.assertEqual(preview.status_code,200,preview.content)
            self.assertEqual(preview.json()["snapshot"]["appearance"],[])
            with self.assertRaises(s.ValidationError):
                s.selected_appearance([ids[0],str(ids[0])])
            rendered = []
            draw = Canvas.drawString
            def capture(canvas, x, y, value, *args, **kwargs):
                rendered.append(str(value))
                return draw(canvas,x,y,value,*args,**kwargs)
            with patch.object(Canvas,"drawString",capture):
                pdf = self.client.post("/api/flow-cards/groups/sample/", {**body,"appearance_ids":new_ids,"paper_layout":A5_SINGLE}, format="json")
            self.assertEqual(pdf.status_code,200,pdf.content[:200])
            self.assertEqual(a5_pages(pdf.content),1)
            self.assertIn("黑点",rendered)
            self.assertIn("压痕",rendered)
            self.assertNotIn("拔破",rendered)
            self.assertTrue(AppearanceIssue.objects.filter(pk=ids[1],is_active=True).exists())
            self.assertEqual(original.snapshot["appearance"][1]["name"],"拔破")
