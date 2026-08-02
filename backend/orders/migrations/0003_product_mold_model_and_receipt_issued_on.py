import django.db.models.deletion
from django.db import migrations, models
from django.utils import timezone


def backfill_safe_links_and_issue_dates(apps, schema_editor):
    ProductSpecification = apps.get_model("orders", "ProductSpecification")
    MaterialReceipt = apps.get_model("orders", "MaterialReceipt")
    MoldModel = apps.get_model("molds", "MoldModel")

    mold_ids_by_code = dict(MoldModel.objects.values_list("code", "id"))
    products_to_update = []
    for product in ProductSpecification.objects.filter(
        mold_model__isnull=True
    ).exclude(mold_no=""):
        mold_model_id = mold_ids_by_code.get(product.mold_no)
        if mold_model_id is not None:
            product.mold_model_id = mold_model_id
            products_to_update.append(product)
    if products_to_update:
        ProductSpecification.objects.bulk_update(products_to_update, ["mold_model"])

    receipts_to_update = []
    for receipt in MaterialReceipt.objects.filter(
        issued_on__isnull=True,
        source_document_at__isnull=False,
    ):
        source_document_at = receipt.source_document_at
        if timezone.is_aware(source_document_at):
            source_document_at = timezone.localtime(source_document_at)
        receipt.issued_on = source_document_at.date()
        receipts_to_update.append(receipt)
    if receipts_to_update:
        MaterialReceipt.objects.bulk_update(receipts_to_update, ["issued_on"])


class Migration(migrations.Migration):
    dependencies = [
        ("molds", "0004_remove_moldasset_mold_status_location_consistent_and_more"),
        ("orders", "0002_materialreceipt_import_identity"),
    ]

    operations = [
        migrations.AddField(
            model_name="productspecification",
            name="mold_model",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="product_specifications",
                to="molds.moldmodel",
                verbose_name="模具型号",
            ),
        ),
        migrations.AddField(
            model_name="materialreceipt",
            name="issued_on",
            field=models.DateField(
                blank=True,
                db_index=True,
                null=True,
                verbose_name="发料日期",
            ),
        ),
        migrations.AlterField(
            model_name="businessimportbatch",
            name="source_type",
            field=models.CharField(
                choices=[
                    ("UNKNOWN", "无法识别"),
                    ("PRODUCT_SPECIFICATIONS", "产品规格数据"),
                    ("INTERNAL_ORDERS", "内部季度订单"),
                    ("FACTORY_WORK_CONTACT", "生产工作联络单"),
                    ("MATERIAL_ISSUE", "混料发料清单"),
                ],
                max_length=40,
            ),
        ),
        migrations.AlterModelOptions(
            name="materialreceipt",
            options={"ordering": ["-issued_on", "-manufactured_on", "-id"]},
        ),
        migrations.RunPython(
            backfill_safe_links_and_issue_dates,
            migrations.RunPython.noop,
        ),
    ]
