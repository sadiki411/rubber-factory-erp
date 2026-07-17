from collections import Counter
from datetime import datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Q
from django.utils import timezone

from production.models import ProductionDailyLog, ProductionRun
from quality.models import QualityShipment, ReturnRework

from .models import ManualFinancialEntry, ManualPerformanceEntry


ZERO = Decimal("0")
MONEY = Decimal("0.01")
QUALITY_FIELDS = (
    "inspection_quantity",
    "qualified_quantity",
    "defective_quantity",
    "shipped_quantity",
    "returned_quantity",
    "reworked_quantity",
    "recovered_quantity",
    "scrap_quantity",
)
FINANCE_FIELDS = (
    "revenue",
    "material_cost",
    "labor_cost",
    "energy_cost",
    "other_cost",
    "total_cost",
    "profit",
)


def _decimal(value):
    return value if isinstance(value, Decimal) else Decimal(str(value or 0))


def _decimal_text(value):
    return format(_decimal(value).quantize(MONEY, rounding=ROUND_HALF_UP), "f")


def _rate(numerator, denominator):
    denominator = _decimal(denominator)
    if denominator == ZERO:
        return None
    return _decimal_text(_decimal(numerator) / denominator * Decimal("100"))


def _ratio(numerator, denominator):
    denominator = _decimal(denominator)
    if denominator == ZERO:
        return None
    return _decimal_text(_decimal(numerator) / denominator)


def _empty_quality():
    return {field: 0 for field in QUALITY_FIELDS}


def _empty_finance():
    return {field: ZERO for field in FINANCE_FIELDS}


def _add_quality(target, source):
    for field in QUALITY_FIELDS:
        target[field] += int(source.get(field, 0) or 0)


def _add_finance(target, source):
    for field in FINANCE_FIELDS:
        target[field] += _decimal(source.get(field, 0))


def _finance_values(*, revenue=0, material_cost=0, labor_cost=0, energy_cost=0, other_cost=0):
    material_cost = _decimal(material_cost)
    labor_cost = _decimal(labor_cost)
    energy_cost = _decimal(energy_cost)
    other_cost = _decimal(other_cost)
    total_cost = material_cost + labor_cost + energy_cost + other_cost
    revenue = _decimal(revenue)
    return {
        "revenue": revenue,
        "material_cost": material_cost,
        "labor_cost": labor_cost,
        "energy_cost": energy_cost,
        "other_cost": other_cost,
        "total_cost": total_cost,
        "profit": revenue - total_cost,
    }


def _manual_finance(entry):
    amount = _decimal(entry.amount)
    if entry.direction == ManualFinancialEntry.Direction.INCOME:
        return _finance_values(revenue=amount)
    costs = {
        "material_cost": ZERO,
        "labor_cost": ZERO,
        "energy_cost": ZERO,
        "other_cost": ZERO,
    }
    category_field = {
        ManualFinancialEntry.Category.MATERIAL: "material_cost",
        ManualFinancialEntry.Category.LABOR: "labor_cost",
        ManualFinancialEntry.Category.ENERGY: "energy_cost",
    }.get(entry.category, "other_cost")
    costs[category_field] = amount
    return _finance_values(**costs)


def _run_finance(run):
    return _finance_values(
        revenue=run.revenue,
        material_cost=_decimal(run.total_material_kg) * _decimal(run.material_unit_price),
        labor_cost=run.labor_cost,
        energy_cost=run.energy_cost,
        other_cost=run.other_cost,
    )


def _period_datetimes(date_from, date_to):
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(date_from, time.min), tz)
    end = timezone.make_aware(
        datetime.combine(date_to + timedelta(days=1), time.min), tz
    )
    return start, end


def _daily_row(day):
    return {
        "date": day.isoformat(),
        "automatic_produced_mold_count": 0,
        "manual_produced_mold_count": 0,
        "produced_mold_count": 0,
        "theoretical_output_quantity": 0,
        "automatic_equivalent_hours": ZERO,
        "manual_reported_hours": ZERO,
        **_empty_quality(),
        "automatic_revenue": ZERO,
        "manual_revenue": ZERO,
        "revenue": ZERO,
        "automatic_total_cost": ZERO,
        "manual_total_cost": ZERO,
        "total_cost": ZERO,
        "automatic_profit": ZERO,
        "manual_profit": ZERO,
        "profit": ZERO,
    }


