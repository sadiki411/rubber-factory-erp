import decimal

import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("molds", "0004_remove_moldasset_mold_status_location_consistent_and_more"),
        ("production", "0005_productionrun_material_changed_at"),
        ("quality", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="ManualPerformanceEntry",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("voided_at", models.DateTimeField(blank=True, db_index=True, null=True, verbose_name="作废时间")),
                ("void_reason", models.CharField(blank=True, default="", max_length=200, verbose_name="作废原因")),
                ("entry_date", models.DateField(db_index=True, verbose_name="绩效日期")),
                ("entry_type", models.CharField(choices=[("PRODUCTION", "生产"), ("QUALITY", "品检/出货"), ("REWORK", "退回/返工")], db_index=True, max_length=20, verbose_name="记录类型")),
                ("staff_name", models.CharField(blank=True, db_index=True, default="", max_length=100, verbose_name="人员名称")),
                ("order_no", models.CharField(blank=True, db_index=True, default="", max_length=100, verbose_name="订单号")),
                ("produced_mold_count", models.PositiveIntegerField(default=0, verbose_name="生产模数")),
                ("production_hours", models.DecimalField(decimal_places=2, default=0, max_digits=10, validators=[django.core.validators.MinValueValidator(decimal.Decimal("0"))], verbose_name="实际生产工时")),
                ("inspection_quantity", models.PositiveIntegerField(default=0, verbose_name="质检数量")),
                ("qualified_quantity", models.PositiveIntegerField(default=0, verbose_name="合格数量")),
                ("defective_quantity", models.PositiveIntegerField(default=0, verbose_name="不良数量")),
                ("shipped_quantity", models.PositiveIntegerField(default=0, verbose_name="出货数量")),
                ("returned_quantity", models.PositiveIntegerField(default=0, verbose_name="退回数量")),
                ("reason_category", models.CharField(blank=True, choices=[("APPEARANCE", "外观"), ("DIMENSION", "尺寸"), ("MATERIAL", "材质"), ("MIXED", "混料/混装"), ("PACKAGING", "包装"), ("OTHER", "其他")], default="", max_length=20, verbose_name="不良原因分类")),
                ("reworked_quantity", models.PositiveIntegerField(default=0, verbose_name="返工数量")),
                ("recovered_quantity", models.PositiveIntegerField(default=0, verbose_name="返工合格数量")),
                ("scrap_quantity", models.PositiveIntegerField(default=0, verbose_name="报废数量")),
                ("rework_hours", models.DecimalField(decimal_places=2, default=0, max_digits=10, validators=[django.core.validators.MinValueValidator(decimal.Decimal("0"))], verbose_name="返工工时")),
                ("notes", models.TextField(blank=True, verbose_name="备注")),
                ("created_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="manual_performance_entries", to=settings.AUTH_USER_MODEL, verbose_name="创建人")),
                ("machine", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="manual_performance_entries", to="molds.machine", verbose_name="机台")),
                ("quality_employee", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="manual_performance_entries", to="quality.qualityemployee", verbose_name="品检/返工员工")),
                ("voided_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="%(app_label)s_%(class)s_voided", to=settings.AUTH_USER_MODEL, verbose_name="作废人")),
            ],
            options={"ordering": ["-entry_date", "-id"]},
        ),
        migrations.CreateModel(
            name="ManualFinancialEntry",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("voided_at", models.DateTimeField(blank=True, db_index=True, null=True, verbose_name="作废时间")),
                ("void_reason", models.CharField(blank=True, default="", max_length=200, verbose_name="作废原因")),
                ("occurred_on", models.DateField(db_index=True, verbose_name="发生日期")),
                ("direction", models.CharField(choices=[("INCOME", "收入"), ("EXPENSE", "支出")], db_index=True, max_length=10, verbose_name="收支方向")),
                ("category", models.CharField(choices=[("SALES", "销售收入"), ("MATERIAL", "材料成本"), ("LABOR", "人工成本"), ("ENERGY", "能耗成本"), ("OTHER", "其他"), ("ADJUSTMENT", "调整")], db_index=True, max_length=20, verbose_name="财务分类")),
                ("amount", models.DecimalField(decimal_places=2, max_digits=14, validators=[django.core.validators.MinValueValidator(decimal.Decimal("0.01"))], verbose_name="金额")),
                ("staff_name", models.CharField(blank=True, default="", max_length=100, verbose_name="关联人员")),
                ("order_no", models.CharField(blank=True, db_index=True, default="", max_length=100, verbose_name="订单号")),
                ("description", models.CharField(max_length=200, verbose_name="事项说明")),
                ("notes", models.TextField(blank=True, verbose_name="备注")),
                ("created_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="manual_financial_entries", to=settings.AUTH_USER_MODEL, verbose_name="创建人")),
                ("machine", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="manual_financial_entries", to="molds.machine", verbose_name="关联机台")),
                ("voided_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="%(app_label)s_%(class)s_voided", to=settings.AUTH_USER_MODEL, verbose_name="作废人")),
            ],
            options={"ordering": ["-occurred_on", "-id"]},
        ),
        migrations.AddIndex(
            model_name="manualperformanceentry",
            index=models.Index(fields=["entry_type", "entry_date"], name="manual_perf_type_date_idx"),
        ),
        migrations.AddIndex(
            model_name="manualfinancialentry",
            index=models.Index(fields=["direction", "occurred_on"], name="manual_fin_dir_date_idx"),
        ),
    ]
