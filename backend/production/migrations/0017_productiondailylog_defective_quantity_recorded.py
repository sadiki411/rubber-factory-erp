from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("production", "0016_production_yield_record")]
    operations = [migrations.AddField(
        model_name="productiondailylog", name="defective_quantity_recorded",
        field=models.BooleanField("不良数量是否明确登记", null=True, blank=True, default=None),
    )]
