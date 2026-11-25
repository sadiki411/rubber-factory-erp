"""Synthetic-only review of new guidance, samples and print-time inspection layout."""
import hashlib
import re
import tempfile
import uuid
from unittest.mock import patch
from django.test import TestCase, override_settings
from django.contrib.auth.models import Permission
from quality.tests.helpers import QualityTestMixin
from orders.models import ProductSpecification
from . import test_preparation as fixtures
from . import preparation as p, services as s
from .guidance import bake_text, oil_instruction, summary
from .models import CardGroup, CardEvent, InternalCard, PrintItem, PrintJob, TraceAudit
from .paper import A4_TWO_UP, SINGLE, TwoUpCanvas
from .pdf import render_cards


@override_settings(FLOW_CARDS_PREPARATION_ENABLED=True, FLOW_CARDS_WRITE_ENABLED=True)
class GuidanceTests(QualityTestMixin, TestCase):
    standard=fixtures.PreparationTests.standard
    create_run=fixtures.PreparationTests.create_run
    body=fixtures.PreparationTests.body

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.product=ProductSpecification.objects.create(specification='SYNTH-GUIDANCE',material='TEST-R')
        cls.order.product_specification=cls.product
        cls.order.source_system='翔邦'
        cls.order.save()

    def setUp(self):
        super().setUp()
        self.user.user_permissions.add(Permission.objects.get(codename='maintain_product_standard'))
    def generated(self, **changes):
        self.standard(**changes.pop('standard', {}))
        return s.generate_group(p.save_draft(self.body(order_plan=True, planned_molds=455, **changes), self.user), self.user)

    def rendered_strings(self, card, **options):
        from reportlab.pdfgen.canvas import Canvas
        strings=[]
        original=Canvas.drawString
        def record(canvas, x, y, text, *args, **kwargs):
            strings.append(str(text))
            return original(canvas, x, y, text, *args, **kwargs)
        with patch.object(Canvas, 'drawString', record):
            raw=render_cards([(card,0)], **options)
        return raw, '\n'.join(strings)

    def test_guidance_original_units_and_unconfirmed_oil_remain_references(self):
        self.product.primary_curing='时间60-65min 温度178-183℃'
        self.product.secondary_curing='150℃ × 120min'
        self.product.save()
        std=self.standard(process_parameters={'primary_curing_time':{'state':'RAW_UNCONFIRMED','value':'60','unit':'s'},
            'oil':{'state':'RAW_UNCONFIRMED','raw_text':'禁止'},'pressure':{'state':'RAW_UNCONFIRMED','value':'180'}})
        rows={r['key']:r for r in summary(self.product,std)}
        self.assertIn('单位冲突',rows['primary_curing_time']['warning'])
        self.assertEqual(rows['primary_curing_time']['value'],'60 s')
        self.assertIn('候选原文：禁止',rows['oil']['instruction'])
        self.assertNotEqual(rows['oil']['instruction'],'要求：禁止洒油')
        self.assertIn('不作为',rows['pressure']['warning'])
        original=s.generate_group(p.save_draft(self.body(order_plan=True,planned_molds=100),self.user),self.user)
        ctx=p.context({'order_id':self.order.pk,'order_plan':'1'},self.user)
        reviews=ctx['original_reference_reviews'][str(original.pk)]
        self.assertEqual(ctx['preferred_group']['id'],str(original.pk))
        self.assertTrue(any('单位冲突' in r['warning'] and r['value']=='60 s' and '60-65min' in r['raw_reference'] for r in reviews))

    def test_sample_first_tail_readonly_no_qr_or_records_and_7268_calculation(self):
        self.standard()
        self.user.user_permissions.clear()
        self.client.force_authenticate(type(self.user).objects.get(pk=self.user.pk))
        before=(CardGroup.objects.count(),InternalCard.objects.count(),PrintJob.objects.count(),TraceAudit.objects.count())
        body=self.body(order_plan=True,arranged_quantity=7268,confirmed_effective_output=16,output_unit='件/模',
            effective_output_confirmed=True,output_basis='合成现场核对')
        result=self.client.post('/api/flow-cards/groups/preview/',body,format='json')
        self.assertEqual(result.status_code,200,result.content)
        self.assertEqual(result.json()['card_molds'],[100,100,100,100,55])
        for which in ('FIRST','LAST'):
            with patch('flowcards.pdf_v7.QrCodeWidget',side_effect=AssertionError('Sample must have no code')):
                response=self.client.post('/api/flow-cards/groups/sample/',{**body,'sample_position':which,'second_inspection':True},format='json')
            self.assertEqual(response.status_code,200,response.content)
            self.assertEqual(response['Content-Type'],'application/pdf')
            self.assertTrue(response.content.startswith(b'%PDF'))
        self.assertEqual(before,(CardGroup.objects.count(),InternalCard.objects.count(),PrintJob.objects.count(),TraceAudit.objects.count()))

    def test_sample_requires_explicit_output_never_uses_product_total_holes(self):
        self.product.total_cavities='16';self.product.save()
        for body in (self.body(order_plan=True,arranged_quantity=7268),self.body(order_plan=True,arranged_quantity=7268,confirmed_effective_output=16)):
            self.assertEqual(self.client.post('/api/flow-cards/groups/sample/',body,format='json').status_code,400)
        self.assertFalse(CardGroup.objects.exists())

    def test_partial_trial_and_explicit_adoption_are_distinct_and_never_do_production(self):
        self.standard()
        body=self.body(order_plan=True,arranged_quantity=1601,confirmed_effective_output=16,output_unit='件/模',
            effective_output_confirmed=True,output_basis='合成现场核对')
        draft=p.save_draft(body,self.user)
        self.assertEqual(draft.snapshot['card_molds'],[100,1])
        with self.assertRaisesMessage(s.ValidationError,'直接填写'):
            s.generate_group(draft,self.user)
        retry={**body,'planned_molds':101,'group_id':str(draft.pk),'expected_revision':draft.revision,'request_key':uuid.uuid4().hex}
        invalid=self.client.post('/api/flow-cards/groups/sample/',{**retry,'card_molds':[100,0]},format='json')
        self.assertEqual(invalid.status_code,400)
        valid=p.save_draft(retry,self.user)
        self.assertEqual(s.generate_group(valid,self.user).cards.count(),2)

    def test_new_paper_oil_and_temperature_first_reference_tail_and_fixed_bake(self):
        group=self.generated(standard={'process_parameters':{'oil':{'state':'CONFIRMED','raw_text':'禁止'},
            'upper_contact_temperature':{'state':'CONFIRMED','lower':'178','upper':'183','unit':'℃'},
            'lower_contact_temperature':{'state':'CONFIRMED','lower':'178','upper':'183','unit':'℃'}},
            'bake':{'state':'APPLICABLE','temperature_value':'150','minutes_value':'120'},
            'route':{'confirmed':True,'molding':True,'patrol':True,'deburr':True,'inspection':True,'bake':True}},
            temperature_plan={'mode':'FIRST','basis':'合成负责人按首件、换模调机/异常复测'})
        first=group.cards.order_by('sequence').first();tail=group.cards.order_by('sequence').last()
        raw,text=self.rendered_strings(first,print_options={'second_inspection':True})
        self.assertIn('要求：禁止洒油',text)
        self.assertIn('接触实测·上模',text)
        self.assertIn('接触实测·下模',text)
        self.assertIn('150 °C × 120 min',text)
        self.assertNotIn('150~150',text)
        self.assertIn('一检：',text);self.assertIn('二检：',text)
        _,tail_text=self.rendered_strings(tail)
        self.assertNotIn('接触实测·上模',tail_text)
        self.assertIn('引用首卡记录',tail_text)
        self.assertIn(first.card_no,tail_text)
        self.assertNotIn('二检：',tail_text)
        self.assertTrue(raw.startswith(b'%PDF'))

    def test_oil_states_and_bake_ranges(self):
        self.assertEqual(oil_instruction({'state':'CONFIRMED','raw_text':'禁止'}),'要求：禁止洒油')
        self.assertIn('待核',oil_instruction({'state':'RAW_UNCONFIRMED','raw_text':'禁止'}))
        self.assertIn('未提供',oil_instruction({}))
        self.assertIn('确认无要求',oil_instruction({'state':'NOT_APPLICABLE'}))
        self.assertEqual(bake_text({'state':'APPLICABLE','temperature_lower':'150','temperature_upper':'150.0','minutes_lower':'120','minutes_upper':'120'}),'150 ℃ × 120 min')
        self.assertEqual(bake_text({'state':'APPLICABLE','temperature_lower':'145','temperature_upper':'155','minutes_lower':'100','minutes_upper':'120'}),'145～155 ℃ × 100～120 min')

    def test_print_options_immutable_resume_and_false_default_old_layout_unchanged(self):
        group=self.generated()
        card=group.cards.first();card.group.snapshot['template']['renderer']='flowcard-preparation-20261119-v5'
        from .pdf_v3 import render_cards_v3
        old=render_cards_v3([(card,0)],applicable_dimensions=True,route_aware=True)
        self.assertEqual(render_cards([(card,0)]),old)
        self.assertEqual(render_cards([(card,0)],print_options={'second_inspection':False}),old)
        ids=list(map(str,group.cards.values_list('pk',flat=True)))
        job=s.create_print({'request_key':uuid.uuid4().hex,'card_ids':ids,'second_inspection':True},self.user)
        self.assertTrue(job.template['second_inspection'])
        with self.assertRaisesMessage(s.ValidationError,'冻结'):
            s.create_print({'request_key':uuid.uuid4().hex,'card_ids':ids,'resume_active':True,'second_inspection':False},self.user)
        self.assertEqual(s.create_print({'request_key':uuid.uuid4().hex,'card_ids':ids,'resume_active':True,'second_inspection':True},self.user).pk,job.pk)

    def test_new_dense_single_card_keeps_qa_on_front_and_v6_reprints_original(self):
        from copy import deepcopy
        from types import SimpleNamespace
        from reportlab.graphics.barcode.qr import QrCodeWidget
        from .pdf_v3 import render_cards_v3
        group = self.generated()
        card = group.cards.first()
        original = deepcopy(card.group.snapshot)
        old_snapshot = deepcopy(original)
        old_snapshot["template"]["renderer"] = s.LEGACY_PREPARATION_RENDERER
        old = SimpleNamespace(pk=card.pk, card_no=card.card_no, original_date=card.original_date,
                              sequence=card.sequence, denominator=card.denominator, planned_molds=card.planned_molds,
                              group=SimpleNamespace(snapshot=old_snapshot))
        self.assertEqual(render_cards([(old,0)]), render_cards_v3(
            [(old,0)], applicable_dimensions=True, route_aware=True, operational_guidance=True))

        snap = card.group.snapshot
        snap["whole_mold"] = "REQUIRED"
        snap["route"].update(molding=True,patrol=True,deburr=True,bake=True,baked_hardness=True,inspection=True)
        snap["process_parameters"].update(
            mold_temperature_range={"state":"CONFIRMED","lower":"178","upper":"183","unit":"℃"},
            primary_curing_time={"state":"CONFIRMED","lower":"60","upper":"65","unit":"s"},
        )
        standard = snap["standard"]["snapshot"]
        standard.update(dimension_count=1, bake={"state":"APPLICABLE","temperature_value":"150","minutes_value":"120"})
        standard["criteria"] = [
            {"name":name,"stage":stage,"lower":low,"upper":high,"unit":unit,"limit_mode":"RANGE"}
            for name,stage,low,high,unit in (("HD","MOLDING_HARDNESS","66","74","度"),
                ("C/S-A","DIMENSION","2.56","2.68","mm"),("HD","BAKED_HARDNESS","66","74","度"))
        ]
        with patch("flowcards.pdf_v7.QrCodeWidget", wraps=QrCodeWidget) as qr:
            pdf, contents = self.rendered_strings(card, print_options={"second_inspection":True, "paper_layout":SINGLE})
        qr.assert_called_once_with(f"ERPFC:{card.pk}")
        self.assertEqual(len(re.findall(rb"/Type /Page\b", pdf)), 1)
        self.assertEqual(contents.splitlines().count("178～183 °C"),2)
        for text in ("整模检验","二烤建议","烤后硬度","一检：","二检：","版式 08"):
            self.assertIn(text,contents)
        self.assertNotIn("表单版次：草稿", contents)
        snap["product"].update(specification="合成超长规格"*27, material="合成长材质"*17,
                               customer_product_no="DEMO-"*25)
        continued, contents = self.rendered_strings(card)
        self.assertGreater(len(re.findall(rb"/Type /Page\b", continued)), 1)
        self.assertIn("完整规格与材质见续页", contents)
        self.assertIn(f"身份详情 · {card.card_no}", contents)
        self.assertIn("品检员", contents)

    def test_material_snapshot_uses_actual_length_and_whole_strip_layout(self):
        self.product.material_length = "999mm"
        self.product.actual_material_length = "320"
        self.product.large_strip_weight_g = 21
        self.product.large_strip_count = 5
        self.product.small_strip_weight_g = 11
        self.product.small_strip_count = 2
        self.product.save()
        card = self.generated().cards.first()
        self.product.actual_material_length = "777mm"
        self.product.save()
        _, text = self.rendered_strings(card)
        self.assertIn("料长 320 mm", text)
        self.assertIn("料重 21/11 g", text)
        self.assertNotIn("×5条", text)
        self.assertNotIn("777mm", text)
        self.assertNotIn("999mm", text)
        card.group.snapshot["template"]["renderer"] = "flowcard-guidance-20261122-v7"
        _, old = self.rendered_strings(card)
        self.assertNotIn("料长", old)
        self.assertIn("版式 07", old)

        from .paper_material import paper_material, material_text
        from types import SimpleNamespace
        run = SimpleNamespace(large_strip_weight_g=18, large_strip_count=6,
                              small_strip_weight_g=None, small_strip_count=None)
        self.product.actual_material_length = ""
        single = material_text(paper_material(self.product, run))
        self.assertEqual(single, "料长 ____    条重 18g×6条")

    def test_actual_cut_weight_and_card_override_survive_draft_sample_and_generation(self):
        self.product.actual_material_length = "320"
        self.product.actual_cut_weight = "13g／11g"
        self.product.cut_weight = "999"
        self.product.large_strip_weight_g = 98
        self.product.large_strip_count = 5
        self.product.save()
        self.standard()
        defaults = p.context({"order_id": self.order.pk, "order_plan": "1"}, self.user)["paper_material_defaults"]
        self.assertEqual((defaults["weight_g"], defaults["weight_source"]), ("13/11", "PRODUCT_ACTUAL"))
        body = self.body(order_plan=True, planned_molds=100, paper_material={"actual_material_length":"321", "weight_g":"14 / 12.50"})
        response = self.client.post('/api/flow-cards/groups/sample/', {**body, "sample_position":"FIRST"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(InternalCard.objects.exists())
        draft = p.save_draft(body, self.user)
        self.product.actual_cut_weight = "15"
        self.product.save(update_fields=["actual_cut_weight"])
        reopened = p.context({"order_id": self.order.pk, "order_plan": "1"}, self.user)["preferred_group"]
        self.assertEqual(reopened["snapshot"]["paper_material"]["weight_g"], "14/12.5")
        card = s.generate_group(draft, self.user).cards.first()
        _, content = self.rendered_strings(card)
        self.assertIn("料长 321 mm", content)
        self.assertIn("料重 14/12.5 g", content)
        self.product.refresh_from_db()
        self.assertEqual(self.product.actual_cut_weight, "15")
        self.assertEqual(self.product.actual_material_length, "320")

    def test_weight_validation_blank_zero_and_missing_actual_reference(self):
        from .paper_material import paper_material, material_text
        self.product.cut_weight = "999"
        self.product.actual_cut_weight = "原记录：待实测"
        self.assertEqual(paper_material(self.product, compact=True)["weight_g"], "")
        self.assertEqual(paper_material(self.product, compact=True)["weight_reference"], "原记录：待实测")
        for value in ("13/","/11","13/11/10","13.001","0.013kg"):
            with self.subTest(value=value), self.assertRaises(s.ValidationError):
                paper_material(self.product, overrides={"weight_g":value}, compact=True)
        for value, text in (("0","料重 0 g"),("","料重 ____ g")):
            self.assertIn(text,material_text(paper_material(self.product, overrides={"weight_g":value}, compact=True)))

    def test_open_original_pdf_is_never_confirmation_then_success_controls_reprint(self):
        card = self.generated().cards.first()
        endpoint = "/api/flow-cards/print-jobs/"
        identity = str(card.pk)
        with tempfile.TemporaryDirectory() as storage, override_settings(FLOW_CARDS_PRIVATE_ROOT=storage):
            def reserve():
                result = self.client.post(endpoint, {"card_ids":[identity],"request_key":uuid.uuid4().hex}, format="json")
                self.assertEqual(result.status_code,201,result.data)
                return result.json()
            job = reserve()
            copies=[]
            for query in ("","?inline=1"):
                response=self.client.get(job["pdf_url"]+query)
                self.assertEqual(response.status_code,200)
                self.assertTrue(response["Content-Disposition"].startswith("inline" if query else "attachment"))
                copies.append(b"".join(response.streaming_content))
                response.close()
            self.assertEqual(copies[0],copies[1])
            self.assertEqual(hashlib.sha256(copies[0]).hexdigest(), job["pdf_sha256"])
            card.refresh_from_db()
            self.assertIsNone(card.printed_at)
            self.assertEqual(card.reprint_count,0)
            self.assertEqual(self.client.get(f"{endpoint}{job['id']}/").json()["items"][0]["proposed_reprint"],0)
            self.assertEqual(PrintJob.objects.count(),1)
            self.assertEqual(self.client.post(f"{endpoint}{job['id']}/confirm/",{"outcomes":{identity:"SUCCESS"}},format="json").status_code,200)
            self.assertEqual(reserve()["items"][0]["proposed_reprint"],1)

    def test_reopen_pending_job_cancel_only_one_card_then_reprint_it(self):
        group = self.generated()
        first, second = list(group.cards.all())[:2]
        endpoint = "/api/flow-cards/print-jobs/"
        job = s.create_print({"card_ids": [str(first.pk), str(second.pk)], "request_key": uuid.uuid4().hex}, self.user)
        rows = self.client.get(endpoint, {"group_id":group.pk,"pending":"1"}).json()
        self.assertEqual([row["id"] for row in rows["results"]], [str(job.pk)])
        self.assertEqual(self.client.get(f"/api/flow-cards/cards/{first.pk}/").json()["pending_print_job_id"], str(job.pk))
        def outcome(card, value):
            response = self.client.post(f"{endpoint}{job.pk}/confirm/", {"outcomes":{str(card.pk):value}}, format="json")
            self.assertEqual(response.status_code, 200, response.data)
        outcome(first,"SUCCESS")
        self.assertEqual(self.client.get(endpoint, {"card_id":first.pk,"pending":"1"}).json()["count"],0)
        self.assertEqual(self.client.get(endpoint, {"card_id":second.pk,"pending":"1"}).json()["count"],1)
        outcome(second,"CANCELLED")
        first.refresh_from_db(); second.refresh_from_db()
        self.assertIsNotNone(first.printed_at)
        self.assertIsNone(second.printed_at)
        self.assertEqual(second.status,"ACTIVE")
        self.assertEqual(self.client.get(endpoint, {"group_id":group.pk,"pending":"1"}).json()["count"],0)
        next_job = s.create_print({"card_ids":[str(first.pk)],"request_key":uuid.uuid4().hex}, self.user)
        entry = next_job.items.get()
        self.assertEqual((entry.card_id,entry.proposed_reprint),(first.pk,1))

    def test_cancelled_job_without_pdf_returns_original_cancel_result_on_retry(self):
        card = self.generated().cards.first()
        body = {"card_ids":[str(card.pk)],"request_key":uuid.uuid4().hex}
        job = s.create_print(body,self.user)
        s.confirm_print(job,{"outcomes":{str(card.pk):"CANCELLED"}},self.user)
        endpoint = "/api/flow-cards/print-jobs/"
        response = self.client.post(endpoint,body,format="json")
        self.assertEqual(response.status_code,201,response.data)
        self.assertEqual((response.data["id"],response.data["status"],response.data["file_prepared"]),
                         (str(job.pk),"CLOSED",False))
        self.assertEqual(PrintJob.objects.count(),1)

    def test_void_unused_cards_preserves_history_and_allows_reissue_only_after_group_void(self):
        self.standard()
        body = self.body(order_plan=True,planned_molds=120)
        group = s.generate_group(p.save_draft(body,self.user),self.user)
        first, second = list(group.cards.all())
        job = s.create_print({"card_ids":[str(second.pk)],"request_key":uuid.uuid4().hex},self.user)
        def void(cards, *, reason="合成误发", unused=True):
            return self.client.post("/api/flow-cards/events/", {
                "card_ids":[str(c.pk) for c in cards],"kind":"VOID","scope":"误发未使用","evidence":"合成核对",
                "request_key":uuid.uuid4().hex,"values":{"reason":reason,"unused_confirmed":unused}
            },format="json")
        self.assertEqual(void([first],unused=False).status_code,400)
        self.assertEqual(void([first],reason="").status_code,400)
        self.assertEqual(void([first,second]).status_code,400)
        self.assertEqual(void([first]).status_code,201)
        group.refresh_from_db()
        self.assertEqual(group.status,"GENERATED")
        with self.assertRaises(p.PreparationConflict):
            p.save_draft({**body,"request_key":uuid.uuid4().hex},self.user)
        s.confirm_print(job,{"outcomes":{str(second.pk):"SUCCESS"}},self.user)
        result=void([second])
        self.assertEqual(result.status_code,201,result.data)
        group.refresh_from_db()
        self.assertEqual(group.status,"VOID")
        with self.assertRaisesMessage(s.ValidationError,"已作废"):
            s.generate_group(group,self.user)
        self.assertEqual(set(group.cards.values_list("status",flat=True)),{"VOID"})
        second.refresh_from_db()
        self.assertIsNotNone(second.printed_at)
        new=s.generate_group(p.save_draft({**body,"request_key":uuid.uuid4().hex},self.user),self.user)
        self.assertFalse(set(new.cards.values_list("card_no",flat=True)) & set(group.cards.values_list("card_no",flat=True)))
        self.assertEqual(void([new.cards.first()]).status_code,201)
        used = new.cards.last()
        event=CardEvent.objects.create(kind="ANOMALY",request_key=uuid.uuid4().hex,request_digest="synthetic",created_by=self.user)
        event.cards.add(used)
        self.assertEqual(void([used]).status_code,400)
        used.refresh_from_db()
        self.assertEqual(used.status,"ACTIVE")

    def test_a4_pairs_batches_top_down_and_odd_last_has_no_second_card(self):
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen.canvas import Canvas
        from reportlab.lib.units import mm
        self.standard()
        group = s.generate_group(p.save_draft(self.body(order_plan=True, planned_molds=755), self.user), self.user)
        cards = list(group.cards.order_by('sequence'))
        form_names, placements, cut_dashes, batches = [], [], [], {}
        original_form, original_text = Canvas.doForm, Canvas.drawString
        original_translate = Canvas.translate

        def capture_form(canvas, name):
            if not canvas.capturing:
                form_names.append(batches[name])
            return original_form(canvas, name)

        def capture_text(canvas, x, y, text, *args, **kwargs):
            batch = re.fullmatch(r"第 (\d+)/8 批", str(text))
            if canvas.capturing and batch:
                batches[canvas.form_name] = int(batch[1])
            return original_text(canvas, x, y, text, *args, **kwargs)

        def capture_translate(canvas, x, y):
            if not canvas.capturing:
                placements.append((x, y))
            return original_translate(canvas, x, y)

        def capture_dash(canvas, *args, **kwargs):
            if not canvas.capturing:
                cut_dashes.append(args)
            return Canvas.setDash(canvas, *args, **kwargs)

        for count, expected in [(8, 4), (5, 3), (1, 1)]:
            form_names.clear(); placements.clear(); cut_dashes.clear(); batches.clear()
            selection = cards[:count] if count != 1 else [cards[3]]
            with patch.object(TwoUpCanvas, "doForm", capture_form), patch.object(TwoUpCanvas, "drawString", capture_text), patch.object(TwoUpCanvas, "translate", capture_translate), patch.object(TwoUpCanvas, "setDash", capture_dash):
                raw = render_cards([(card, 0) for card in reversed(selection)], print_options={"paper_layout": A4_TWO_UP})
            self.assertEqual(len(re.findall(rb"/Type /Page\b", raw)), expected)
            self.assertEqual(cut_dashes, [([3*mm, 2*mm],)] * expected)
            self.assertEqual(form_names, [c.sequence for c in selection])
            self.assertEqual(len(placements), count)
            self.assertEqual([y > A4[1]/2 for _, y in placements], [i % 2 == 0 for i in range(count)])
            self.assertTrue(all(abs(x - 5*mm) < .01 for x, _ in placements))
        single = render_cards([(card, 0) for card in cards[:5]], print_options={"paper_layout": SINGLE})
        self.assertEqual(len(re.findall(rb"/Type /Page\b", single)), 5)

    def test_print_preview_and_selected_reprint_do_not_touch_other_batches(self):
        group = self.generated()
        card = group.cards.get(sequence=4)
        ids = [str(card.pk)]
        endpoint = '/api/flow-cards/print-jobs/'
        before = list(group.cards.values('pk', 'printed_at', 'reprint_count'))
        counts = (PrintJob.objects.count(), PrintItem.objects.count(), TraceAudit.objects.count())
        with patch('flowcards.pdf_v7.QrCodeWidget', side_effect=AssertionError('Preview must not be scannable')):
            preview = self.client.post(endpoint+'preview/', {'card_ids': ids}, format='json')
        self.assertEqual(preview.status_code, 200, preview.content[:200])
        self.assertEqual(len(re.findall(rb"/Type /Page\b", preview.content)), 1)
        self.assertEqual(counts, (PrintJob.objects.count(), PrintItem.objects.count(), TraceAudit.objects.count()))
        self.assertEqual(before, list(group.cards.values('pk', 'printed_at', 'reprint_count')))
        self.assertEqual(self.client.post(endpoint+'preview/', {'card_ids': ids, 'paper_layout': 'INVALID'}, format='json').status_code, 400)
        with tempfile.TemporaryDirectory() as root, override_settings(FLOW_CARDS_PRIVATE_ROOT=root):
            old = s.create_print({'card_ids': ids, 'request_key': uuid.uuid4().hex, 'paper_layout': SINGLE}, self.user)
            old.template.pop('paper_layout')
            old.save(update_fields=['template'])
            old_file = render_cards([(card, 0)])
            resumed = self.client.post(endpoint, {'card_ids': ids, 'request_key': uuid.uuid4().hex, 'resume_active': True}, format='json')
            self.assertEqual(resumed.status_code, 201, resumed.content)
            self.assertEqual(resumed.json()['id'], str(old.pk))
            self.assertEqual(resumed.json()['paper_layout'], SINGLE)
            self.assertEqual(resumed.json()['pdf_sha256'], hashlib.sha256(old_file).hexdigest())
            confirm = lambda job: self.client.post(endpoint+job+'/confirm/', {'outcomes': {ids[0]: 'SUCCESS'}}, format='json')
            self.assertEqual(confirm(str(old.pk)).status_code, 200)
            raw = self.client.post(endpoint, {'card_ids': ids, 'request_key': uuid.uuid4().hex}, format='json')
            self.assertEqual(raw.status_code, 201, raw.content)
            new = raw.json()
            self.assertEqual(new['paper_layout'], A4_TWO_UP)
            self.assertEqual([(i['card_no'], i['sequence'], i['denominator'], i['proposed_reprint']) for i in new['items']], [(card.card_no, 4, 5, 1)])
            card.refresh_from_db()
            self.assertEqual(card.reprint_count, 0)
            self.assertEqual(confirm(new['id']).status_code, 200)
            card.refresh_from_db()
            self.assertEqual(card.reprint_count, 1)
            self.assertEqual(list(group.cards.exclude(pk=card.pk).values('pk', 'printed_at', 'reprint_count')), [row for row in before if row['pk'] != card.pk])
            self.assertEqual(b''.join(self.client.get(resumed.json()['pdf_url']).streaming_content), old_file)

    def test_oil_actual_violation_creates_linked_anomaly_once_without_changing_actual(self):
        group=self.generated(standard={'process_parameters':{'oil':{'state':'CONFIRMED','raw_text':'禁止'}}})
        card=group.cards.first()
        body={'request_key':uuid.uuid4().hex,'card_ids':[str(card.pk)],'kind':'DEBURR','scope':'合成整包',
              'evidence':'合成如实登记','values':{'oil':'SMALL'}}
        event=s.record_event(body,self.user);s.record_event(body,self.user)
        self.assertEqual(event.values['oil'],'SMALL')
        self.assertEqual(event.values['oil_judgment'],'EXCEPTION')
        self.assertEqual(CardEvent.objects.filter(kind='ANOMALY').count(),1)
        self.assertEqual(CardEvent.objects.get(kind='ANOMALY').values['deburr_event_id'],str(event.pk))

    def test_temperature_reference_requires_basis_and_real_reference(self):
        self.standard()
        for value in ({'mode':'FIRST'},{'mode':'REFERENCE','basis':'合成安排'},{'mode':'invalid'}):
            with self.assertRaises(s.ValidationError):
                p.build_plan(self.body(order_plan=True,planned_molds=100,temperature_plan=value))
        plan=p.build_plan(self.body(order_plan=True,planned_molds=100,temperature_plan={'mode':'REFERENCE','basis':'合成负责人','reference':'原卡TEST-1'}))
        self.assertEqual(plan['snapshot']['temperature_plan']['reference'],'原卡TEST-1')

    def test_guidance_supplement_is_a_dated_separate_record_not_a_card_mutation(self):
        group=self.generated()
        card=group.cards.first()
        from django.utils import timezone
        base={'request_key':uuid.uuid4().hex,'card_ids':[str(card.pk)],'kind':'WORK_GUIDANCE','scope':'本包',
              'evidence':'合成现场通知依据','business_at':timezone.now().isoformat(),'values':{'note':'合成补充作业指示'}}
        digest=group.snapshot['digest']
        event=s.record_event(base,self.user)
        self.assertEqual(event.created_by_id,self.user.pk)
        self.assertTrue(event.values['instruction_only'])
        group.refresh_from_db()
        self.assertEqual(group.snapshot['digest'],digest)
        self.user.user_permissions.clear()
        self.client.force_authenticate(type(self.user).objects.get(pk=self.user.pk))
        self.assertEqual(self.client.post('/api/flow-cards/events/',{**base,'request_key':uuid.uuid4().hex},format='json').status_code,403)
