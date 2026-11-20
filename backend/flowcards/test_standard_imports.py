"""Synthetic workbook/API tests: no customer product limits and no production writes."""
import io
import uuid
from decimal import Decimal
from zipfile import ZipFile
import re
from django.test import SimpleTestCase, TestCase, override_settings
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from openpyxl import Workbook
from rest_framework.exceptions import ValidationError
from quality.tests.helpers import QualityTestMixin
from orders.models import ProductSpecification, ProductInspectionCriterion
from .standard_imports import limits, preview_workbook
from .limits import validate_limits, evaluate, limit_text
from .models import AppearanceIssue, StandardRevision
from . import services as s, preparation as p


def workbook_bytes():
    book = Workbook()
    a = book.active
    a.title = "工艺甲"
    for row in [
        ["客户", "SYNTH-A", "客户料号", "SYNTH-P-A", "模具", "SYNTH-M-A"],
        ["规格", "100×3", "材料", "SYNTH-R", "文件版号", "A01"],
        ["工序", "原项目", "原要求", "单位／量表", "对象／方法", "执行说明"],
        ["成型后", "HD（硬度）", "66～72", "Shore A", "成品；手持硬度计", "本项需测"],
        ["二次加硫后", "HD（硬度）", "68～74", "Shore A", "胶片；手持硬度计", "本项需测"],
        ["成型后", "线径", "≤3.05", "mm", "成品", "常规随包测"],
        ["成型后", "内径", "100.00 +0.10/-0.05", "mm", "成品", "常规不随包测"],
        ["一次硫化", "上模设定温度", "180±3", "°C", "机台设定", ""],
        ["二次加硫", "二烤条件", "145 °C × 90 min", "见原要求", "", ""],
    ]: a.append(row)
    b=book.create_sheet("条件乙")
    for row in [
        ["客户料号","规格","材料","模具","版次"],
        ["SYNTH-P-B","100×3","SYNTH-R","SYNTH-M-B","B01"],
        ["硬度","线径","内径","毛边洒油","二烤"],
        ["70","<3.05 mm","≥99.90 mm","禁止","时间min温度°C"],
        ["一次硫化时间","线径测量","内径测量","客户","备注"],
        ["50–60 min","本料号随包测","本料号随包测","SYNTH-B","仅有数值"],
    ]: b.append(row)
    c=book.create_sheet("订单补充")
    for row in [
        ["订单号","项次","客户料号","依据的产品版本","适用范围"],
        ["SYNTH-PO","2","SYNTH-P-A","A01","仅此项次"],
        ["工序","项目","订单补充要求","单位","备注"],
        ["成型后","内径","本单随包加测","mm","引用A01"],
        ["成型后","线径","≤3.00","mm","收紧上限"],
    ]: c.append(row)
    stream=io.BytesIO();book.save(stream);book.close()
    return stream.getvalue()


