from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("orders", "0008_purchase_order_images"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="OrderYieldRemainder",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("group_key", models.CharField(max_length=64, unique=True)),
                ("source_order_ids", models.JSONField(default=list)),
                ("source_run_ids", models.JSONField(default=list)),
                ("production_quantity_snapshot", models.PositiveBigIntegerField()),
                ("shipped_quantity_snapshot", models.PositiveBigIntegerField()),
                ("remaining_quantity", models.PositiveIntegerField(default=0)),
                ("notes", models.CharField(blank=True, max_length=500)),
                ("confirmed_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="order_yield_remainders", to=settings.AUTH_USER_MODEL)),
            ],
            options={"abstract": False},
        ),
    ]
