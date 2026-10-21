from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("orders", "0007_productspecification_strip_weights")]

    operations = [
        migrations.AlterField(
            model_name="businessimportbatch", name="source_type",
            field=models.CharField(max_length=40, choices=[
                ("UNKNOWN", "无法识别"),
                ("PRODUCT_SPECIFICATIONS", "产品规格数据"),
                ("INTERNAL_ORDERS", "内部季度订单"),
                ("FACTORY_WORK_CONTACT", "生产工作联络单"),
                ("MATERIAL_ISSUE", "混料发料清单"),
                ("PURCHASE_ORDER_IMAGES", "艾斯霖采购单图片"),
            ]),
        ),
    ]
