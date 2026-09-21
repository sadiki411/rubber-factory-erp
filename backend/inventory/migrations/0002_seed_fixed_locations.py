from django.db import migrations


def seed_locations(apps, schema_editor):
    InventoryLocation = apps.get_model("inventory", "InventoryLocation")
    for rack_number in range(1, 10):
        rack_code = f"K{rack_number:02d}"
        if rack_number <= 6:
            rack_type = "LARGE"
            level_count, position_count = 4, 5
            allows_basket, allows_bag = True, True
        else:
            rack_type = "SMALL"
            level_count, position_count = 5, 2
            allows_basket, allows_bag = False, True
        for level_no in range(1, level_count + 1):
            for position_no in range(1, position_count + 1):
                code = f"{rack_code}-L{level_no:02d}-P{position_no:02d}"
                InventoryLocation.objects.get_or_create(
                    code=code,
                    defaults={
                        "rack_code": rack_code,
                        "rack_type": rack_type,
                        "level_no": level_no,
                        "position_no": position_no,
                        "label": f"{rack_code} 第{level_no}层 第{position_no}位",
                        "allows_basket": allows_basket,
                        "allows_bag": allows_bag,
                    },
                )
    InventoryLocation.objects.get_or_create(
        code="F01-FRIDGE",
        defaults={
            "rack_code": "F01",
            "rack_type": "FRIDGE",
            "label": "冰箱",
            "allows_basket": False,
            "allows_bag": False,
        },
    )


class Migration(migrations.Migration):
    dependencies = [("inventory", "0001_initial")]
    operations = [migrations.RunPython(seed_locations, migrations.RunPython.noop)]

