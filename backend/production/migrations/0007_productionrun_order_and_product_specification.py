import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        (
            "production",
            "0006_remove_productionstation_production_station_position_valid_and_more",
        ),
        ("orders", "0001_initial"),
        ("quality", "0002_promote_qualityorder_to_global_order"),
    ]

    operations = [
        migrations.AddField(
            model_name="productionrun",
            name="order",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="production_runs",
                to="quality.qualityorder",
                verbose_name="关联订单明细",
            ),
        ),
        migrations.AddField(
            model_name="productionrun",
            name="product_specification",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="production_runs",
                to="orders.productspecification",
                verbose_name="关联产品规格",
            ),
        ),
    ]
