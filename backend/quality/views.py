from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Count, IntegerField, Prefetch, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.dateparse import parse_date
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import viewsets
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import QualityEmployee, QualityOrder, QualityShipment, ReturnRework
from .serializers import (
    QualityEmployeeSerializer,
    QualityOrderSerializer,
    QualityShipmentSerializer,
    ReturnReworkSerializer,
)


class QualityPagination(PageNumberPagination):
    page_size = 30
    page_size_query_param = "page_size"
    max_page_size = 1000


def _parsed_date(value, field):
    parsed = parse_date(value) if value else None
    if value and parsed is None:
        raise DRFValidationError({field: "日期格式应为yyyy-mm-dd。"})
    return parsed


def _date_range(params, *, default_month=False):
    raw_from = str(params.get("date_from", "")).strip()
    raw_to = str(params.get("date_to", "")).strip()
    parsed_from = _parsed_date(raw_from, "date_from")
    parsed_to = _parsed_date(raw_to, "date_to")
    if default_month:
        today = timezone.localdate()
        parsed_from = parsed_from or today.replace(day=1)
        parsed_to = parsed_to or today
    if parsed_from and parsed_to and parsed_from > parsed_to:
        raise DRFValidationError({"date_to": "结束日期不能早于开始日期。"})
    return parsed_from, parsed_to


def _filter_employee(queryset, value, fields):
    value = str(value or "").strip()
    if not value:
        return queryset
    employee_filter = Q()
    for field in fields:
        if value.isdigit():
            employee_filter |= Q(**{f"{field}_id": int(value)})
        employee_filter |= Q(**{f"{field}__employee_no__iexact": value})
        employee_filter |= Q(**{f"{field}__name__icontains": value})
    return queryset.filter(employee_filter)


def _filter_order(queryset, value, field="order"):
    value = str(value or "").strip()
    if not value:
        return queryset
    order_filter = Q()
    if value.isdigit():
        order_filter |= Q(**{f"{field}_id": int(value)})
    order_filter |= Q(**{f"{field}__order_no__iexact": value})
    order_filter |= Q(**{f"{field}__batch_no__iexact": value})
    return queryset.filter(order_filter)


class NoDeleteModelViewSet(viewsets.ModelViewSet):
    pagination_class = QualityPagination
    http_method_names = ["get", "post", "put", "patch", "head", "options"]


class QualityEmployeeViewSet(NoDeleteModelViewSet):
    serializer_class = QualityEmployeeSerializer

    def get_queryset(self):
        queryset = QualityEmployee.objects.all()
        q = str(self.request.query_params.get("q", "")).strip()
        if q:
            queryset = queryset.filter(
                Q(employee_no__icontains=q)
                | Q(name__icontains=q)
                | Q(team__icontains=q)
            )
        role = str(self.request.query_params.get("role", "")).strip().upper()
        if role:
            if role not in QualityEmployee.Role.values:
                raise DRFValidationError({"role": "无效的员工岗位。"})
            queryset = queryset.filter(role=role)
        active = str(
            self.request.query_params.get(
                "active", self.request.query_params.get("is_active", "")
            )
        ).strip().lower()
        if active in {"1", "true", "yes"}:
            queryset = queryset.filter(is_active=True)
        elif active in {"0", "false", "no"}:
            queryset = queryset.filter(is_active=False)
        return queryset.order_by("employee_no")


class QualityOrderViewSet(NoDeleteModelViewSet):
    serializer_class = QualityOrderSerializer

    def get_queryset(self):
        queryset = QualityOrder.objects.select_related("created_by", "product_specification")
        params = self.request.query_params
        q = str(params.get("q", "")).strip()
        if q:
            queryset = queryset.filter(
                Q(order_no__icontains=q)
                | Q(item_no__icontains=q)
                | Q(batch_no__icontains=q)
                | Q(product_code__icontains=q)
                | Q(product_name__icontains=q)
                | Q(specification__icontains=q)
                | Q(material__icontains=q)
                | Q(product_specification__customer_product_no__icontains=q)
            )
        status_value = str(params.get("status", "")).strip().upper()
        if status_value:
            if status_value not in QualityOrder.Status.values:
                raise DRFValidationError({"status": "无效的订单状态。"})
            queryset = queryset.filter(status=status_value)
        date_from, date_to = _date_range(params)
        if date_from:
            queryset = queryset.filter(order_date__gte=date_from)
        if date_to:
            queryset = queryset.filter(order_date__lte=date_to)
        return queryset.order_by("-order_date", "-id")

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


