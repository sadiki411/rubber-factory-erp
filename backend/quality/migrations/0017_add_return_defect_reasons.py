from django.db import migrations
from django.db.models import Max


RETURN_REASONS = (
    ("TEARING", "拔破"),
    ("VOID", "空洞"),
    ("CONTAMINATION", "脏"),
    ("DEFORMATION", "变形"),
)


def add_return_reasons(apps, schema_editor):
    Reason = apps.get_model("quality", "DefectReason")
    reasons = Reason.objects.using(schema_editor.connection.alias)
    sort_order = reasons.aggregate(value=Max("sort_order"))["value"] or 0
    for code, name in RETURN_REASONS:
        # Reuse an existing dictionary entry (including a manually added one).
        # Never rename, replace or reassign reasons on historical return records.
        reason = reasons.filter(code=code).first() or reasons.filter(name=name).first()
        if reason is None:
            sort_order += 10
            reasons.create(code=code, name=name, is_active=True, is_system=True, sort_order=sort_order)
        elif not reason.is_active:
            reasons.filter(pk=reason.pk).update(is_active=True)


class Migration(migrations.Migration):
    dependencies = [("quality", "0016_qualityemployee_production_profile")]
    operations = [migrations.RunPython(add_return_reasons, migrations.RunPython.noop)]
