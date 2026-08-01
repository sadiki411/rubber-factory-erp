import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("orders", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="materialreceipt",
            name="external_key",
            field=models.CharField(blank=True, db_index=True, default="", max_length=500),
        ),
        migrations.AddField(
            model_name="materialreceipt",
            name="last_imported_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="materialreceipt",
            name="last_source_batch",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="latest_material_receipts",
                to="orders.businessimportbatch",
            ),
        ),
        migrations.AddField(
            model_name="materialreceipt",
            name="source_document_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="materialreceipt",
            name="source_system",
            field=models.CharField(blank=True, db_index=True, default="", max_length=100),
        ),
        migrations.AddConstraint(
            model_name="materialreceipt",
            constraint=models.UniqueConstraint(
                condition=~models.Q(external_key=""),
                fields=("external_key",),
                name="uniq_material_receipt_external_key",
            ),
        ),
    ]
