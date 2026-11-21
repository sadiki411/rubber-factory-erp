import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("orders", "0010_orderinspectionsource_customerinspectionreview"),
                    ("production", "0017_productiondailylog_defective_quantity_recorded")]

    operations = [
        migrations.CreateModel(
            name="ProductMachineTemperature",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("temperature_c", models.DecimalField(max_digits=6, decimal_places=2, verbose_name="建议设定模温（℃）")),
                ("product", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="machine_temperatures", to="orders.productspecification")),
                ("station", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="production.productionstation")),
            ],
            options={"constraints": [models.UniqueConstraint(fields=("product", "station"), name="one_temperature_per_product_station")]},
        ),
    ]