class ImportExtractionTests(SimpleTestCase):
    def test_three_layouts_preserve_identity_limits_units_and_scope(self):
        a,b,c=preview_workbook(workbook_bytes())["candidates"]
        self.assertEqual(a["customer_key"],"SYNTH-A")
        self.assertNotEqual(a["customer_product_no"],b["customer_product_no"])
        self.assertEqual(b["customer_key"],"SYNTH-B")
        self.assertEqual((len(a["criteria"]),len(b["criteria"]),len(c["criteria"])),(4,3,4))
        self.assertEqual(a["criteria"][0]["lower"],"66")
        self.assertEqual(a["criteria"][0]["unit"],"Shore A")
        self.assertEqual(a["criteria"][0]["method"],"成品；手持硬度计")
        self.assertEqual(a["hardness"]["MOLDING_HARDNESS"]["object"],"PRODUCT")
        self.assertEqual(a["criteria"][1]["stage"],"BAKED_HARDNESS")
        inner=next(x for x in a["criteria"] if x["name"]=="内径")
        self.assertEqual((inner["lower"],inner["upper"]),("99.95","100.10"))
        self.assertFalse(inner["follow_package"])
        self.assertEqual(a["bake"]["temperature_lower"],"145")
        self.assertEqual(b["bake"]["state"],"UNKNOWN")
        self.assertEqual(b["criteria"][0]["unit"],"")
        self.assertEqual(b["criteria"][0]["limit_mode"],"PENDING")
        wire=next(x for x in b["criteria"] if x["name"]=="线径")
        self.assertFalse(wire["upper_inclusive"])
        self.assertEqual(next(x for x in b["criteria"] if x["name"]=="内径")["limit_mode"],"LOWER")
        self.assertEqual(b["process_parameters"]["primary_curing_time"]["unit"],"min")
        self.assertEqual(c["order_no"],"SYNTH-PO")
        self.assertEqual(next(x for x in c["criteria"] if x["name"]=="线径")["upper"],"3.00")
        self.assertTrue(next(x for x in c["criteria"] if x["name"]=="内径")["follow_package"])
        self.assertEqual(next(x for x in a["criteria"] if x["name"]=="线径")["upper"],"3.05")

    def test_bad_file_and_no_identity_not_guessed(self):
        with self.assertRaises(ValidationError):preview_workbook(b"invalid")
        book=Workbook();book.active.append(["硬度","70"])
        stream=io.BytesIO();book.save(stream);book.close()
        with self.assertRaises(ValidationError):preview_workbook(stream.getvalue())

    def test_dimensionless_workbook_is_supported(self):
        result=io.BytesIO()
        with ZipFile(io.BytesIO(workbook_bytes())) as original, ZipFile(result,'w') as output:
            for name in original.namelist():
                content=original.read(name)
                if name.startswith('xl/worksheets/'):
                    content=re.sub(rb'<dimension[^>]*/>',b'',content)
                output.writestr(name,content)
        candidates=preview_workbook(result.getvalue())["candidates"]
        self.assertEqual(len(candidates),3)
        self.assertEqual(candidates[2]["base_version"],"A01")
        self.assertEqual(len(candidates[2]["criteria"]),4)

    def test_both_limits_and_lower_only_boundaries(self):
        row={**limits("≥2.95 mm"),"unit":"mm","method":"SYNTH"}
        self.assertEqual(validate_limits(row),"LOWER")
        self.assertEqual(limit_text(row),"≥ 2.95 mm")
        for actual,result in [("2.94","OUTSIDE"),("2.95","WITHIN"),("3.1","WITHIN")]:
            self.assertEqual(evaluate(row,Decimal(actual),True)["judgment"],result)
        row["lower_inclusive"]=False
        self.assertEqual(evaluate(row,Decimal("2.95"),True)["judgment"],"OUTSIDE")
        row={**limits("3.00±0.05"),"unit":"mm","method":"SYNTH"}
        self.assertEqual(validate_limits(row),"RANGE")
        for actual in ("2.94","3.06"):
            self.assertEqual(evaluate(row,Decimal(actual),True)["judgment"],"OUTSIDE")
        row["lower"]="4"
        with self.assertRaises(ValidationError):validate_limits(row)

    def test_text_operations_and_fixed_conditions_are_not_tolerance_ranges(self):
        book=Workbook();sheet=book.active
        for row in [["客户料号","SYNTH-TEXT"],["工序","项目","原要求","单位"],
            ["毛边","洒油","少量","—"], ["成型","脱模剂","不使用","—"],
            ["成型","排气","2次；上停0.3秒","—"], ["成型","上模设定温度","180","℃"],
            ["成型","一次硫化时间","120 s","s"], ["后工序","二烤","无需二烤","—"]]:sheet.append(row)
        stream=io.BytesIO();book.save(stream);book.close()
        candidate=preview_workbook(stream.getvalue())["candidates"][0]
        for key in ("oil","release_agent","venting"):
            self.assertNotIn("lower",candidate["process_parameters"][key])
            self.assertNotIn("upper",candidate["process_parameters"][key])
        self.assertEqual(candidate["process_parameters"]["oil"]["raw_text"],"少量")
        self.assertEqual(candidate["process_parameters"]["upper_set_temperature"]["value"],"180")
        self.assertEqual(candidate["process_parameters"]["primary_curing_time"]["value"],"120")
        self.assertEqual(candidate["bake"],{"state":"NOT_APPLICABLE"})
        self.assertFalse(candidate["route"]["bake"])


