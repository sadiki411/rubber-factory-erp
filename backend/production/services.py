from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from molds.models import Machine

from .models import (
    PRODUCTION_STATION_LAYOUT,
    ProductionRun,
    ProductionSettlementRevision,
    ProductionStation,
)


LEGACY_DEFAULT_MACHINE_CODES = {
    f"{group}{position_no:02d}"
    for group in ProductionStation.Group.values
    for position_no in range(1, 7)
}


def _next_settlement_revision(run):
    current = run.settlement_revisions.aggregate(value=Max("revision_no"))["value"]
    return (current or 0) + 1


def record_settlement_revision(run, user, action):
    return ProductionSettlementRevision.objects.create(
        run=run,
        revision_no=_next_settlement_revision(run),
        action=action,
        cavities=run.cavities,
        produced_mold_count=run.produced_mold_count,
        unit_price=run.unit_price,
        material_unit_price=run.material_unit_price,
        actual_good_quantity=run.actual_good_quantity,
        actual_defective_quantity=run.actual_defective_quantity,
        total_material_kg=run.total_material_kg,
        labor_cost=run.labor_cost,
        energy_cost=run.energy_cost,
        other_cost=run.other_cost,
        settlement_notes=run.settlement_notes,
        changed_by=user,
    )


def invalidate_settlement(run, user):
    """Preserve an audit snapshot and reopen accounting after mold totals change."""

    if not run.settled_at:
        return False
    record_settlement_revision(
        run, user, ProductionSettlementRevision.Action.INVALIDATED
    )
    ProductionRun.objects.filter(pk=run.pk, settled_at__isnull=False).update(
        settled_at=None,
        settled_by=None,
        updated_at=timezone.now(),
    )
    run.settled_at = None
    run.settled_by = None
    return True


@transaction.atomic
def seed_default_stations():
    """Ensure the physical 1-6 machine layout and retire obsolete defaults safely."""

    def retire_default_machine(machine_id):
        if not machine_id:
            return
        machine = Machine.objects.filter(pk=machine_id).first()
        if machine is None or machine.code not in LEGACY_DEFAULT_MACHINE_CODES:
            return
        referenced = (
            ProductionStation.objects.filter(machine_id=machine.pk).exists()
            or machine.current_molds.exists()
            or machine.movements_from.exists()
            or machine.movements_to.exists()
        )
        if referenced:
            if machine.is_active:
                Machine.objects.filter(pk=machine.pk).update(is_active=False)
        else:
            machine.delete()

    legacy_machine_ids = []
    obsolete_stations = ProductionStation.objects.filter(
        group__in=ProductionStation.Group.values,
        position_no__gt=2,
    ).select_related("machine")
    for station in obsolete_stations:
        if station.runs.exists():
            if station.is_active:
                ProductionStation.objects.filter(pk=station.pk).update(is_active=False)
            if station.machine_id:
                Machine.objects.filter(pk=station.machine_id).update(is_active=False)
        else:
            legacy_machine_ids.append(station.machine_id)
            station.delete()

    stations = []
    for group, position_no, code, legacy_code in PRODUCTION_STATION_LAYOUT:
        station = ProductionStation.objects.filter(
            group=group, position_no=position_no
        ).first()
        previous_machine_id = station.machine_id if station else None

        machine = Machine.objects.filter(code=code).first()
        if machine is None:
            legacy_machine = Machine.objects.filter(code=legacy_code).first()
            if (
                station
                and station.machine_id
                and station.machine.code == legacy_code
            ):
                legacy_machine = station.machine
            if legacy_machine:
                legacy_machine.code = code
                legacy_machine.name = f"{code}号机台"
                legacy_machine.is_active = True
                legacy_machine.save(update_fields=["code", "name", "is_active", "updated_at"])
                machine = legacy_machine
            else:
                machine = Machine.objects.create(
                    code=code, name=f"{code}号机台", is_active=True
                )
        elif not machine.is_active:
            Machine.objects.filter(pk=machine.pk).update(is_active=True)
            machine.is_active = True

        if station is None:
            station = ProductionStation.objects.create(
                group=group,
                position_no=position_no,
                code=code,
                machine=machine,
                is_active=True,
            )
        else:
            ProductionStation.objects.filter(machine_id=machine.pk).exclude(
                pk=station.pk
            ).update(machine=None, is_active=False)
            station.code = code
            station.machine = machine
            station.is_active = True
            station.save(
                update_fields=["code", "machine", "is_active", "updated_at"]
            )
        stations.append(station)

        if previous_machine_id and previous_machine_id != machine.pk:
            legacy_machine_ids.append(previous_machine_id)

    for machine_id in legacy_machine_ids:
        retire_default_machine(machine_id)
    for machine in list(Machine.objects.filter(code__in=LEGACY_DEFAULT_MACHINE_CODES)):
        retire_default_machine(machine.pk)
    return stations
