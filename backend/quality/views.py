from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.db.models import Count, IntegerField, Prefetch, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.dateparse import parse_date
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from orders.services import with_order_activity

from .models import (QualityEmployee, QualityOrder, QualityShipment, ReturnRework,
                     ProductUnitWeight, ProcessCard, QualityShipmentBatch,
                     QualityShipmentLine, QualityReworkCase, QualityReworkAttempt)
from .serializers import (
    QualityEmployeeSerializer,
    QualityOrderSerializer,
    QualityShipmentSerializer,
    ReturnReworkSerializer,
    ProductUnitWeightSerializer, ProcessCardSerializer, QualityShipmentBatchSerializer,
    QualityShipmentLineSerializer, QualityReworkCaseSerializer, QualityReworkAttemptSerializer,
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


class WorkflowModelViewSet(viewsets.ModelViewSet):
    pagination_class = QualityPagination
    http_method_names = ["get", "post", "put", "patch", "head", "options"]

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def destroy(self, request, *args, **kwargs):
        # Workflow rows are audit records.  Even draft rows are retained so a
        # retry or an operator correction cannot silently erase history.
        raise DRFValidationError({"detail": "品检出货流程记录不支持删除，请使用作废操作。"})


class ProductUnitWeightViewSet(WorkflowModelViewSet):
    serializer_class = ProductUnitWeightSerializer
    queryset = ProductUnitWeight.objects.select_related("product_specification", "mold_model", "created_by").all()

    def get_queryset(self):
        queryset = self.queryset
        q = str(self.request.query_params.get("q", "")).strip()
        if q:
            queryset = queryset.filter(
                Q(product_specification__product_name__icontains=q)
                | Q(product_specification__specification__icontains=q)
                | Q(product_specification__customer_product_no__icontains=q)
                | Q(mold_model__code__icontains=q)
            )
        active = str(self.request.query_params.get("active", "")).lower()
        if active in {"1", "true", "yes"}:
            queryset = queryset.filter(is_active=True)
        elif active in {"0", "false", "no"}:
            queryset = queryset.filter(is_active=False)
        return queryset


class ProcessCardViewSet(WorkflowModelViewSet):
    serializer_class = ProcessCardSerializer

    def get_queryset(self):
        queryset = ProcessCard.objects.select_related("order", "product_specification", "unit_weight_config", "created_by").all()
        q = str(self.request.query_params.get("q", "")).strip()
        if q:
            queryset = queryset.filter(
                Q(card_no__icontains=q)
                | Q(source_order_no__icontains=q)
                | Q(source_item_no__icontains=q)
                | Q(product_name_snapshot__icontains=q)
                | Q(product_code_snapshot__icontains=q)
                | Q(specification_snapshot__icontains=q)
                | Q(material_snapshot__icontains=q)
            )
        status = str(self.request.query_params.get("status", "")).strip().upper()
        if status:
            if status not in ProcessCard.Status.values:
                raise DRFValidationError({"status": "无效的流程卡状态。"})
            queryset = queryset.filter(status=status)
        date_from, date_to = _date_range(self.request.query_params)
        if date_from:
            queryset = queryset.filter(demand_date__gte=date_from)
        if date_to:
            queryset = queryset.filter(demand_date__lte=date_to)
        return queryset

    @action(detail=True, methods=["get"])
    def timeline(self, request, pk=None):
        card = self.get_object()
        batches = QualityShipmentBatch.objects.filter(lines__process_card=card).distinct().order_by("shipment_date", "id")
        cases = QualityReworkCase.objects.filter(process_card=card).order_by("opened_on", "id")
        return Response({"card_id": card.pk, "events": [
            *({"type": "shipment", "id": b.pk, "shipment_no": b.shipment_no, "date": b.shipment_date, "status": b.status, "net_weight_kg": str(b.net_weight_kg)} for b in batches),
            *({"type": "rework", "id": c.pk, "case_no": c.case_no, "date": c.opened_on, "status": c.status, "origin": c.origin} for c in cases),
        ]})

    @action(detail=True, methods=["get"], url_path="rework-timeline")
    def rework_timeline(self, request, pk=None):
        card = self.get_object()
        cases = QualityReworkCase.objects.filter(process_card=card).prefetch_related("attempts").order_by("opened_on", "id")
        return Response(QualityReworkCaseSerializer(cases, many=True, context={"request": request}).data)


class QualityShipmentLineViewSet(WorkflowModelViewSet):
    serializer_class = QualityShipmentLineSerializer

    def get_queryset(self):
        return QualityShipmentLine.objects.select_related("batch", "process_card", "process_card__order").all()

    def perform_create(self, serializer):
        batch = serializer.validated_data.get("batch")
        if batch is None:
            raise DRFValidationError({"batch_id": "创建出货明细必须指定草稿批次。"})
        if batch.status != QualityShipmentBatch.Status.DRAFT:
            raise DRFValidationError({"batch": "只有草稿批次可以新增明细。"})
        serializer.save()

    def perform_update(self, serializer):
        if serializer.instance.batch.status != QualityShipmentBatch.Status.DRAFT:
            raise DRFValidationError({"batch": "已确认或已作废的出货明细不能修改。"})
        target_batch = serializer.validated_data.get("batch", serializer.instance.batch)
        if target_batch.status != QualityShipmentBatch.Status.DRAFT:
            raise DRFValidationError({"batch": "出货明细只能保存在草稿批次中。"})
        serializer.save()


class QualityShipmentBatchViewSet(WorkflowModelViewSet):
    serializer_class = QualityShipmentBatchSerializer

    def get_queryset(self):
        queryset = QualityShipmentBatch.objects.select_related("inspector", "created_by").prefetch_related("lines__process_card").all()
        q = str(self.request.query_params.get("q", "")).strip()
        if q:
            queryset = queryset.filter(
                Q(shipment_no__icontains=q)
                | Q(client_key__icontains=q)
                | Q(customer__icontains=q)
                | Q(lines__process_card__card_no__icontains=q)
            ).distinct()
        date_from, date_to = _date_range(self.request.query_params)
        if date_from:
            queryset = queryset.filter(Q(shipment_date__gte=date_from) | Q(shipment_date__isnull=True))
        if date_to:
            queryset = queryset.filter(Q(shipment_date__lte=date_to) | Q(shipment_date__isnull=True))
        status = str(self.request.query_params.get("status", "")).strip().upper()
        if status:
            if status not in QualityShipmentBatch.Status.values:
                raise DRFValidationError({"status": "无效的出货批次状态。"})
            queryset = queryset.filter(status=status)
        return queryset

    @action(detail=True, methods=["post"])
    def confirm(self, request, pk=None):
        with transaction.atomic():
            batch = QualityShipmentBatch.objects.select_for_update().get(pk=pk)
            if batch.status == QualityShipmentBatch.Status.VOID:
                raise DRFValidationError({"status": "A void shipment batch cannot be confirmed."})
            if batch.status == QualityShipmentBatch.Status.CONFIRMED:
                return Response(self.get_serializer(batch).data)
            # Confirmation is idempotent.  Mobile clients may retry after a
            # network timeout; a previously confirmed batch must not be
            # counted against itself a second time.
            if batch.status == QualityShipmentBatch.Status.CONFIRMED:
                return Response(self.get_serializer(batch).data)
            lines = list(batch.lines.select_related("process_card"))
            if not lines:
                raise DRFValidationError({"lines": "At least one shipment line is required."})
            if batch.shipment_date is None:
                raise DRFValidationError({"shipment_date": "请先填写实际出货日期后再确认。"})
            # Lock cards and check existing confirmed deliveries plus all lines
            # in this batch atomically.
            by_card = {}
            by_pieces = {}
            for line in lines:
                by_card.setdefault(line.process_card_id, Decimal("0"))
                by_card[line.process_card_id] += Decimal(line.net_weight_kg)
                if line.piece_quantity is not None:
                    by_pieces.setdefault(line.process_card_id, 0)
                    by_pieces[line.process_card_id] += line.piece_quantity
            for card_id, incoming in by_card.items():
                card = ProcessCard.objects.select_for_update().get(pk=card_id)
                if card.unit_weight_g is None or card.unit_weight_g <= 0:
                    raise DRFValidationError({"lines": f"流程卡 {card.card_no} 尚未填写成品单重，不能确认出货。"})
                if card.delivered_net_weight_kg + incoming > card.max_allowed_weight_kg:
                    raise DRFValidationError({"lines": f"流程卡 {card.card_no} 累计出货净重不能超过理论重量的110%。"})
                if card_id in by_pieces:
                    existing_pieces = QualityShipmentLine.objects.filter(process_card_id=card_id, batch__status=QualityShipmentBatch.Status.CONFIRMED).exclude(batch_id=batch.pk).aggregate(total=Sum("piece_quantity"))["total"] or 0
                    if existing_pieces - card.returned_piece_quantity + by_pieces[card_id] > card.quantity:
                        raise DRFValidationError({"lines": f"流程卡 {card.card_no} 累计出货件数不能超过卡上数量。"})
            batch.status = QualityShipmentBatch.Status.CONFIRMED
            batch.save()
            for card_id in by_card:
                ProcessCard.objects.get(pk=card_id).refresh_shipping_status()
        return Response(self.get_serializer(batch).data)

    @action(detail=True, methods=["post"])
    def void(self, request, pk=None):
        with transaction.atomic():
            batch = QualityShipmentBatch.objects.select_for_update().get(pk=pk)
            if batch.status == QualityShipmentBatch.Status.CONFIRMED:
                raise DRFValidationError({"status": "A confirmed shipment batch cannot be voided."})
            if batch.status == QualityShipmentBatch.Status.VOID:
                return Response(self.get_serializer(batch).data)
            batch.status = QualityShipmentBatch.Status.VOID
            batch.save()
        return Response(self.get_serializer(batch).data)


class QualityReworkCaseViewSet(WorkflowModelViewSet):
    serializer_class = QualityReworkCaseSerializer
    queryset = QualityReworkCase.objects.select_related("process_card", "shipment_line", "responsible_inspector", "created_by").prefetch_related("attempts").all()

    def get_queryset(self):
        queryset = self.queryset
        q = str(self.request.query_params.get("q", "")).strip()
        if q:
            queryset = queryset.filter(
                Q(case_no__icontains=q)
                | Q(reason__icontains=q)
                | Q(process_card__card_no__icontains=q)
            )
        origin = str(self.request.query_params.get("origin", "")).strip().upper()
        if origin:
            if origin not in QualityReworkCase.Origin.values:
                raise DRFValidationError({"origin": "无效的返工来源。"})
            queryset = queryset.filter(origin=origin)
        status = str(self.request.query_params.get("status", "")).strip().upper()
        if status:
            if status not in QualityReworkCase.Status.values:
                raise DRFValidationError({"status": "无效的返工状态。"})
            queryset = queryset.filter(status=status)
        return queryset


class QualityReworkAttemptViewSet(WorkflowModelViewSet):
    serializer_class = QualityReworkAttemptSerializer
    queryset = QualityReworkAttempt.objects.select_related("case", "rework_employee", "created_by").all()

    def get_queryset(self):
        queryset = self.queryset
        case_id = self.request.query_params.get("case") or self.request.query_params.get("case_id")
        if case_id:
            queryset = queryset.filter(case_id=case_id)
        return queryset


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
        queryset = with_order_activity(
            QualityOrder.objects.select_related(
                "created_by",
                "product_specification",
                "product_specification__mold_model",
            )
        )
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
            "order__product_specification__mold_model",
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