def _order_row(
    order_no,
    *,
    row_key,
    order_id=None,
    link_type="LEGACY",
    product_name="",
    specification="",
    material="",
):
    return {
        "row_key": row_key,
        "order_id": order_id,
        "link_type": link_type,
        "order_no": order_no,
        "product_name": product_name or "",
        "specification": specification or "",
        "material": material or "",
        "automatic_produced_mold_count": 0,
        "manual_produced_mold_count": 0,
        "produced_mold_count": 0,
        "theoretical_output_quantity": 0,
        **_empty_quality(),
        **_empty_finance(),
        "automatic_record_count": 0,
        "manual_record_count": 0,
        "_run_ids": set(),
    }


def _order_key(order_no):
    return str(order_no or "").strip().upper()


def _order_row_key(order_id, order_no):
    if order_id:
        return f"order:{order_id}"
    normalized = _order_key(order_no)
    return f"legacy:{normalized}" if normalized else ""


def _ensure_order_row(
    orders,
    *,
    order_id=None,
    order_no="",
    product_name="",
    specification="",
    material="",
):
    row_key = _order_row_key(order_id, order_no)
    if not row_key:
        return None
    normalized_order_no = _order_key(order_no)
    row = orders.setdefault(
        row_key,
        _order_row(
            normalized_order_no,
            row_key=row_key,
            order_id=order_id,
            link_type="ORDER" if order_id else "LEGACY",
            product_name=product_name,
            specification=specification,
            material=material,
        ),
    )
    for field, value in (
        ("product_name", product_name),
        ("specification", specification),
        ("material", material),
    ):
        if not row[field] and value:
            row[field] = value
    return row


def _production_order_reference(run):
    fallback_product_name = (
        run.mold.mold_model.product_name if run.mold_id else ""
    )
    if run.order_id:
        return {
            "order_id": run.order_id,
            "order_no": run.order.order_no,
            "product_name": run.order.product_name or fallback_product_name,
            "specification": run.order.specification or run.specification,
            "material": run.order.material or run.material,
        }
    return {
        "order_id": None,
        "order_no": run.order_no,
        "product_name": fallback_product_name,
        "specification": run.specification,
        "material": run.material,
    }


def _employee_row(employee=None, staff_name=""):
    return {
        "employee_id": employee.pk if employee else None,
        "employee_no": employee.employee_no if employee else "",
        "name": employee.name if employee else staff_name,
        "team": employee.team if employee else "",
        "role": employee.role if employee else None,
        "inspection_quantity": 0,
        "qualified_quantity": 0,
        "defective_quantity": 0,
        "shipped_quantity": 0,
        "responsible_return_quantity": 0,
        "handled_returned_quantity": 0,
        "reworked_quantity": 0,
        "recovered_quantity": 0,
        "scrap_quantity": 0,
        "rework_hours": ZERO,
        "inspection_days": set(),
        "automatic_record_count": 0,
        "manual_record_count": 0,
    }


def _employee_key(employee=None, staff_name=""):
    if employee:
        return f"employee:{employee.pk}"
    return f"name:{' '.join(str(staff_name or '').split()).casefold()}"


def _operator_row(operator):
    return {
        "operator": operator,
        "automatic_mold_count": 0,
        "manual_mold_count": 0,
        "total_mold_count": 0,
        "theoretical_output_quantity": 0,
        "automatic_equivalent_hours": ZERO,
        "manual_reported_hours": ZERO,
        "production_days": set(),
        "run_ids": set(),
        "automatic_record_count": 0,
        "manual_record_count": 0,
    }


def _machine_row(machine=None, station=None):
    code = machine.code if machine else (station.code if station else "未关联机台")
    return {
        "machine_id": machine.pk if machine else None,
        "machine_code": code,
        "machine_name": machine.name if machine else "",
        "station_id": station.pk if station else None,
        "station_code": station.code if station else "",
        "group": station.group if station else None,
        "automatic_mold_count": 0,
        "manual_mold_count": 0,
        "total_mold_count": 0,
        "theoretical_output_quantity": 0,
        "automatic_equivalent_hours": ZERO,
        "automatic_actual_machine_hours": ZERO,
        "manual_reported_hours": ZERO,
        **_empty_finance(),
        "run_ids": set(),
        "automatic_record_count": 0,
        "manual_record_count": 0,
    }


def _machine_key(machine=None, station=None):
    if machine:
        return f"machine:{machine.pk}"
    if station:
        return f"station:{station.pk}"
    return "unassigned"


def _reason_row(category, display):
    return {
        "reason_category": category,
        "reason_category_display": display,
        "returned_quantity": 0,
        "reworked_quantity": 0,
        "recovered_quantity": 0,
        "scrap_quantity": 0,
        "rework_hours": ZERO,
        "automatic_record_count": 0,
        "manual_record_count": 0,
    }


