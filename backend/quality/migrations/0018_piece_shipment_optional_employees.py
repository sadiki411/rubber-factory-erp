from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("quality", "0017_add_return_defect_reasons")]

    operations = [
        migrations.AlterField(
            model_name=model, name=name,
            field=models.ForeignKey(
                to="quality.qualityemployee", on_delete=django.db.models.deletion.PROTECT,
                null=True, blank=True, related_name=related_name, verbose_name=label,
            ),
        )
        for model, name, related_name, label in [
            ("qualityshipment", "inspector", "inspected_shipments", "品检员"),
            ("returnrework", "responsible_inspector", "responsible_reworks", "责任品检员"),
            ("returnrework", "rework_employee", "handled_reworks", "返工处理员工"),
        ]
    ] + [
        operation
        for model in ("qualityshipment", "returnrework")
        for operation in (
            migrations.AddField(
                model_name=model, name="client_request_id",
                field=models.UUIDField(null=True, blank=True, unique=True),
            ),
            migrations.AddField(
                model_name=model, name="request_fingerprint",
                field=models.CharField(max_length=64, blank=True, default="", editable=False),
            ),
        )
    ]