@override_settings(FLOW_CARDS_WRITE_ENABLED=True,FLOW_CARDS_PREPARATION_ENABLED=True)
class ProductStandardIntegrationTests(QualityTestMixin,TestCase):
    def setUp(self):
        super().setUp()
        self.user.user_permissions.add(Permission.objects.get(codename="maintain_product_standard"))
        self.product=ProductSpecification.objects.create(specification="SYNTH-100x3",material="SYNTH-R")

    def body(self):
        return {"request_key":uuid.uuid4().hex,"product_id":self.product.pk,"source":"SYNTH客户标准 / A01", "appearance_ids":list(AppearanceIssue.objects.values_list("pk",flat=True)),"route":{"confirmed":True,"molding":True},"criteria":[{"name":"线径","stage":"DIMENSION","lower":"2.95","upper":"3.05","limit_mode":"RANGE","unit":"mm","method":"合成测法","requirement_state":"CONFIRMED","follow_package":True,"provenance":{"file":"synthetic.xlsx","sheet":"工艺甲","row":6,"raw_text":"3.00±0.05"}}]}

    def test_preview_never_writes_product_or_standard(self):
        before=(ProductSpecification.objects.count(),ProductInspectionCriterion.objects.count(),StandardRevision.objects.count())
        response=self.client.post("/api/flow-cards/standards/import-preview/",{"file":SimpleUploadedFile("synthetic.xlsx",workbook_bytes())},format="multipart")
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(len(response.data["candidates"]),3)
        self.assertEqual(before,(ProductSpecification.objects.count(),ProductInspectionCriterion.objects.count(),StandardRevision.objects.count()))

    def test_manual_version_uses_existing_product_criteria_retains_origin_and_old_values(self):
        first=s.confirm_standard(self.body(),self.user)
        p.activate_standard(first,self.user)
        row=ProductInspectionCriterion.objects.get(pk=first.snapshot["criteria"][0]["id"])
        self.assertEqual(row.product_specification_id,self.product.pk)
        self.assertEqual(row.raw_data["standard_import"]["raw_text"],"3.00±0.05")
        body=self.body();body["criteria"][0].update(criterion_id=row.pk,override=True,lower="2.90",method="新版合成测法",name="新版线径名称")
        second=s.confirm_standard(body,self.user);p.activate_standard(second,self.user)
        row.refresh_from_db();first.refresh_from_db()
        self.assertEqual(row.lower_limit,"2.95")
        self.assertEqual(first.snapshot["criteria"][0]["lower"],"2.95")
        self.assertEqual(first.status,"SUPERSEDED")
        self.assertEqual(second.snapshot["criteria"][0]["lower"],"2.90")
        self.assertEqual(ProductInspectionCriterion.objects.get(pk=second.snapshot["criteria"][0]["id"]).raw_data["method"],"新版合成测法")
        self.assertEqual(second.snapshot['criteria'][0]['name'],'新版线径名称')
        self.assertEqual(ProductSpecification.objects.filter(pk=self.product.pk).count(),1)

    def test_order_exception_cannot_be_saved_against_another_product(self):
        body=self.body();body["applicable_order_id"]=self.order.pk
        with self.assertRaises(ValidationError):s.confirm_standard(body,self.user)
        self.assertEqual(StandardRevision.objects.count(),0)

    def test_import_identity_conflict_is_rejected_without_partial_write(self):
        body=self.body();body['import_source']={'identity':{'material':'ANOTHER-SYNTHETIC-MATERIAL'}}
        with self.assertRaises(ValidationError):s.confirm_standard(body,self.user)
        self.assertEqual(StandardRevision.objects.count(),0)
        self.assertEqual(ProductInspectionCriterion.objects.count(),0)

    def test_drawing_only_dimension_is_preserved_but_not_counted_as_package_measurement(self):
        body=self.body();body['dimension_count']=1
        body['criteria'].append({**body['criteria'][0],'name':'内径','lower':'99.95','upper':'100.10','follow_package':False})
        standard=s.confirm_standard(body,self.user)
        self.assertEqual(standard.snapshot['dimension_count'],1)
        self.assertEqual(len(standard.snapshot['criteria']),2)
        from .measurements import applicable_criteria
        self.assertEqual(len(applicable_criteria(standard.snapshot['criteria'])),1)

    def test_fixed_bake_and_text_process_can_save_without_any_tolerance_input(self):
        body=self.body();body.update(bake={"state":"APPLICABLE","temperature_value":"145","minutes_value":"90"},
            process_parameters={"oil":{"state":"CONFIRMED","raw_text":"少量"},"venting":{"state":"CONFIRMED","raw_text":"2次"},"upper_set_temperature":{"state":"CONFIRMED","value":"180","unit":"℃"}})
        standard=s.confirm_standard(body,self.user)
        self.assertEqual(standard.snapshot["bake"]["temperature_value"],"145")
        self.assertEqual(standard.snapshot["bake"]["temperature_lower"],"145")
        self.assertEqual(standard.snapshot["process_parameters"]["oil"]["raw_text"],"少量")
        self.assertNotIn("lower",standard.snapshot["process_parameters"]["oil"])
        self.assertEqual(standard.snapshot["process_parameters"]["upper_set_temperature"]["value"],"180")

    def test_no_bake_or_unspecified_bake_does_not_require_temperature_or_duration(self):
        for state in ("UNKNOWN","NOT_APPLICABLE"):
            body=self.body();body["bake"]={"state":state}
            standard=s.confirm_standard(body,self.user)
            self.assertEqual(standard.snapshot["bake"],{"state":state})

    def test_bake_fixed_value_conflicts_and_invalid_numbers_are_rejected_atomically(self):
        for change in ({"temperature_value":"NaN"},{"temperature_value":"-1"},{"temperature_lower":"140"},{"minutes_value":""}):
            body=self.body();body["bake"]={"state":"APPLICABLE","temperature_value":"145","minutes_value":"90",**change}
            with self.assertRaises(ValidationError):s.confirm_standard(body,self.user)
        self.assertEqual(StandardRevision.objects.count(),0)

    def test_new_fixed_bake_version_does_not_rewrite_existing_range(self):
        body=self.body();body["bake"]={"state":"APPLICABLE","temperature_lower":"140","temperature_upper":"150","minutes_lower":"80","minutes_upper":"100"}
        first=s.confirm_standard(body,self.user);frozen=first.snapshot.copy()
        body=self.body();body["bake"]={"state":"APPLICABLE","temperature_value":"145","minutes_value":"90"}
        second=s.confirm_standard(body,self.user);first.refresh_from_db()
        self.assertEqual(first.snapshot,frozen)
        self.assertNotIn("temperature_value",first.snapshot["bake"])
        self.assertEqual(second.snapshot["bake"]["temperature_value"],"145")