def _record_source(automatic_count, manual_count):
    if automatic_count and manual_count:
        return "COMBINED"
    if manual_count:
        return "MANUAL"
    return "AUTOMATIC"


def _filter_production(queryset, *, group=None, machine_id=None, prefix=""):
    if machine_id:
        queryset = queryset.filter(**{f"{prefix}station__machine_id": machine_id})
    if group:
        queryset = queryset.filter(**{f"{prefix}station__group__iexact": group})
    return queryset


def _manual_queryset(date_from, date_to, *, group=None, machine_id=None):
    queryset = ManualPerformanceEntry.objects.filter(
        entry_date__gte=date_from,
        entry_date__lte=date_to,
        voided_at__isnull=True,
    ).select_related("machine", "quality_employee", "created_by", "voided_by")
    production_filter = Q(entry_type=ManualPerformanceEntry.EntryType.PRODUCTION)
    if machine_id:
        queryset = queryset.filter(
            ~production_filter | Q(machine_id=machine_id)
        )
    if group:
        queryset = queryset.filter(
            ~production_filter
            | Q(machine__production_station__group__iexact=group)
        )
    return queryset.order_by("entry_date", "id")


def _financial_queryset(date_from, date_to, *, group=None, machine_id=None):
    queryset = ManualFinancialEntry.objects.filter(
        occurred_on__gte=date_from,
        occurred_on__lte=date_to,
        voided_at__isnull=True,
    ).select_related("machine", "created_by", "voided_by")
    if machine_id:
        queryset = queryset.filter(machine_id=machine_id)
    if group:
        queryset = queryset.filter(
            machine__production_station__group__iexact=group
        )
    return queryset.order_by("occurred_on", "id")


