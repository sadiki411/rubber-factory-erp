from django.db import migrations, models

import orders.models


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0005_productspecification_actual_cut_weight_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="productspecification",
            name="main_image",
            field=models.ImageField(
                blank=True,
                help_text="产品规格资料的可选外观照片。",
                upload_to=orders.models.product_specification_image_path,
                verbose_name="产品照片",
            ),
        ),
    ]
