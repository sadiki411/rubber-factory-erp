from django.conf import settings
from django.db import migrations, models
import django.core.validators


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("production", "0015_explicit_strip_weights"),
    ]

    operations = [
        migrations.CreateModel(
            name="ProductionYieldRecord",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "source_order_ids",
                    models.JSONField(blank=True, default=list, verbose_name="计算订单ID"),
                ),
                (
                    "production_quantity",
                    models.PositiveIntegerField(verbose_name="实际生产件数"),
                ),
                (
                    "effective_shipped_quantity",
                    models.PositiveIntegerField(verbose_name="有效出货数量"),
                ),
                (
                    "remaining_quantity",
                    models.PositiveIntegerField(default=0, verbose_name="人工确认剩余数量"),
                ),
                (
                    "yield_percent",
                    models.DecimalField(
                        blank=True,
                        decimal_places=2,
                        max_digits=8,
                        null=True,
                        validators=[django.core.validators.MinValueValidator(0)],
                        verbose_name="最终良率(%)",
                    ),
                ),
                ("notes", models.TextField(blank=True, verbose_name="说明")),
                (
                    "confirmed_at",
                    models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="确认时间"),
                ),
                (
                    "confirmed_by",
                    models.ForeignKey(
                        on_delete=models.deletion.PROTECT,
                        related_name="confirmed_production_yields",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="确认人",
                    ),
                ),
                (
                    "run",
                    models.OneToOneField(
                        on_delete=models.deletion.PROTECT,
                        related_name="final_yield_record",
                        to="production.productionrun",
                        verbose_name="生产任务",
                    ),
                ),
            ],
            options={
                "ordering": ["-confirmed_at", "-id"],
            },
        ),
    ]
