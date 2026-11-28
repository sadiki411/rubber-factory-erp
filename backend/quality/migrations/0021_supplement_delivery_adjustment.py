import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("quality", "0020_shipment_supplements")]

    operations = [
        migrations.AddField(
            model_name="shipmentsupplement",
            name="reused_card_no",
            field=models.CharField(blank=True, max_length=150),
        ),
        migrations.CreateModel(
            name="ShipmentSupplementDeliveryAdjustment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("unit_no", models.PositiveIntegerField()),
                ("piece_quantity", models.PositiveIntegerField()),
                ("evidence", models.CharField(max_length=500)),
                ("allocation", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                    related_name="delivery_adjustments", to="quality.qualityshipmentorderallocation")),
                ("origin", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                    related_name="delivery_adjustments", to="quality.shipmentsupplementorigin")),
            ],
            options={"constraints": [
                models.UniqueConstraint(fields=("allocation", "unit_no"), name="quality_delivery_adjustment_unit_uniq"),
                models.CheckConstraint(condition=models.Q(piece_quantity__gt=0), name="quality_delivery_adjustment_positive"),
            ]},
        ),
    ]
