from django.db import migrations, models
import django.db.models.deletion
from django.conf import settings
import uuid
import unicodedata


def normalize_name(value):
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    return " ".join(normalized.split()).casefold()


def generated_no(existing):
    while True:
        value = f"PROD-{uuid.uuid4().hex[:12].upper()}"
        if value not in existing:
            existing.add(value)
            return value


def migrate_production_people(apps, schema_editor):
    ProductionEmployee = apps.get_model("production", "ProductionEmployee")
    ProductionDailyLog = apps.get_model("production", "ProductionDailyLog")
    QualityEmployee = apps.get_model("quality", "QualityEmployee")
    IdentityMatch = apps.get_model("production", "ProductionEmployeeIdentityMatch")

    existing_numbers = set(QualityEmployee.objects.values_list("employee_no", flat=True))
    old_to_new = {}
    resolved_by_name = {}

    def resolve(name, source_id=None):
        normalized = normalize_name(name)
        if not normalized:
            return None
        if normalized in resolved_by_name:
            return resolved_by_name[normalized][0]
        candidates = [
            item
            for item in QualityEmployee.objects.all().order_by("id")
            if normalize_name(item.name) == normalized
        ]
        if len(candidates) == 1:
            employee = candidates[0]
            if not employee.production_enabled:
                employee.production_enabled = True
                employee.save(update_fields=["production_enabled"])
            resolved_by_name[normalized] = (employee, None)
            return employee

        employee = QualityEmployee.objects.create(
            employee_no=generated_no(existing_numbers),
            name=" ".join(str(name).split()),
            role="PRODUCTION",
            production_enabled=True,
        )
        if len(candidates) > 1:
            match = IdentityMatch.objects.create(
                source_employee_id=source_id,
                source_name=employee.name,
                temporary_employee_id=employee.pk,
                status="PENDING",
            )
            match.candidates.set(candidates)
            resolved_by_name[normalized] = (employee, match)
        else:
            resolved_by_name[normalized] = (employee, None)
        return employee

    for old in ProductionEmployee.objects.all().order_by("id"):
        old_to_new[old.pk] = resolve(old.name, old.pk)

    for log in ProductionDailyLog.objects.all().order_by("id"):
        target = old_to_new.get(log.operator_employee_id)
        if target is None:
            target = resolve(log.operator)
        if target is not None:
            log.employee_id = target.pk
            log.save(update_fields=["employee"])

        old_assistants = log.assistant_operators.all()
        assistant_targets = [
            old_to_new[item.pk]
            for item in old_assistants
            if item.pk in old_to_new and old_to_new[item.pk] is not None
        ]
        if assistant_targets:
            log.assistant_employees.set([item.pk for item in assistant_targets])


def reverse_migrate_production_people(apps, schema_editor):
    # The legacy columns remain in place intentionally.  New shared links are
    # not destructively copied back because doing so could merge legitimate
    # same-name employees.
    return None


class Migration(migrations.Migration):

    dependencies = [
        ("production", "0013_productionrunorder"),
        ("quality", "0016_qualityemployee_production_profile"),
    ]

    operations = [
        migrations.CreateModel(
            name="ProductionEmployeeIdentityMatch",
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
                ("source_employee_id", models.PositiveIntegerField(blank=True, null=True, verbose_name="原生产员工ID")),
                ("source_name", models.CharField(max_length=100, verbose_name="原生产姓名")),
                ("status", models.CharField(choices=[("PENDING", "待确认"), ("RESOLVED", "已确认")], default="PENDING", max_length=20, verbose_name="状态")),
                ("resolved_at", models.DateTimeField(blank=True, null=True, verbose_name="确认时间")),
                ("candidates", models.ManyToManyField(blank=True, related_name="production_identity_candidates", to="quality.qualityemployee", verbose_name="可能匹配的员工")),
                ("resolved_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="resolved_production_employee_identities", to=settings.AUTH_USER_MODEL, verbose_name="确认人")),
                ("resolved_employee", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="resolved_production_identities", to="quality.qualityemployee", verbose_name="确认后的员工")),
                ("temporary_employee", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="production_identity_matches", to="quality.qualityemployee", verbose_name="迁移后临时员工档案")),
            ],
            options={"ordering": ["status", "source_name", "id"]},
        ),
        migrations.AddField(
            model_name="productiondailylog",
            name="employee",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="production_logs", to="quality.qualityemployee", verbose_name="统一员工档案（主要作业员）"),
        ),
        migrations.AddField(
            model_name="productiondailylog",
            name="assistant_employees",
            field=models.ManyToManyField(blank=True, related_name="assisted_production_logs", to="quality.qualityemployee", verbose_name="统一员工档案（协助人员）"),
        ),
        migrations.RunPython(migrate_production_people, reverse_migrate_production_people),
    ]
