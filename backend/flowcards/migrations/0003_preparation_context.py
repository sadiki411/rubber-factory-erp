from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("flowcards", "0002_default_appearance"), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.AddField("standardrevision", "status", models.CharField(max_length=16, default="LEGACY", db_index=True)),
        migrations.AddField("standardrevision", "customer_key", models.CharField(max_length=100, blank=True)),
        migrations.AddField("standardrevision", "source_kind", models.CharField(max_length=24, default="UNCONFIRMED")),
        migrations.AddField("standardrevision", "applicable_order", models.ForeignKey("quality.QualityOrder", on_delete=django.db.models.deletion.PROTECT, null=True, blank=True)),
        migrations.AlterModelOptions("standardrevision", {"permissions": [("maintain_product_standard", "维护及生效产品工艺检验标准")]}),
        migrations.AddField("cardgroup", "context_key", models.CharField(max_length=100, blank=True, db_index=True)),
        migrations.AddField("cardgroup", "revision", models.PositiveIntegerField(default=1)),
        migrations.AddField("cardgroup", "range_start", models.PositiveIntegerField(null=True, blank=True)),
        migrations.AddField("cardgroup", "range_end", models.PositiveIntegerField(null=True, blank=True)),
        migrations.AddField("cardgroup", "updated_at", models.DateTimeField(auto_now=True)),
        migrations.AddField("cardgroup", "updated_by", models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=django.db.models.deletion.PROTECT, null=True, related_name="updated_flow_groups")),
        migrations.AddConstraint("cardgroup", models.UniqueConstraint(fields=["context_key"], condition=models.Q(status="DRAFT") & ~models.Q(context_key=""), name="fc_one_context_draft")),
        migrations.AddConstraint("cardgroup", models.CheckConstraint(condition=models.Q(range_start__isnull=True, range_end__isnull=True) | models.Q(range_start__isnull=False, range_end__isnull=False, range_start__gte=1, range_end__gte=models.F("range_start")), name="fc_valid_mold_range")),
        migrations.CreateModel(name="OrderCardPreparation", fields=[
            ("id", models.BigAutoField(primary_key=True, serialize=False, auto_created=True, verbose_name="ID")),
            ("created_at", models.DateTimeField(auto_now_add=True)),
            ("created_by", models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=django.db.models.deletion.PROTECT)),
            ("order", models.OneToOneField("quality.QualityOrder", on_delete=django.db.models.deletion.PROTECT, related_name="card_preparation")),
            ("draft", models.ForeignKey("flowcards.CardGroup", on_delete=django.db.models.deletion.PROTECT, null=True, blank=True)),
            ("state", models.CharField(max_length=24, default="PENDING")),
            ("updated_at", models.DateTimeField(auto_now=True)),
        ]),
    ]