def _shipment_queryset():
    return (
        QualityShipment.objects.select_related(
            "order__created_by",
            "order__product_specification",
            "inspector",
            "created_by",
        )
        .annotate(
            rework_count_value=Count("reworks", distinct=True),
            returned_quantity_value=Coalesce(
                Sum("reworks__returned_quantity"), Value(0), output_field=IntegerField()
            ),
        )
    )


class QualityShipmentViewSet(NoDeleteModelViewSet):
    serializer_class = QualityShipmentSerializer

    def get_queryset(self):
        queryset = _shipment_queryset()
        params = self.request.query_params
        q = str(params.get("q", "")).strip()
        if q:
            queryset = queryset.filter(
                Q(shipment_no__icontains=q)
                | Q(order__order_no__icontains=q)
                | Q(order__batch_no__icontains=q)
                | Q(order__product_code__icontains=q)
                | Q(order__product_name__icontains=q)
                | Q(inspector__employee_no__icontains=q)
                | Q(inspector__name__icontains=q)
            )
        date_from, date_to = _date_range(params)
        if date_from:
            queryset = queryset.filter(shipment_date__gte=date_from)
        if date_to:
            queryset = queryset.filter(shipment_date__lte=date_to)
        status_value = str(params.get("status", "")).strip().upper()
        if status_value:
            if status_value not in QualityOrder.Status.values:
                raise DRFValidationError({"status": "无效的订单状态。"})
            queryset = queryset.filter(order__status=status_value)
        queryset = _filter_employee(queryset, params.get("employee"), ["inspector"])
        queryset = _filter_employee(queryset, params.get("inspector"), ["inspector"])
        queryset = _filter_order(queryset, params.get("order"))
        return queryset.order_by("-shipment_date", "-id")

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


class ReturnReworkViewSet(NoDeleteModelViewSet):
    serializer_class = ReturnReworkSerializer

    def get_queryset(self):
        queryset = ReturnRework.objects.select_related(
            "responsible_inspector",
            "rework_employee",
            "created_by",
        ).prefetch_related(Prefetch("shipment", queryset=_shipment_queryset()))
        params = self.request.query_params
        q = str(params.get("q", "")).strip()
        if q:
            queryset = queryset.filter(
                Q(shipment__shipment_no__icontains=q)
                | Q(shipment__order__order_no__icontains=q)
                | Q(shipment__order__batch_no__icontains=q)
                | Q(shipment__order__product_code__icontains=q)
                | Q(shipment__order__product_name__icontains=q)
                | Q(reason__icontains=q)
                | Q(responsible_inspector__name__icontains=q)
                | Q(rework_employee__name__icontains=q)
            )
        status_value = str(params.get("status", "")).strip().upper()
        if status_value:
            if status_value not in ReturnRework.Status.values:
                raise DRFValidationError({"status": "无效的返工状态。"})
            queryset = queryset.filter(status=status_value)
        reason_category = str(params.get("reason_category", "")).strip().upper()
        if reason_category:
            if reason_category not in ReturnRework.ReasonCategory.values:
                raise DRFValidationError({"reason_category": "无效的退货原因分类。"})
            queryset = queryset.filter(reason_category=reason_category)
        date_from, date_to = _date_range(params)
        if date_from:
            queryset = queryset.filter(rework_date__gte=date_from)
        if date_to:
            queryset = queryset.filter(rework_date__lte=date_to)
        queryset = _filter_employee(
            queryset,
            params.get("employee"),
            ["responsible_inspector", "rework_employee"],
        )
        queryset = _filter_employee(
            queryset, params.get("responsible_inspector"), ["responsible_inspector"]
        )
        queryset = _filter_employee(
            queryset, params.get("rework_employee"), ["rework_employee"]
        )
        queryset = _filter_order(queryset, params.get("order"), "shipment__order")
        return queryset.order_by("-rework_date", "-id")

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


def _integer(value):
    return int(value or 0)