def build_dashboard(*, date_from, date_to, month=None, group=None, machine_id=None):
    period_start, period_end = _period_datetimes(date_from, date_to)
    daily = {}
    cursor = date_from
    while cursor <= date_to:
        daily[cursor] = _daily_row(cursor)
        cursor += timedelta(days=1)

    logs_qs = ProductionDailyLog.objects.filter(
        production_date__gte=date_from,
        production_date__lte=date_to,
    ).select_related(
        "run__station__machine", "run__mold__mold_model", "run__order"
    )
    logs_qs = _filter_production(
        logs_qs, group=group, machine_id=machine_id, prefix="run__"
    )
    logs = list(logs_qs.order_by("production_date", "id"))

    shipments = list(
        QualityShipment.objects.filter(
            shipment_date__gte=date_from,
            shipment_date__lte=date_to,
        ).select_related("order", "inspector")
    )
    reworks = list(
        ReturnRework.objects.filter(
            rework_date__gte=date_from,
            rework_date__lte=date_to,
        ).select_related(
            "shipment__order", "responsible_inspector", "rework_employee"
        )
    )
    manual_entries = list(
        _manual_queryset(
            date_from, date_to, group=group, machine_id=machine_id
        )
    )
    financial_entries = list(
        _financial_queryset(
            date_from, date_to, group=group, machine_id=machine_id
        )
    )

    settled_qs = ProductionRun.objects.filter(
        settled_at__gte=period_start,
        settled_at__lt=period_end,
    ).select_related(
        "station__machine", "mold__mold_model", "order"
    ).prefetch_related(
        "daily_logs"
    )
    settled_qs = _filter_production(
        settled_qs, group=group, machine_id=machine_id
    )
    settled_runs = list(settled_qs)

    period_runs_qs = ProductionRun.objects.filter(
        (
            Q(loaded_at__isnull=False, loaded_at__lt=period_end)
            & (Q(unloaded_at__isnull=True) | Q(unloaded_at__gte=period_start))
        )
        | Q(
            status=ProductionRun.Status.PLANNED,
            loaded_at__isnull=True,
            created_at__gte=period_start,
            created_at__lt=period_end,
        )
    ).select_related("station__machine")
    period_runs_qs = _filter_production(
        period_runs_qs, group=group, machine_id=machine_id
    )
    period_runs = list(period_runs_qs.distinct())

    unsettled_completed_qs = ProductionRun.objects.filter(
        status=ProductionRun.Status.COMPLETED,
        unloaded_at__gte=period_start,
        unloaded_at__lt=period_end,
        settled_at__isnull=True,
    )
    unsettled_completed_qs = _filter_production(
        unsettled_completed_qs, group=group, machine_id=machine_id
    )
    unsettled_completed_run_count = unsettled_completed_qs.count()

    automatic_production = {
        "produced_mold_count": 0,
        "theoretical_output_quantity": 0,
        "equivalent_hours": ZERO,
        "actual_machine_hours": ZERO,
    }
    manual_production = {
        "produced_mold_count": 0,
        "reported_hours": ZERO,
    }
    automatic_finance = _empty_finance()
    manual_finance = _empty_finance()
    automatic_quality = _empty_quality()
    manual_quality = _empty_quality()
    operators = {}
    machines = {}
    employees = {}
    reasons = {}
    orders = {}
    run_ids = {run.pk for run in period_runs}
    production_days = set()

    now = timezone.now()
    for run in period_runs:
        machine = run.station.machine
        machine_key = _machine_key(machine, run.station)
        machine_row = machines.setdefault(
            machine_key, _machine_row(machine, run.station)
        )
        machine_row["run_ids"].add(run.pk)
        if not run.loaded_at:
            continue
        actual_start = max(run.loaded_at, period_start)
        actual_end = min(run.unloaded_at or now, period_end, now)
        actual_seconds = max((actual_end - actual_start).total_seconds(), 0)
        actual_hours = _decimal(actual_seconds) / Decimal("3600")
        automatic_production["actual_machine_hours"] += actual_hours
        machine_row["automatic_actual_machine_hours"] += actual_hours

    for log in logs:
        run = log.run
        molds = int(log.produced_mold_count or 0)
        output = molds * int(run.cavities or 0)
        equivalent_hours = _decimal(molds * int(log.curing_seconds_snapshot or 0)) / Decimal("3600")
        automatic_production["produced_mold_count"] += molds
        automatic_production["theoretical_output_quantity"] += output
        automatic_production["equivalent_hours"] += equivalent_hours
        production_days.add(log.production_date)
        run_ids.add(run.pk)

        day = daily[log.production_date]
        day["automatic_produced_mold_count"] += molds
        day["produced_mold_count"] += molds
        day["theoretical_output_quantity"] += output
        day["automatic_equivalent_hours"] += equivalent_hours

        operator_name = " ".join(str(log.operator or "未指定").split()) or "未指定"
        operator = operators.setdefault(operator_name.casefold(), _operator_row(operator_name))
        operator["automatic_mold_count"] += molds
        operator["total_mold_count"] += molds
        operator["theoretical_output_quantity"] += output
        operator["automatic_equivalent_hours"] += equivalent_hours
        operator["production_days"].add(log.production_date)
        operator["run_ids"].add(run.pk)
        operator["automatic_record_count"] += 1

        machine = run.station.machine
        machine_key = _machine_key(machine, run.station)
        machine_row = machines.setdefault(machine_key, _machine_row(machine, run.station))
        machine_row["automatic_mold_count"] += molds
        machine_row["total_mold_count"] += molds
        machine_row["theoretical_output_quantity"] += output
        machine_row["automatic_equivalent_hours"] += equivalent_hours
        machine_row["run_ids"].add(run.pk)
        machine_row["automatic_record_count"] += 1

        row = _ensure_order_row(
            orders,
            **_production_order_reference(run),
        )
        if row is not None:
            row["automatic_produced_mold_count"] += molds
            row["produced_mold_count"] += molds
            row["theoretical_output_quantity"] += output
            row["automatic_record_count"] += 1
            row["_run_ids"].add(run.pk)

    for run in settled_runs:
        finance = _run_finance(run)
        _add_finance(automatic_finance, finance)
        settled_day = timezone.localtime(run.settled_at).date()
        if settled_day in daily:
            day = daily[settled_day]
            day["automatic_revenue"] += finance["revenue"]
            day["revenue"] += finance["revenue"]
            day["automatic_total_cost"] += finance["total_cost"]
            day["total_cost"] += finance["total_cost"]
            day["automatic_profit"] += finance["profit"]
            day["profit"] += finance["profit"]

        machine = run.station.machine
        machine_key = _machine_key(machine, run.station)
        machine_row = machines.setdefault(machine_key, _machine_row(machine, run.station))
        _add_finance(machine_row, finance)

        row = _ensure_order_row(
            orders,
            **_production_order_reference(run),
        )
        if row is not None:
            _add_finance(row, finance)
            row["_run_ids"].add(run.pk)

    for shipment in shipments:
        values = {
            "inspection_quantity": shipment.inspection_quantity,
            "qualified_quantity": shipment.qualified_quantity,
            "defective_quantity": shipment.defective_quantity,
            "shipped_quantity": shipment.shipped_quantity,
        }
        _add_quality(automatic_quality, values)
        _add_quality(daily[shipment.shipment_date], values)

        employee_key = _employee_key(shipment.inspector)
        employee = employees.setdefault(
            employee_key, _employee_row(shipment.inspector)
        )
        for field in (
            "inspection_quantity",
            "qualified_quantity",
            "defective_quantity",
            "shipped_quantity",
        ):
            employee[field] += int(values[field] or 0)
        employee["inspection_days"].add(shipment.shipment_date)
        employee["automatic_record_count"] += 1

        order = shipment.order
        row = _ensure_order_row(
            orders,
            order_id=order.pk,
            order_no=order.order_no,
            product_name=order.product_name,
            specification=order.specification,
            material=order.material,
        )
        _add_quality(row, values)
        row["automatic_record_count"] += 1

    reason_labels = dict(ReturnRework.ReasonCategory.choices)
    for rework in reworks:
        values = {
            "returned_quantity": rework.returned_quantity,
            "reworked_quantity": rework.reworked_quantity,
            "recovered_quantity": rework.recovered_quantity,
            "scrap_quantity": rework.scrap_quantity,
        }
        _add_quality(automatic_quality, values)
        _add_quality(daily[rework.rework_date], values)

        responsible_key = _employee_key(rework.responsible_inspector)
        responsible = employees.setdefault(
            responsible_key, _employee_row(rework.responsible_inspector)
        )
        responsible["responsible_return_quantity"] += int(rework.returned_quantity or 0)
        responsible["automatic_record_count"] += 1

        reworker_key = _employee_key(rework.rework_employee)
        reworker = employees.setdefault(
            reworker_key, _employee_row(rework.rework_employee)
        )
        reworker["handled_returned_quantity"] += int(rework.returned_quantity or 0)
        reworker["reworked_quantity"] += int(rework.reworked_quantity or 0)
        reworker["recovered_quantity"] += int(rework.recovered_quantity or 0)
        reworker["scrap_quantity"] += int(rework.scrap_quantity or 0)
        reworker["rework_hours"] += _decimal(rework.work_hours)
        reworker["automatic_record_count"] += 1

        category = rework.reason_category
        reason = reasons.setdefault(
            category,
            _reason_row(category, reason_labels.get(category, category)),
        )
        for field in (
            "returned_quantity",
            "reworked_quantity",
            "recovered_quantity",
            "scrap_quantity",
        ):
            reason[field] += int(values[field] or 0)
        reason["rework_hours"] += _decimal(rework.work_hours)
        reason["automatic_record_count"] += 1

        order = rework.shipment.order
        row = _ensure_order_row(
            orders,
            order_id=order.pk,
            order_no=order.order_no,
            product_name=order.product_name,
            specification=order.specification,
            material=order.material,
        )
        _add_quality(row, values)
        row["automatic_record_count"] += 1

    manual_counts = Counter(entry.entry_type for entry in manual_entries)
    for entry in manual_entries:
        day = daily[entry.entry_date]
        order_row = _ensure_order_row(orders, order_no=entry.order_no)
        if entry.entry_type == ManualPerformanceEntry.EntryType.PRODUCTION:
            molds = int(entry.produced_mold_count or 0)
            reported_hours = _decimal(entry.production_hours)
            manual_production["produced_mold_count"] += molds
            manual_production["reported_hours"] += reported_hours

            day["manual_produced_mold_count"] += molds
            day["produced_mold_count"] += molds
            day["manual_reported_hours"] += reported_hours
            if molds or reported_hours:
                production_days.add(entry.entry_date)
                operator_key = entry.staff_name.casefold()
                operator = operators.setdefault(
                    operator_key, _operator_row(entry.staff_name)
                )
                operator["manual_mold_count"] += molds
                operator["total_mold_count"] += molds
                operator["manual_reported_hours"] += reported_hours
                operator["production_days"].add(entry.entry_date)
                operator["manual_record_count"] += 1

            machine_key = _machine_key(entry.machine)
            machine_row = machines.setdefault(
                machine_key, _machine_row(entry.machine)
            )
            machine_row["manual_mold_count"] += molds
            machine_row["total_mold_count"] += molds
            machine_row["manual_reported_hours"] += reported_hours
            machine_row["manual_record_count"] += 1

            if order_row is not None:
                order_row["manual_produced_mold_count"] += molds
                order_row["produced_mold_count"] += molds
                order_row["manual_record_count"] += 1
        elif entry.entry_type == ManualPerformanceEntry.EntryType.QUALITY:
            values = {field: getattr(entry, field) for field in QUALITY_FIELDS}
            _add_quality(manual_quality, values)
            _add_quality(day, values)
            employee_key = _employee_key(entry.quality_employee, entry.staff_name)
            employee = employees.setdefault(
                employee_key,
                _employee_row(entry.quality_employee, entry.staff_name),
            )
            for field in (
                "inspection_quantity",
                "qualified_quantity",
                "defective_quantity",
                "shipped_quantity",
            ):
                employee[field] += int(getattr(entry, field) or 0)
            employee["inspection_days"].add(entry.entry_date)
            employee["manual_record_count"] += 1
            if order_row is not None:
                _add_quality(order_row, values)
                order_row["manual_record_count"] += 1
        else:
            values = {field: getattr(entry, field) for field in QUALITY_FIELDS}
            _add_quality(manual_quality, values)
            _add_quality(day, values)
            employee_key = _employee_key(entry.quality_employee, entry.staff_name)
            employee = employees.setdefault(
                employee_key,
                _employee_row(entry.quality_employee, entry.staff_name),
            )
            employee["handled_returned_quantity"] += int(entry.returned_quantity or 0)
            employee["reworked_quantity"] += int(entry.reworked_quantity or 0)
            employee["recovered_quantity"] += int(entry.recovered_quantity or 0)
            employee["scrap_quantity"] += int(entry.scrap_quantity or 0)
            employee["rework_hours"] += _decimal(entry.rework_hours)
            employee["manual_record_count"] += 1

            category = entry.reason_category or ReturnRework.ReasonCategory.OTHER
            reason = reasons.setdefault(
                category,
                _reason_row(category, reason_labels.get(category, category)),
            )
            for field in (
                "returned_quantity",
                "reworked_quantity",
                "recovered_quantity",
                "scrap_quantity",
            ):
                reason[field] += int(getattr(entry, field) or 0)
            reason["rework_hours"] += _decimal(entry.rework_hours)
            reason["manual_record_count"] += 1
            if order_row is not None:
                _add_quality(order_row, values)
                order_row["manual_record_count"] += 1

    for entry in financial_entries:
        finance = _manual_finance(entry)
        _add_finance(manual_finance, finance)
        day = daily[entry.occurred_on]
        day["manual_revenue"] += finance["revenue"]
        day["revenue"] += finance["revenue"]
        day["manual_total_cost"] += finance["total_cost"]
        day["total_cost"] += finance["total_cost"]
        day["manual_profit"] += finance["profit"]
        day["profit"] += finance["profit"]
        if entry.machine_id:
            machine_key = _machine_key(entry.machine)
            machine_row = machines.setdefault(
                machine_key, _machine_row(entry.machine)
            )
            _add_finance(machine_row, finance)
            machine_row["manual_record_count"] += 1
        order_row = _ensure_order_row(orders, order_no=entry.order_no)
        if order_row is not None:
            _add_finance(order_row, finance)
            order_row["manual_record_count"] += 1

    combined_quality = _empty_quality()
    _add_quality(combined_quality, automatic_quality)
    _add_quality(combined_quality, manual_quality)
    combined_finance = _empty_finance()
    _add_finance(combined_finance, automatic_finance)
    _add_finance(combined_finance, manual_finance)

    run_status_counts = Counter(
        ProductionRun.objects.filter(pk__in=run_ids).values_list("status", flat=True)
    )

    operator_payload = []
    for row in operators.values():
        row["production_days"] = len(row["production_days"])
        row["participated_run_count"] = len(row.pop("run_ids"))
        row["automatic_equivalent_hours"] = _decimal_text(row["automatic_equivalent_hours"])
        row["manual_reported_hours"] = _decimal_text(row["manual_reported_hours"])
        row["automatic_molds_per_equivalent_hour"] = _ratio(
            row["automatic_mold_count"], row["automatic_equivalent_hours"]
        )
        row["manual_molds_per_reported_hour"] = _ratio(
            row["manual_mold_count"], row["manual_reported_hours"]
        )
        row["average_daily_mold_count"] = _ratio(
            row["total_mold_count"], row["production_days"]
        )
        row["source"] = _record_source(
            row["automatic_record_count"], row["manual_record_count"]
        )
        operator_payload.append(row)
    operator_payload.sort(key=lambda item: (-item["total_mold_count"], item["operator"]))

    machine_payload = []
    for row in machines.values():
        row["run_count"] = len(row.pop("run_ids"))
        automatic_hours = row["automatic_equivalent_hours"]
        actual_hours = row["automatic_actual_machine_hours"]
        manual_hours = row["manual_reported_hours"]
        row["automatic_molds_per_equivalent_hour"] = _ratio(
            row["automatic_mold_count"], automatic_hours
        )
        row["manual_molds_per_reported_hour"] = _ratio(
            row["manual_mold_count"], manual_hours
        )
        row["automatic_efficiency_percent"] = _rate(
            automatic_hours, actual_hours
        )
        row["automatic_equivalent_hours"] = _decimal_text(automatic_hours)
        row["automatic_actual_machine_hours"] = _decimal_text(actual_hours)
        row["manual_reported_hours"] = _decimal_text(manual_hours)
        for field in FINANCE_FIELDS:
            row[field] = _decimal_text(row[field])
        row["profit_margin"] = _rate(row["profit"], row["revenue"])
        row["source"] = _record_source(
            row["automatic_record_count"], row["manual_record_count"]
        )
        machine_payload.append(row)
    machine_payload.sort(key=lambda item: (item["machine_code"], item["station_code"]))

    employee_payload = []
    for row in employees.values():
        row["inspection_days"] = len(row["inspection_days"])
        row["rework_hours"] = _decimal_text(row["rework_hours"])
        row["first_pass_rate"] = _rate(
            row["qualified_quantity"], row["inspection_quantity"]
        )
        row["return_rate"] = _rate(
            row["responsible_return_quantity"], row["shipped_quantity"]
        )
        row["rework_pass_rate"] = _rate(
            row["recovered_quantity"], row["reworked_quantity"]
        )
        row["source"] = _record_source(
            row["automatic_record_count"], row["manual_record_count"]
        )
        employee_payload.append(row)
    employee_payload.sort(
        key=lambda item: (
            -(item["inspection_quantity"] + item["reworked_quantity"]),
            item["employee_no"],
            item["name"],
        )
    )

    reason_payload = []
    for row in reasons.values():
        row["rework_hours"] = _decimal_text(row["rework_hours"])
        row["share_of_returns"] = _rate(
            row["returned_quantity"], combined_quality["returned_quantity"]
        )
        row["rework_pass_rate"] = _rate(
            row["recovered_quantity"], row["reworked_quantity"]
        )
        row["source"] = _record_source(
            row["automatic_record_count"], row["manual_record_count"]
        )
        reason_payload.append(row)
    reason_payload.sort(key=lambda item: -item["returned_quantity"])

    order_payload = []
    for row in orders.values():
        row["production_run_count"] = len(row.pop("_run_ids"))
        for field in FINANCE_FIELDS:
            row[field] = _decimal_text(row[field])
        row["first_pass_rate"] = _rate(
            row["qualified_quantity"], row["inspection_quantity"]
        )
        row["return_rate"] = _rate(
            row["returned_quantity"], row["shipped_quantity"]
        )
        row["rework_pass_rate"] = _rate(
            row["recovered_quantity"], row["reworked_quantity"]
        )
        row["profit_margin"] = _rate(row["profit"], row["revenue"])
        row["source"] = _record_source(
            row["automatic_record_count"], row["manual_record_count"]
        )
        order_payload.append(row)
    order_payload.sort(key=lambda item: (item["order_no"], item["row_key"]))

    daily_payload = []
    for row in daily.values():
        for field in (
            "automatic_equivalent_hours",
            "manual_reported_hours",
            "automatic_revenue",
            "manual_revenue",
            "revenue",
            "automatic_total_cost",
            "manual_total_cost",
            "total_cost",
            "automatic_profit",
            "manual_profit",
            "profit",
        ):
            row[field] = _decimal_text(row[field])
        row["first_pass_rate"] = _rate(
            row["qualified_quantity"], row["inspection_quantity"]
        )
        row["return_rate"] = _rate(
            row["returned_quantity"], row["shipped_quantity"]
        )
        row["profit_margin"] = _rate(row["profit"], row["revenue"])
        daily_payload.append(row)

    def production_source(source, source_name):
        payload = {
            "produced_mold_count": source["produced_mold_count"],
            "theoretical_output_quantity": source.get("theoretical_output_quantity", 0),
            "automatic_equivalent_hours": _decimal_text(
                source.get("equivalent_hours", 0)
            ),
            "automatic_actual_machine_hours": _decimal_text(
                source.get("actual_machine_hours", 0)
            ),
            "manual_reported_hours": _decimal_text(source.get("reported_hours", 0)),
        }
        if source_name == "automatic":
            payload["molds_per_equivalent_hour"] = _ratio(
                source["produced_mold_count"], source.get("equivalent_hours", 0)
            )
            payload["efficiency_percent"] = _rate(
                source.get("equivalent_hours", 0),
                source.get("actual_machine_hours", 0),
            )
        elif source_name == "manual":
            payload["molds_per_reported_hour"] = _ratio(
                source["produced_mold_count"], source.get("reported_hours", 0)
            )
        else:
            payload["molds_per_equivalent_hour"] = None
            payload["molds_per_reported_hour"] = None
            payload["efficiency_percent"] = None
        return payload

    combined_production = {
        "produced_mold_count": automatic_production["produced_mold_count"]
        + manual_production["produced_mold_count"],
        "theoretical_output_quantity": automatic_production["theoretical_output_quantity"],
        "equivalent_hours": automatic_production["equivalent_hours"],
        "actual_machine_hours": automatic_production["actual_machine_hours"],
        "reported_hours": manual_production["reported_hours"],
    }

    def finance_source(source):
        payload = {field: _decimal_text(source[field]) for field in FINANCE_FIELDS}
        payload["profit_margin"] = _rate(source["profit"], source["revenue"])
        return payload

    def quality_source(source):
        payload = dict(source)
        payload["first_pass_rate"] = _rate(
            payload["qualified_quantity"], payload["inspection_quantity"]
        )
        payload["return_rate"] = _rate(
            payload["returned_quantity"], payload["shipped_quantity"]
        )
        payload["rework_pass_rate"] = _rate(
            payload["recovered_quantity"], payload["reworked_quantity"]
        )
        return payload

    data_basis = {
        "production_quantity_date": "ProductionDailyLog.production_date",
        "automatic_production_hours": "生产日报模数×硫化时间快照的折算工时",
        "automatic_actual_machine_hours": "ProductionRun上模/停机区间与筛选日期的重叠机时",
        "manual_production_hours": "ManualPerformanceEntry.production_hours手工填报实际工时",
        "automatic_finance_date": "ProductionRun.settled_at",
        "manual_finance_date": "ManualFinancialEntry.occurred_on",
        "quality_date": "QualityShipment.shipment_date",
        "rework_date": "ReturnRework.rework_date",
        "order_link": (
            "已关联生产、品检和出货记录按order_id归集；"
            "未关联历史及手工补录按legacy:规范化订单号单独归集"
        ),
        "quality_filter_scope": "品检与返工数据仅按日期筛选；无机台关联，machine/group筛选不作用于品质数据",
        "zero_denominator_rate": None,
    }

    return {
        "period": {
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "month": month,
            "group": group,
            "machine_id": machine_id,
        },
        "data_basis": data_basis,
        "sources": {
            "production": {
                "automatic": len(logs),
                "manual": manual_counts[ManualPerformanceEntry.EntryType.PRODUCTION],
                "total": len(logs)
                + manual_counts[ManualPerformanceEntry.EntryType.PRODUCTION],
                "automatic_settled_runs": len(settled_runs),
            },
            "quality": {
                "automatic": len(shipments),
                "manual": manual_counts[ManualPerformanceEntry.EntryType.QUALITY],
                "total": len(shipments)
                + manual_counts[ManualPerformanceEntry.EntryType.QUALITY],
            },
            "rework": {
                "automatic": len(reworks),
                "manual": manual_counts[ManualPerformanceEntry.EntryType.REWORK],
                "total": len(reworks)
                + manual_counts[ManualPerformanceEntry.EntryType.REWORK],
            },
            "finance": {
                "automatic": len(settled_runs),
                "manual": len(financial_entries),
                "total": len(settled_runs) + len(financial_entries),
            },
        },
        "production": {
            "automatic": production_source(automatic_production, "automatic"),
            "manual": production_source(manual_production, "manual"),
            "total": production_source(combined_production, "total"),
            "production_days": len(production_days),
            "operator_count": len(operator_payload),
            "run_count": len(period_runs),
            "settled_run_count": len(settled_runs),
            "unsettled_completed_run_count": unsettled_completed_run_count,
            "status_counts": {
                value: run_status_counts.get(value, 0)
                for value in ProductionRun.Status.values
            },
            "settled_good_quantity": sum(
                int(run.actual_good_quantity or 0) for run in settled_runs
            ),
            "settled_defective_quantity": sum(
                int(run.actual_defective_quantity or 0) for run in settled_runs
            ),
            "settled_defect_rate": _rate(
                sum(int(run.actual_defective_quantity or 0) for run in settled_runs),
                sum(
                    int(run.actual_good_quantity or 0)
                    + int(run.actual_defective_quantity or 0)
                    for run in settled_runs
                ),
            ),
        },
        "finance": {
            "automatic": finance_source(automatic_finance),
            "manual": finance_source(manual_finance),
            "total": finance_source(combined_finance),
        },
        "quality": {
            "automatic": quality_source(automatic_quality),
            "manual": quality_source(manual_quality),
            "total": quality_source(combined_quality),
            "shipment_count": len(shipments),
            "rework_count": len(reworks),
        },
        "daily_trend": daily_payload,
        "operator_performance": operator_payload,
        "station_performance": machine_payload,
        "quality_employee_performance": employee_payload,
        "defect_reason_breakdown": reason_payload,
        "order_performance": order_payload,
        "manual_entries": manual_entries,
        "manual_financial_entries": financial_entries,
    }
