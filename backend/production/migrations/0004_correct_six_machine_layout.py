from django.db import migrations, models


CANONICAL_LAYOUT = (
    ("A", 1, "1", "A01"),
    ("A", 2, "2", "A02"),
    ("B", 1, "3", "B01"),
    ("B", 2, "4", "B02"),
    ("C", 1, "5", "C01"),
    ("C", 2, "6", "C02"),
)
LEGACY_DEFAULT_MACHINE_CODES = {
    f"{group}{position_no:02d}"
    for group in "ABC"
    for position_no in range(1, 7)
}


def _unused_station_code(ProductionStation, database_alias):
    for prefix in ("X", "Y", "Z", "L"):
        for number in range(1, 100):
            candidate = f"{prefix}{number:02d}"
            if not ProductionStation.objects.using(database_alias).filter(
                code=candidate
            ).exists():
                return candidate
    raise RuntimeError("无法为冲突的历史站位生成保留编号。")


def migrate_six_machine_layout(apps, schema_editor):
    Machine = apps.get_model("molds", "Machine")
    MoldAsset = apps.get_model("molds", "MoldAsset")
    MoldMovement = apps.get_model("molds", "MoldMovement")
    ProductionRun = apps.get_model("production", "ProductionRun")
    ProductionStation = apps.get_model("production", "ProductionStation")
    database_alias = schema_editor.connection.alias

    machines = Machine.objects.using(database_alias)
    stations = ProductionStation.objects.using(database_alias)
    runs = ProductionRun.objects.using(database_alias)
    molds = MoldAsset.objects.using(database_alias)
    movements = MoldMovement.objects.using(database_alias)

    canonical_station_ids = set()
    canonical_machine_ids = set()
    legacy_machine_ids = set()

    for group, position_no, code, legacy_code in CANONICAL_LAYOUT:
        station = stations.filter(group=group, position_no=position_no).first()
        if station is None:
            continue

        code_owner = stations.filter(code=code).exclude(pk=station.pk).first()
        if code_owner is not None:
            stations.filter(pk=code_owner.pk).update(
                code=_unused_station_code(ProductionStation, database_alias),
                is_active=False,
            )

        previous_machine_id = station.machine_id
        machine = machines.filter(code=code).first()
        if machine is None:
            legacy_machine = None
            if previous_machine_id:
                linked_machine = machines.filter(pk=previous_machine_id).first()
                if linked_machine is not None and linked_machine.code == legacy_code:
                    legacy_machine = linked_machine
            if legacy_machine is None:
                legacy_machine = machines.filter(code=legacy_code).first()
            if legacy_machine is not None:
                machines.filter(pk=legacy_machine.pk).update(
                    code=code,
                    name=f"{code}号机台",
                    is_active=True,
                )
                machine = machines.get(pk=legacy_machine.pk)
            else:
                machine = machines.create(
                    code=code,
                    name=f"{code}号机台",
                    is_active=True,
                )
        else:
            machines.filter(pk=machine.pk).update(is_active=True)

        stations.filter(machine_id=machine.pk).exclude(pk=station.pk).update(
            machine_id=None,
            is_active=False,
        )
        stations.filter(pk=station.pk).update(
            code=code,
            machine_id=machine.pk,
            is_active=True,
        )
        canonical_station_ids.add(station.pk)
        canonical_machine_ids.add(machine.pk)
        if previous_machine_id and previous_machine_id != machine.pk:
            legacy_machine_ids.add(previous_machine_id)

    obsolete_stations = stations.filter(
        group__in=("A", "B", "C"),
        position_no__gt=2,
    )
    for station in list(obsolete_stations):
        if runs.filter(station_id=station.pk).exists():
            stations.filter(pk=station.pk).update(is_active=False)
            if station.machine_id:
                machines.filter(pk=station.machine_id).update(is_active=False)
        else:
            if station.machine_id:
                legacy_machine_ids.add(station.machine_id)
            stations.filter(pk=station.pk).delete()

    legacy_machine_ids.update(
        machines.filter(code__in=LEGACY_DEFAULT_MACHINE_CODES).values_list(
            "pk", flat=True
        )
    )
    for machine_id in legacy_machine_ids - canonical_machine_ids:
        if not machines.filter(pk=machine_id).exists():
            continue
        referenced = (
            stations.filter(machine_id=machine_id).exists()
            or molds.filter(current_machine_id=machine_id).exists()
            or movements.filter(from_machine_id=machine_id).exists()
            or movements.filter(to_machine_id=machine_id).exists()
        )
        if referenced:
            machines.filter(pk=machine_id).update(is_active=False)
        else:
            machines.filter(pk=machine_id).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("production", "0003_order_settlement_and_operator_performance"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="productionstation",
            name="production_position_between_1_and_6",
        ),
        migrations.AlterField(
            model_name="productionstation",
            name="code",
            field=models.CharField(max_length=3, unique=True, verbose_name="机台编号"),
        ),
        migrations.AlterField(
            model_name="productionstation",
            name="group",
            field=models.CharField(
                choices=[("A", "一组"), ("B", "二组"), ("C", "三组")],
                max_length=1,
                verbose_name="机台组",
            ),
        ),
        migrations.RunPython(
            migrate_six_machine_layout,
            migrations.RunPython.noop,
        ),
        migrations.AddConstraint(
            model_name="productionstation",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        is_active=True,
                        position_no__gte=1,
                        position_no__lte=2,
                    )
                    | models.Q(
                        is_active=False,
                        position_no__gte=1,
                        position_no__lte=6,
                    )
                ),
                name="production_station_position_valid",
            ),
        ),
    ]