def _rate(numerator, denominator):
    if not denominator:
        return "0.00"
    value = Decimal(numerator or 0) / Decimal(denominator) * Decimal("100")
    return format(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f")


def _empty_quantities():
    return {
        "inspection_quantity": 0,
        "qualified_quantity": 0,
        "defective_quantity": 0,
        "shipped_quantity": 0,
        "returned_quantity": 0,
        "reworked_quantity": 0,
        "recovered_quantity": 0,
        "scrap_quantity": 0,
    }


class QualitySummaryView(APIView):
    @extend_schema(
        responses=dict,
        parameters=[
            OpenApiParameter("date_from", str, description="开始日期，格式YYYY-MM-DD；默认本月1日"),
            OpenApiParameter("date_to", str, description="结束日期，格式YYYY-MM-DD；默认今天"),
        ],
    )
    def get(self, request):
        date_from, date_to = _date_range(request.query_params, default_month=True)
        shipments = QualityShipment.objects.filter(
            shipment_date__gte=date_from, shipment_date__lte=date_to
        )
        reworks = ReturnRework.objects.filter(
            rework_date__gte=date_from, rework_date__lte=date_to
        )

        shipment_totals = shipments.aggregate(
            inspection_quantity=Sum("inspection_quantity"),
            qualified_quantity=Sum("qualified_quantity"),
            defective_quantity=Sum("defective_quantity"),
            shipped_quantity=Sum("shipped_quantity"),
            shipment_count=Count("id"),
        )
        rework_totals = reworks.aggregate(
            returned_quantity=Sum("returned_quantity"),
            reworked_quantity=Sum("reworked_quantity"),
            recovered_quantity=Sum("recovered_quantity"),
            scrap_quantity=Sum("scrap_quantity"),
        )
        totals = _empty_quantities()
        for key in (
            "inspection_quantity",
            "qualified_quantity",
            "defective_quantity",
            "shipped_quantity",
        ):
            totals[key] = _integer(shipment_totals[key])
        for key in (
            "returned_quantity",
            "reworked_quantity",
            "recovered_quantity",
            "scrap_quantity",
        ):
            totals[key] = _integer(rework_totals[key])
        order_ids = set(shipments.values_list("order_id", flat=True)) | set(
            reworks.values_list("shipment__order_id", flat=True)
        )
        totals.update(
            {
                "shipment_count": _integer(shipment_totals["shipment_count"]),
                "order_count": len(order_ids),
                "first_pass_rate": _rate(
                    totals["qualified_quantity"], totals["inspection_quantity"]
                ),
                "return_rate": _rate(
                    totals["returned_quantity"], totals["shipped_quantity"]
                ),
                "rework_pass_rate": _rate(
                    totals["recovered_quantity"], totals["reworked_quantity"]
                ),
            }
        )

        daily = {}
        cursor = date_from
        while cursor <= date_to:
            daily[cursor] = {"date": cursor.isoformat(), **_empty_quantities()}
            cursor += timedelta(days=1)
        for item in shipments.values("shipment_date").annotate(
            inspection_quantity=Sum("inspection_quantity"),
            qualified_quantity=Sum("qualified_quantity"),
            defective_quantity=Sum("defective_quantity"),
            shipped_quantity=Sum("shipped_quantity"),
        ):
            row = daily[item["shipment_date"]]
            for key in (
                "inspection_quantity",
                "qualified_quantity",
                "defective_quantity",
                "shipped_quantity",
            ):
                row[key] = _integer(item[key])
        for item in reworks.values("rework_date").annotate(
            returned_quantity=Sum("returned_quantity"),
            reworked_quantity=Sum("reworked_quantity"),
            recovered_quantity=Sum("recovered_quantity"),
            scrap_quantity=Sum("scrap_quantity"),
        ):
            row = daily[item["rework_date"]]
            for key in (
                "returned_quantity",
                "reworked_quantity",
                "recovered_quantity",
                "scrap_quantity",
            ):
                row[key] = _integer(item[key])

        order_quantities = {}
        for item in shipments.values("order_id").annotate(
            inspection_quantity=Sum("inspection_quantity"),
            qualified_quantity=Sum("qualified_quantity"),
            defective_quantity=Sum("defective_quantity"),
            shipped_quantity=Sum("shipped_quantity"),
            shipment_count=Count("id"),
        ):
            order_quantities[item["order_id"]] = {
                **_empty_quantities(),
                "shipment_count": _integer(item["shipment_count"]),
                "rework_count": 0,
                **{
                    key: _integer(item[key])
                    for key in (
                        "inspection_quantity",
                        "qualified_quantity",
                        "defective_quantity",
                        "shipped_quantity",
                    )
                },
            }
        for item in reworks.values("shipment__order_id").annotate(
            returned_quantity=Sum("returned_quantity"),
            reworked_quantity=Sum("reworked_quantity"),
            recovered_quantity=Sum("recovered_quantity"),
            scrap_quantity=Sum("scrap_quantity"),
            rework_count=Count("id"),
        ):
            order_id = item["shipment__order_id"]
            row = order_quantities.setdefault(
                order_id,
                {**_empty_quantities(), "shipment_count": 0, "rework_count": 0},
            )
            for key in (
                "returned_quantity",
                "reworked_quantity",
                "recovered_quantity",
                "scrap_quantity",
                "rework_count",
            ):
                row[key] = _integer(item[key])

        orders = {
            item.pk: item
            for item in QualityOrder.objects.filter(pk__in=order_quantities).order_by(
                "order_no", "batch_no", "id"
            )
        }
        order_stats = []
        for order_id, order in orders.items():
            row = order_quantities[order_id]
            order_stats.append(
                {
                    "order_id": order.pk,
                    "order_no": order.order_no,
                    "batch_no": order.batch_no,
                    "product_code": order.product_code,
                    "product_name": order.product_name,
                    "specification": order.specification,
                    "material": order.material,
                    **row,
                    "first_pass_rate": _rate(
                        row["qualified_quantity"], row["inspection_quantity"]
                    ),
                    "return_rate": _rate(
                        row["returned_quantity"], row["shipped_quantity"]
                    ),
                    "rework_pass_rate": _rate(
                        row["recovered_quantity"], row["reworked_quantity"]
                    ),
                }
            )

        employee_quantities = {}
        for item in shipments.values("inspector_id").annotate(
            inspection_quantity=Sum("inspection_quantity"),
            qualified_quantity=Sum("qualified_quantity"),
            defective_quantity=Sum("defective_quantity"),
            shipped_quantity=Sum("shipped_quantity"),
            inspection_days=Count("shipment_date", distinct=True),
            shipment_count=Count("id"),
        ):
            employee_quantities[item["inspector_id"]] = {
                "inspection_quantity": _integer(item["inspection_quantity"]),
                "qualified_quantity": _integer(item["qualified_quantity"]),
                "defective_quantity": _integer(item["defective_quantity"]),
                "shipped_quantity": _integer(item["shipped_quantity"]),
                "inspection_days": _integer(item["inspection_days"]),
                "shipment_count": _integer(item["shipment_count"]),
                "responsible_return_quantity": 0,
                "reworked_quantity": 0,
                "recovered_quantity": 0,
                "scrap_quantity": 0,
            }
        for item in reworks.values("responsible_inspector_id").annotate(
            responsible_return_quantity=Sum("returned_quantity")
        ):
            row = employee_quantities.setdefault(
                item["responsible_inspector_id"],
                {
                    "inspection_quantity": 0,
                    "qualified_quantity": 0,
                    "defective_quantity": 0,
                    "shipped_quantity": 0,
                    "inspection_days": 0,
                    "shipment_count": 0,
                    "responsible_return_quantity": 0,
                    "reworked_quantity": 0,
                    "recovered_quantity": 0,
                    "scrap_quantity": 0,
                },
            )
            row["responsible_return_quantity"] = _integer(
                item["responsible_return_quantity"]
            )
        for item in reworks.values("rework_employee_id").annotate(
            reworked_quantity=Sum("reworked_quantity"),
            recovered_quantity=Sum("recovered_quantity"),
            scrap_quantity=Sum("scrap_quantity"),
        ):
            row = employee_quantities.setdefault(
                item["rework_employee_id"],
                {
                    "inspection_quantity": 0,
                    "qualified_quantity": 0,
                    "defective_quantity": 0,
                    "shipped_quantity": 0,
                    "inspection_days": 0,
                    "shipment_count": 0,
                    "responsible_return_quantity": 0,
                    "reworked_quantity": 0,
                    "recovered_quantity": 0,
                    "scrap_quantity": 0,
                },
            )
            for key in ("reworked_quantity", "recovered_quantity", "scrap_quantity"):
                row[key] = _integer(item[key])

        employees = {
            item.pk: item
            for item in QualityEmployee.objects.filter(pk__in=employee_quantities).order_by(
                "employee_no"
            )
        }
        employee_stats = []
        for employee_id, employee in employees.items():
            row = employee_quantities[employee_id]
            employee_stats.append(
                {
                    "employee_id": employee.pk,
                    "employee_no": employee.employee_no,
                    "name": employee.name,
                    "team": employee.team,
                    "role": employee.role,
                    **row,
                    "first_pass_rate": _rate(
                        row["qualified_quantity"], row["inspection_quantity"]
                    ),
                    "return_rate": _rate(
                        row["responsible_return_quantity"], row["shipped_quantity"]
                    ),
                    "rework_pass_rate": _rate(
                        row["recovered_quantity"], row["reworked_quantity"]
                    ),
                }
            )

        return Response(
            {
                "period": {
                    "date_from": date_from.isoformat(),
                    "date_to": date_to.isoformat(),
                },
                "totals": totals,
                "daily_trend": list(daily.values()),
                "order_stats": order_stats,
                "employee_stats": employee_stats,
            }
        )
