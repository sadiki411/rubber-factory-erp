from decimal import Decimal

from django.db import migrations, models
from django.core.validators import MinValueValidator


class Migration(migrations.Migration):
    dependencies = [
        ("production", "0014_unified_employee_profiles"),
    ]

    operations = [
        migrations.AddField(
            model_name="productionrun",
            name="large_strip_weight_g",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text="成型前排料记录，不代表成品重量或库存重量。",
                max_digits=10,
                null=True,
                validators=[MinValueValidator(Decimal("0"))],
                verbose_name="大条条重(g)",
            ),
        ),
        migrations.AddField(
            model_name="productionrun",
            name="large_strip_count",
            field=models.PositiveIntegerField(
                blank=True,
                null=True,
                validators=[MinValueValidator(1)],
                verbose_name="大条数量",
            ),
        ),
        migrations.AddField(
            model_name="productionrun",
            name="small_strip_weight_g",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text="成型前补料记录，不代表成品重量或库存重量。",
                max_digits=10,
                null=True,
                validators=[MinValueValidator(Decimal("0"))],
                verbose_name="小条条重(g)",
            ),
        ),
        migrations.AddField(
            model_name="productionrun",
            name="small_strip_count",
            field=models.PositiveIntegerField(
                blank=True,
                null=True,
                validators=[MinValueValidator(1)],
                verbose_name="小条数量",
            ),
        ),
    ]
