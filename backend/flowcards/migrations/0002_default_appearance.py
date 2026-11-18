from django.db import migrations


def seed(apps, schema_editor):
    model = apps.get_model("flowcards", "AppearanceIssue")
    for code, name in [("BURR", "毛刺"), ("DIRT", "杂质"), ("AIR", "包风"), ("SHORT", "缺料")]:
        model.objects.get_or_create(code=code, defaults={"name": name})


class Migration(migrations.Migration):
    dependencies = [("flowcards", "0001_initial")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
