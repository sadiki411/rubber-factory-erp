from decimal import Decimal

import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("quality", "0007_qualityshipment_inspectors"),
    ]

    operations = [
        migrations.AddField(
            model_name="qualityshipmentbatch",
            name="process_card_shipment_quantity",
            field=models.PositiveIntegerField(
                blank=True,
                null=True,
                verbose_name="process-card shipment quantity per batch",
            ),
        ),
        migrations.AddField(
            model_name="qualityshipmentbatch",
            name="single_batch_net_weight_kg",
            field=models.DecimalField(
                blank=True,
                decimal_places=3,
                max_digits=14,
                null=True,
                validators=[django.core.validators.MinValueValidator(Decimal("0.001"))],
                verbose_name="single batch net weight (kg)",
            ),
        ),
        migrations.AddField(
            model_name="qualityshipmentline",
            name="process_card_shipment_quantity",
            field=models.PositiveIntegerField(
                blank=True,
                null=True,
                verbose_name="process-card shipment quantity per batch",
            ),
        ),
        migrations.AddField(
            model_name="qualityshipmentline",
            name="single_batch_net_weight_kg",
            field=models.DecimalField(
                blank=True,
                decimal_places=3,
                max_digits=14,
                null=True,
                validators=[django.core.validators.MinValueValidator(Decimal("0.001"))],
                verbose_name="single batch net weight (kg)",
            ),
        ),
    ]
