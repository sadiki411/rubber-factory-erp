from decimal import Decimal

import django.core.validators
import django.db.models.deletion
from django.db import migrations, models


def backfill_source_shipment_batch(apps, schema_editor):
    QualityReworkCase = apps.get_model("quality", "QualityReworkCase")
    cases = list(QualityReworkCase.objects.filter(
        shipment_batch__isnull=True,
        shipment_line__isnull=False,
    ).select_related("shipment_line"))
    for case in cases:
        case.shipment_batch_id = case.shipment_line.batch_id
    if cases:
        QualityReworkCase.objects.bulk_update(cases, ["shipment_batch"])


class Migration(migrations.Migration):

    dependencies = [
        ("quality", "0008_shipment_repeat_entry_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="qualityreworkcase",
            name="shipment_batch",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="rework_cases",
                to="quality.qualityshipmentbatch",
                verbose_name="source shipment batch",
            ),
        ),
        migrations.AddField(
            model_name="qualityreworkcase",
            name="shipment_unit_no",
            field=models.PositiveIntegerField(
                blank=True,
                null=True,
                verbose_name="physical shipment unit number",
            ),
        ),
        migrations.RunPython(
            backfill_source_shipment_batch,
            migrations.RunPython.noop,
        ),
        migrations.CreateModel(
            name="QualityReturnAllocation",
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
                ("piece_quantity", models.PositiveIntegerField()),
                (
                    "net_weight_kg",
                    models.DecimalField(
                        decimal_places=3,
                        max_digits=14,
                        validators=[
                            django.core.validators.MinValueValidator(Decimal("0"))
                        ],
                    ),
                ),
                (
                    "case",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="shipment_allocations",
                        to="quality.qualityreworkcase",
                    ),
                ),
                (
                    "shipment_line",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="rework_allocations",
                        to="quality.qualityshipmentline",
                    ),
                ),
            ],
            options={
                "ordering": ["case_id", "shipment_line_id"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("case", "shipment_line"),
                        name="quality_return_case_line_uniq",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("piece_quantity__gt", 0)),
                        name="quality_return_allocation_piece_gt_zero",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("net_weight_kg__gte", 0)),
                        name="quality_return_allocation_weight_nonnegative",
                    ),
                ],
            },
        ),
        migrations.AddConstraint(
            model_name="qualityreworkcase",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(("shipment_unit_no__isnull", True))
                    | models.Q(("shipment_batch__isnull", False))
                ),
                name="quality_rework_unit_requires_batch_ck",
            ),
        ),
        migrations.AddConstraint(
            model_name="qualityreworkcase",
            constraint=models.UniqueConstraint(
                condition=(
                    models.Q(("origin", "CUSTOMER_RETURN"))
                    & models.Q(("shipment_batch__isnull", False))
                    & models.Q(("shipment_unit_no__isnull", False))
                    & ~models.Q(("status", "CANCELLED"))
                ),
                fields=("shipment_batch", "shipment_unit_no"),
                name="quality_active_return_batch_unit_uniq",
            ),
        ),
    ]
