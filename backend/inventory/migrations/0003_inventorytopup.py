import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0002_seed_fixed_locations"),
        ("quality", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="InventoryTopUp",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("request_id", models.UUIDField(editable=False, unique=True)),
                ("payload_hash", models.CharField(editable=False, max_length=64)),
                ("received_on", models.DateField(verbose_name="本次入库日期")),
                ("inspector_name", models.CharField(blank=True, default="", max_length=100, verbose_name="本次品检员快照")),
                ("inspected_on", models.DateField(blank=True, null=True, verbose_name="本次检验日期")),
                ("quality_status", models.CharField(choices=[("WAITING", "待检"), ("PASSED", "已检合格"), ("FAILED", "不合格"), ("HOLD", "冻结")], max_length=12)),
                ("product_snapshot", models.JSONField(default=dict)),
                ("quantity_before", models.PositiveIntegerField()),
                ("quantity_after", models.PositiveIntegerField()),
                ("bag_count_before", models.PositiveIntegerField()),
                ("bag_count_after", models.PositiveIntegerField()),
                ("inspector", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="inventory_top_ups", to="quality.qualityemployee")),
                ("transaction", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="top_up", to="inventory.inventorytransaction")),
            ],
            options={"ordering": ["-created_at", "-id"]},
        ),
    ]
