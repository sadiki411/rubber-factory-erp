from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("production", "0004_correct_six_machine_layout"),
    ]

    operations = [
        migrations.AddField(
            model_name="productionrun",
            name="material_changed_at",
            field=models.DateTimeField(
                blank=True,
                db_index=True,
                null=True,
                verbose_name="换料时间",
            ),
        ),
    ]
