import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("quality", "0002_promote_qualityorder_to_global_order"),
    ]

    operations = [
        migrations.AddField(
            model_name="qualityorder",
            name="external_key",
            field=models.CharField(blank=True, db_index=True, default="", max_length=500),
        ),
        migrations.AddField(
            model_name="qualityorder",
            name="last_imported_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="qualityorder",
            name="last_source_batch",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="latest_orders",
                to="orders.businessimportbatch",
            ),
        ),
        migrations.AddField(
            model_name="qualityorder",
            name="process_card_text",
            field=models.CharField(blank=True, default="", max_length=200, verbose_name="流程卡原始记录"),
        ),
        migrations.AddField(
            model_name="qualityorder",
            name="production_quantity",
            field=models.CharField(blank=True, default="", max_length=200, verbose_name="生产数量原始记录"),
        ),
        migrations.AddField(
            model_name="qualityorder",
            name="shipment_date",
            field=models.CharField(blank=True, default="", max_length=200, verbose_name="出货日期原始记录"),
        ),
        migrations.AddField(
            model_name="qualityorder",
            name="shipped_quantity",
            field=models.CharField(blank=True, default="", max_length=200, verbose_name="出货数量原始记录"),
        ),
        migrations.AddField(
            model_name="qualityorder",
            name="source_document_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="qualityorder",
            name="source_system",
            field=models.CharField(blank=True, db_index=True, default="", max_length=100),
        ),
        migrations.AddConstraint(
            model_name="qualityorder",
            constraint=models.UniqueConstraint(
                condition=~models.Q(external_key=""),
                fields=("external_key",),
                name="uniq_quality_order_external_key",
            ),
        ),
    ]
