"""Synthetic-only review of new guidance, samples and print-time inspection layout."""
import uuid
from unittest.mock import patch
from django.test import TestCase, override_settings
from django.contrib.auth.models import Permission
from quality.tests.helpers import QualityTestMixin
from orders.models import ProductSpecification
from . import test_preparation as fixtures
from . import preparation as p, services as s
from .guidance import bake_text, oil_instruction, summary
from .models import CardGroup, CardEvent, InternalCard, PrintJob, TraceAudit
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
            with patch('flowcards.pdf_v3.QrCodeWidget',side_effect=AssertionError('Sample must have no code')):
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
