from decimal import Decimal

from django.db import migrations, models
from django.core.validators import MinValueValidator


class Migration(migrations.Migration):
    dependencies = [
        ("orders", "0006_productspecification_main_image"),
    ]

    operations = [
        migrations.AddField(
            model_name="productspecification",
            name="large_strip_weight_g",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text="成型前排料记录，不代表成品重量。",
                max_digits=10,
                null=True,
                validators=[MinValueValidator(Decimal("0"))],
                verbose_name="大条条重(g)",
            ),
        ),
        migrations.AddField(
            model_name="productspecification",
            name="large_strip_count",
            field=models.PositiveIntegerField(
                blank=True,
                null=True,
                validators=[MinValueValidator(1)],
                verbose_name="大条数量",
            ),
        ),
        migrations.AddField(
            model_name="productspecification",
            name="small_strip_weight_g",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text="成型前补料记录，不代表成品重量。",
                max_digits=10,
                null=True,
                validators=[MinValueValidator(Decimal("0"))],
                verbose_name="小条条重(g)",
            ),
        ),
        migrations.AddField(
            model_name="productspecification",
            name="small_strip_count",
            field=models.PositiveIntegerField(
                blank=True,
                null=True,
                validators=[MinValueValidator(1)],
                verbose_name="小条数量",
            ),
        ),
    ]
