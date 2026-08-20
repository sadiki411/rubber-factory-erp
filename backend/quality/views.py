from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.db import IntegrityError, transaction
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
from orders.models import ProductSpecification

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


class QualityShippingCandidatesView(APIView):
    """Return exact-match orders that still have quantity available to ship.

    The endpoint deliberately uses equality predicates for specification,
    material, and order number.  A fuzzy match can silently associate a
    shipment with the wrong customer specification, so callers receive the
    complete exact candidate set and choose the row explicitly.
    """

    @extend_schema(responses=dict)
    def get(self, request):
        params = request.query_params
        queryset = QualityOrder.objects.select_related(
            "product_specification", "created_by"
        ).filter(status=QualityOrder.Status.OPEN)
        specification = str(params.get("specification", "") or "").strip()
        material = str(params.get("material", "") or "").strip()
        order_no = str(params.get("order_no", "") or "").strip()
        if specification:
            queryset = queryset.filter(specification__iexact=specification)
        if material:
            queryset = queryset.filter(material__iexact=material)
        if order_no:
            queryset = queryset.filter(order_no__iexact=order_no)
        q = str(params.get("q", "") or "").strip()
        if q:
            queryset = queryset.filter(
                Q(order_no__icontains=q)
                | Q(item_no__icontains=q)
                | Q(product_name__icontains=q)
                | Q(specification__icontains=q)
                | Q(material__icontains=q)
            )
        # An order is a candidate only while at least one process card or the
        # order itself has remaining pieces.  Legacy quantity shipments are
        # included in the same remainder to keep old and new entry forms in
        # agreement.
        order_ids = list(queryset.values_list("id", flat=True))
        # Process-card lines may also carry ``order_id`` in the newer schema.
        # Aggregate the union in Python so a row is counted exactly once, yet
        # old rows with only ``process_card_id`` still resolve to their order.
        shipped_by_order = {}
        weighted_lines = QualityShipmentLine.objects.filter(
            batch__status=QualityShipmentBatch.Status.CONFIRMED,
        ).filter(
            Q(order_id__in=order_ids) | Q(process_card__order_id__in=order_ids)
        ).select_related("process_card")
        for line in weighted_lines:
            order_id = line.order_id or (
                line.process_card.order_id if line.process_card_id else None
            )
            if order_id is None:
                continue
            quantity = line.piece_quantity
            if quantity is None and line.unit_weight_g_snapshot and line.net_weight_kg:
                quantity = int(
                    (Decimal(line.net_weight_kg) * Decimal("1000") / Decimal(line.unit_weight_g_snapshot))
                    .quantize(Decimal("1"), rounding=ROUND_HALF_UP)
                )
            returned = line.returned_piece_quantity
            delivered = max(0, int(quantity or 0) - int(returned or 0))
            shipped_by_order[order_id] = shipped_by_order.get(order_id, 0) + delivered
        legacy_totals = {}
        for shipment in QualityShipment.objects.filter(order_id__in=order_ids).prefetch_related("reworks"):
            delivered = max(0, int(shipment.shipped_quantity or 0) - int(shipment.returned_quantity or 0))
            legacy_totals[shipment.order_id] = legacy_totals.get(shipment.order_id, 0) + delivered
        rows = []
        for order in queryset.order_by("due_date", "order_date", "id"):
            shipped = int(shipped_by_order.get(order.pk, 0) or 0) + int(legacy_totals.get(order.pk, 0) or 0)
            remaining = max(0, int(order.order_quantity or 0) - shipped)
            if remaining <= 0:
                continue
            card = (
                ProcessCard.objects.filter(order_id=order.pk)
                .exclude(status=ProcessCard.Status.CANCELLED)
                .exclude(unit_weight_g__isnull=True)
                .order_by("-received_on", "-id")
                .first()
            )
            unit = card.unit_weight_g if card else None
            if unit is None and order.product_specification_id:
                unit = ProductUnitWeight.objects.filter(
                    product_specification_id=order.product_specification_id,
                    is_active=True,
                ).order_by("-measured_on", "-id").values_list("unit_weight_g", flat=True).first()
            rows.append(
                {
                    "id": order.pk,
                    "order_id": order.pk,
                    "order": QualityOrderSerializer(
                        order, context={"request": request}
                    ).data,
                    "order_no": order.order_no,
                    "item_no": order.item_no,
                    "batch_no": order.batch_no,
                    "product_code": order.product_code,
                    "product_name": order.product_name,
                    "specification": order.specification,
                    "material": order.material,
                    "remaining_quantity": remaining,
                    "remaining_weight_kg": (
                        (Decimal(remaining) * Decimal(unit) / Decimal("1000")).quantize(Decimal("0.001"))
                        if unit else None
                    ),
                    "unit_weight_g": unit,
                    "product_specification_id": order.product_specification_id,
                    "is_candidate": True,
                }
            )
        page_size = params.get("page_size")
        if page_size is not None or params.get("page") is not None:
            paginator = QualityPagination()
            page = paginator.paginate_queryset(rows, request, view=self)
            return paginator.get_paginated_response(page)
        return Response(rows)


class QualityShipmentBatchViewSet(WorkflowModelViewSet):
    serializer_class = QualityShipmentBatchSerializer

    def create(self, request, *args, **kwargs):
        """Create or resume a weighted shipment draft.

        The serializer safely reuses a draft with the same human-facing
        shipment number.  Return HTTP 200 for that resume path so clients can
        distinguish it from a newly-created draft (201) without guessing from
        the returned id.  Client-key idempotent retries retain the historical
        201 response for backward compatibility.
        """
        shipment_no = str(request.data.get("shipment_no") or request.data.get("batch_no") or "").strip()
        existing = None
        if shipment_no:
            existing = QualityShipmentBatch.objects.filter(
                shipment_no__iexact=shipment_no
            ).first()
        response = super().create(request, *args, **kwargs)
        if existing is not None and response.status_code == 201:
            response.status_code = 200
        return response

    def get_queryset(self):
        queryset = QualityShipmentBatch.objects.select_related(
            "inspector", "created_by", "order", "product_specification"
        ).prefetch_related("inspectors", "lines__process_card", "lines__order", "lines__product_specification").all()
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
            lines = list(batch.lines.select_related("process_card", "order", "product_specification"))
            if not lines:
                raise DRFValidationError({"lines": "At least one shipment line is required."})
            if batch.shipment_date is None:
                raise DRFValidationError({"shipment_date": "请先填写实际出货日期后再确认。"})
            inspectors = list(batch.inspectors.all())
            if not inspectors and batch.inspector_id:
                inspectors = [batch.inspector]
            invalid_inspectors = [
                employee.employee_no
                for employee in inspectors
                if not employee.is_active
                or employee.role
                not in (QualityEmployee.Role.INSPECTOR, QualityEmployee.Role.BOTH)
            ]
            if invalid_inspectors:
                raise DRFValidationError({"inspector_ids": "存在停用或岗位不符合的品检员：" + ", ".join(invalid_inspectors)})
            # Lock cards and check existing confirmed deliveries plus all lines
            # in this batch atomically.
            by_card = {}
            by_order = {}
            by_pieces = {}
            for line in lines:
                # Re-derive all historical values inside the confirmation
                # transaction.  Draft edits may have been made after the line
                # was first saved, and this is the point at which the immutable
                # unit/spec/material snapshot is committed.
                card = line.process_card
                order = line.order or (card.order if card else None)
                if not card and not order:
                    raise DRFValidationError({"lines": "每条出货明细必须关联流程卡或订单。"})
                unit = (
                    line.unit_weight_g_snapshot
                    or (card.unit_weight_g if card else None)
                    or batch.unit_weight_g
                )
                if unit:
                    self._normalize_repeat_line(line, unit)
                if card:
                    by_card.setdefault(card.pk, Decimal("0"))
                    by_card[card.pk] += Decimal(line.net_weight_kg)
                else:
                    by_order.setdefault(order.pk, Decimal("0"))
                    by_order[order.pk] += Decimal(line.net_weight_kg)
                if line.piece_quantity is not None:
                    key = ("card", card.pk) if card else ("order", order.pk)
                    by_pieces.setdefault(key, 0)
                    by_pieces[key] += line.piece_quantity
            for card_id, incoming in by_card.items():
                card = ProcessCard.objects.select_for_update().get(pk=card_id)
                card_lines = [line for line in lines if line.process_card_id == card_id]
                # A manually entered line unit weight is a historical measured
                # value.  Fall back to the process-card snapshot only when the
                # line did not provide one.
                for line in card_lines:
                    if line.unit_weight_g_snapshot is None:
                        line.unit_weight_g_snapshot = card.unit_weight_g
                    if line.unit_weight_g_snapshot is None or line.unit_weight_g_snapshot <= 0:
                        raise DRFValidationError({"lines": f"流程卡 {card.card_no} 的成品单重必须大于 0。"})
                    self._normalize_repeat_line(line, line.unit_weight_g_snapshot)
                    quantity = int(line.piece_quantity or 0)
                    theoretical_quantity = int(
                        (
                            line.process_card_shipment_quantity
                            * int(line.product_batch_count or 1)
                            if line.process_card_shipment_quantity is not None
                            else quantity
                        )
                    )
                    line.theoretical_weight_kg_snapshot = (
                        Decimal(theoretical_quantity) * Decimal(line.unit_weight_g_snapshot) / Decimal("1000")
                    ).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
                    line.max_allowed_weight_kg_snapshot = (
                        line.theoretical_weight_kg_snapshot * Decimal("1.10")
                    ).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
                    self._save_line_history(
                        line, card=card, order=card.order, created_by=request.user,
                        measured_on=batch.shipment_date,
                    )
                # When the card was created before a unit weight was known,
                # the confirmed line measurement becomes its frozen shipping
                # snapshot.  This also keeps card status/remaining-weight
                # calculations meaningful after confirmation.
                card_unit = card.unit_weight_g or card_lines[0].unit_weight_g_snapshot
                if card.unit_weight_g is None and card_unit:
                    ProcessCard.objects.filter(pk=card.pk).update(
                        unit_weight_g=card_unit,
                        updated_at=timezone.now(),
                    )
                    card.unit_weight_g = card_unit
                # Legacy card rows have one fixed card total, so retain their
                # cumulative cap.  New repeated-weighing rows carry an
                # explicit per-batch standard and were already checked line by
                # line above; comparing their expanded multi-batch total to a
                # single old card quantity would incorrectly reject valid
                # entries (for example 105 pieces x 3 batches).
                legacy_card_lines = [
                    line for line in card_lines
                    if line.single_batch_net_weight_kg is None
                ]
                if legacy_card_lines:
                    legacy_incoming = sum(
                        (Decimal(line.net_weight_kg) for line in legacy_card_lines),
                        Decimal("0"),
                    )
                    max_card_weight = (
                        Decimal(card.quantity) * Decimal(card_unit) / Decimal("1000") * Decimal("1.10")
                    ).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
                    if card.delivered_net_weight_kg + legacy_incoming > max_card_weight:
                        raise DRFValidationError({"lines": f"流程卡 {card.card_no} 累计出货净重不能超过理论重量的110%。"})
                if ("card", card_id) in by_pieces and legacy_card_lines:
                    existing_pieces = QualityShipmentLine.objects.filter(process_card_id=card_id, batch__status=QualityShipmentBatch.Status.CONFIRMED).exclude(batch_id=batch.pk).aggregate(total=Sum("piece_quantity"))["total"] or 0
                    maximum_card_pieces = Decimal(card.quantity) * Decimal("1.10")
                    legacy_incoming_pieces = sum(
                        int(line.piece_quantity or 0) for line in legacy_card_lines
                    )
                    if (
                        existing_pieces
                        - card.returned_piece_quantity
                        + legacy_incoming_pieces
                        > maximum_card_pieces
                    ):
                        raise DRFValidationError({"lines": f"流程卡 {card.card_no} 累计出货件数不能超过卡上数量的110%。"})
            # Resolve direct-order lines first.  Their cap is checked below
            # against *all* confirmed weighted lines for the order (including
            # process-card lines), not just other direct-order rows.
            locked_orders = {}
            for order_id in sorted(by_order):
                order = QualityOrder.objects.select_for_update().get(pk=order_id)
                locked_orders[order_id] = order
                order_lines = [
                    line for line in lines
                    if not line.process_card_id and line.order_id == order_id
                ]
                for line in order_lines:
                    unit = line.unit_weight_g_snapshot or batch.unit_weight_g
                    if unit is None or unit <= 0:
                        raise DRFValidationError({"lines": "自由出货明细必须填写大于 0 的成品单重。"})
                    line.unit_weight_g_snapshot = unit
                    self._normalize_repeat_line(line, unit)
                    quantity = int(line.piece_quantity or 0)
                    theoretical_quantity = int(
                        (
                            line.process_card_shipment_quantity
                            * int(line.product_batch_count or 1)
                            if line.process_card_shipment_quantity is not None
                            else quantity
                        )
                    )
                    line.theoretical_weight_kg_snapshot = (
                        Decimal(theoretical_quantity) * Decimal(unit) / Decimal("1000")
                    ).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
                    line.max_allowed_weight_kg_snapshot = (
                        line.theoretical_weight_kg_snapshot * Decimal("1.10")
                    ).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
                    self._save_line_history(
                        line, card=None, order=order, created_by=request.user,
                        measured_on=batch.shipment_date,
                    )

            # An order can be shipped through several process cards and/or
            # direct lines.  Enforce one cumulative quantity and weight cap so
            # a direct entry cannot bypass quantities already shipped from a
            # process-card entry.  The OR query returns each line once even
            # when the newer schema also stores ``line.order_id``.
            # Process-card workflows have their own per-card allowance and
            # historically permit several cards for the same order (for
            # example, duplicate physical cards covering separate runs).
            # The order-level cap below is specifically for the new
            # free-form order workflow; when a batch contains a direct line,
            # include prior card deliveries in its allowance so that direct
            # entry cannot bypass an already shipped order.
            order_ids = sorted({
                line.order_id
                for line in lines
                if not line.process_card_id and line.order_id
            })
            for order_id in order_ids:
                order = locked_orders.get(order_id)
                if order is None:
                    order = QualityOrder.objects.select_for_update().get(pk=order_id)
                    locked_orders[order_id] = order
                current_order_lines = [
                    line for line in lines
                    if (line.order_id or (line.process_card.order_id if line.process_card_id else None)) == order_id
                ]
                incoming_weight = sum(
                    (Decimal(line.net_weight_kg) for line in current_order_lines),
                    Decimal("0"),
                )
                incoming_pieces = sum(int(line.piece_quantity or 0) for line in current_order_lines)
                unit_candidates = [
                    line.unit_weight_g_snapshot for line in current_order_lines
                    if line.unit_weight_g_snapshot and line.unit_weight_g_snapshot > 0
                ]
                previous = list(
                    QualityShipmentLine.objects.filter(
                        batch__status=QualityShipmentBatch.Status.CONFIRMED,
                    )
                    .exclude(batch_id=batch.pk)
                    .filter(Q(order_id=order_id) | Q(process_card__order_id=order_id))
                    .select_related("process_card")
                )
                previous_weight = sum(
                    (Decimal(line.delivered_net_weight_kg) for line in previous),
                    Decimal("0"),
                )
                previous_pieces = 0
                for line in previous:
                    quantity = line.piece_quantity
                    if quantity is None and line.unit_weight_g_snapshot and line.net_weight_kg:
                        quantity = int(
                            (Decimal(line.net_weight_kg) * Decimal("1000") / Decimal(line.unit_weight_g_snapshot))
                            .quantize(Decimal("1"), rounding=ROUND_HALF_UP)
                        )
                    previous_pieces += max(
                        int(quantity or 0) - int(line.returned_piece_quantity or 0),
                        0,
                    )
                    if line.unit_weight_g_snapshot and line.unit_weight_g_snapshot > 0:
                        unit_candidates.append(line.unit_weight_g_snapshot)

                # Legacy quantity-only shipments predate the weighted lines;
                # include them in the same order allowance when present.
                legacy_pieces = sum(
                    max(
                        int(shipment.shipped_quantity or 0)
                        - int(shipment.returned_quantity or 0),
                        0,
                    )
                    for shipment in QualityShipment.objects.filter(
                        order_id=order_id
                    ).prefetch_related("reworks")
                )
                previous_pieces += legacy_pieces
                unit = unit_candidates[0] if unit_candidates else None
                if unit is None and order.product_specification_id:
                    unit = ProductUnitWeight.objects.filter(
                        product_specification_id=order.product_specification_id,
                        is_active=True,
                    ).order_by("-measured_on", "-id").values_list("unit_weight_g", flat=True).first()
                if unit is None or unit <= 0:
                    raise DRFValidationError({"lines": f"订单 {order.order_no} 尚未填写大于 0 的成品单重。"})
                legacy_weight = Decimal(legacy_pieces) * Decimal(unit) / Decimal("1000")
                max_weight = (
                    Decimal(order.order_quantity) * Decimal(unit) / Decimal("1000") * Decimal("1.10")
                ).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
                if previous_weight + legacy_weight + incoming_weight > max_weight:
                    raise DRFValidationError({"lines": f"订单 {order.order_no} 累计出货净重不能超过理论重量的110%。"})
                if (
                    Decimal(previous_pieces + incoming_pieces)
                    > Decimal(order.order_quantity) * Decimal("1.10")
                ):
                    raise DRFValidationError({"lines": f"订单 {order.order_no} 累计出货件数不能超过订单数量的110%。"})
            # Ensure batch-level context is also frozen for manually entered
            # rows, and preserve an exact product specification history.
            if lines:
                first = lines[0]
                if batch.order_id is None:
                    batch.order_id = first.order_id or (first.process_card.order_id if first.process_card_id else None)
                if batch.specification_snapshot == "":
                    batch.specification_snapshot = first.specification_snapshot
                if batch.material_snapshot == "":
                    batch.material_snapshot = first.material_snapshot
                if batch.unit_weight_g is None:
                    batch.unit_weight_g = first.unit_weight_g_snapshot
                if batch.product_specification_id is None:
                    batch.product_specification_id = first.product_specification_id
            batch.status = QualityShipmentBatch.Status.CONFIRMED
            batch.save()
            # Mirror a legacy inspector into the M2M when a batch was created
            # by an older client without inspector_ids.
            if not batch.inspectors.exists() and batch.inspector_id:
                batch.inspectors.add(batch.inspector_id)
            for card_id in by_card:
                ProcessCard.objects.get(pk=card_id).refresh_shipping_status()
        return Response(self.get_serializer(batch).data)

    @staticmethod
    def _normalize_repeat_line(line, unit_weight):
        """Expand one equal-weight scale reading into immutable totals."""
        unit = Decimal(unit_weight)
        if unit <= 0:
            raise DRFValidationError({"lines": "成品单重必须大于 0。"})
        repeat_count = int(line.product_batch_count or 1)
        if line.single_batch_net_weight_kg is not None:
            if line.process_card_id and line.process_card_shipment_quantity is None:
                line.process_card_shipment_quantity = line.process_card.quantity
            if line.process_card_shipment_quantity is None:
                raise DRFValidationError(
                    {
                        "process_card_shipment_quantity": (
                            "重复称重出货必须填写单批流程卡出货数量。"
                        )
                    }
                )
            single_weight = Decimal(line.single_batch_net_weight_kg)
            if single_weight <= 0:
                raise DRFValidationError({"lines": "单批净重必须大于 0。"})
            line.net_weight_kg = (
                single_weight * Decimal(repeat_count)
            ).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
            single_pieces = int(
                (single_weight * Decimal("1000") / unit).quantize(
                    Decimal("1"), rounding=ROUND_HALF_UP
                )
            )
            line.pieces_per_batch = single_pieces
            line.piece_quantity = single_pieces * repeat_count
        elif line.piece_quantity is None:
            line.piece_quantity = int(
                (Decimal(line.net_weight_kg) * Decimal("1000") / unit).quantize(
                    Decimal("1"), rounding=ROUND_HALF_UP
                )
            )
            single_pieces = int(line.piece_quantity)
        else:
            single_pieces = int(
                (Decimal(line.piece_quantity) / Decimal(repeat_count)).quantize(
                    Decimal("1"), rounding=ROUND_HALF_UP
                )
            )
        standard = line.process_card_shipment_quantity
        if standard is not None and Decimal(single_pieces) > Decimal(standard) * Decimal("1.10"):
            raise DRFValidationError(
                {
                    "process_card_shipment_quantity": (
                        "单批实际出货数量不能超过流程卡出货数量的110%。"
                    )
                }
            )

    @action(detail=True, methods=["post"], url_path="assign-inspectors")
    def assign_inspectors(self, request, pk=None):
        """Add or correct responsible inspectors without rewriting shipment facts."""
        raw_ids = request.data.get("inspector_ids")
        if not isinstance(raw_ids, list):
            raise DRFValidationError({"inspector_ids": "请提交品检员编号列表。"})
        try:
            ids = list(dict.fromkeys(int(value) for value in raw_ids))
        except (TypeError, ValueError):
            raise DRFValidationError({"inspector_ids": "品检员编号格式无效。"})
        with transaction.atomic():
            batch = QualityShipmentBatch.objects.select_for_update().get(pk=pk)
            if batch.status == QualityShipmentBatch.Status.VOID:
                raise DRFValidationError({"status": "已作废出货记录不能补录品检员。"})
            people = list(
                QualityEmployee.objects.filter(
                    pk__in=ids,
                    is_active=True,
                    role__in=(QualityEmployee.Role.INSPECTOR, QualityEmployee.Role.BOTH),
                )
            )
            by_id = {person.pk: person for person in people}
            missing = [value for value in ids if value not in by_id]
            if missing:
                raise DRFValidationError(
                    {"inspector_ids": f"品检员不存在、已停用或岗位不符：{', '.join(map(str, missing))}"}
                )
            ordered = [by_id[value] for value in ids]
            batch.inspectors.set(ordered)
            batch.inspector = ordered[0] if ordered else None
            batch.save(update_fields=["inspector", "updated_at"])
        return Response(self.get_serializer(batch).data)

    @staticmethod
    def _save_line_history(line, *, card, order, created_by=None, measured_on=None):
        """Persist immutable snapshots and resolve/create exact product specs."""
        spec = str(line.specification_snapshot or "").strip()
        material = str(line.material_snapshot or "").strip()
        if not spec and order:
            spec = str(order.specification or "").strip()
            line.specification_snapshot = spec
        if not material and order:
            material = str(order.material or "").strip()
            line.material_snapshot = material
        product = line.product_specification
        if product is not None:
            if spec and product.specification and product.specification != spec:
                raise DRFValidationError({"lines": "规格快照与产品规格资料不一致。"})
            if material and product.material and product.material != material:
                raise DRFValidationError({"lines": "材质快照与产品规格资料不一致。"})
        elif spec or material:
            matches = ProductSpecification.objects.filter(
                is_active=True, specification=spec, material=material
            ).order_by("id")
            count = matches.count()
            if count == 1:
                line.product_specification = matches.first()
            elif count > 1:
                raise DRFValidationError({"lines": "规格和材质对应多条产品资料，请明确选择产品规格。"})
            else:
                # The operator may enter a genuinely new specification.  The
                # exact snapshot is retained and a master row is created in
                # this same transaction so retries cannot lose the history.
                product = ProductSpecification.objects.create(
                    product_name=(order.product_name if order else ""),
                    specification=spec,
                    material=material,
                )
                line.product_specification = product
        if line.product_specification_id and line.unit_weight_g_snapshot and created_by:
            # Keep the manually confirmed unit weight available to subsequent
            # candidate forms.  Existing identical active standards are reused
            # so repeated confirmation retries remain idempotent.
            ProductUnitWeight.objects.get_or_create(
                product_specification_id=line.product_specification_id,
                unit_weight_g=line.unit_weight_g_snapshot,
                is_active=True,
                defaults={
                    "measured_on": measured_on or timezone.localdate(),
                    "backfill_reason": "出货确认自动保存历史单重",
                    "created_by": created_by,
                    "notes": "由出货确认自动保存的单重历史",
                },
            )
        line.save(update_fields=[
            "product_specification", "specification_snapshot", "material_snapshot",
            "unit_weight_g_snapshot", "single_batch_net_weight_kg", "net_weight_kg",
            "process_card_shipment_quantity", "product_batch_count", "pieces_per_batch",
            "piece_quantity", "theoretical_weight_kg_snapshot",
            "max_allowed_weight_kg_snapshot", "updated_at",
        ])

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

    @action(detail=False, methods=["get"], url_path="candidates")
    def candidates(self, request):
        return QualityShippingCandidatesView().get(request)

    @action(detail=False, methods=["get"], url_path="check-shipment-no")
    def check_shipment_no(self, request):
        shipment_no = str(request.query_params.get("shipment_no", "") or "").strip().upper()
        exclude_id = request.query_params.get("exclude_id")
        queryset = QualityShipmentBatch.objects.filter(shipment_no__iexact=shipment_no)
        if exclude_id and str(exclude_id).isdigit():
            queryset = queryset.exclude(pk=int(exclude_id))
        item = queryset.select_related("order", "inspector").first()
        legacy = QualityShipment.objects.filter(shipment_no__iexact=shipment_no).first()
        payload = {
            "exists": bool(item or legacy),
            "duplicate": bool(item or legacy),
            "can_resume": bool(item and item.status == QualityShipmentBatch.Status.DRAFT),
            "status": item.status if item else ("LEGACY" if legacy else None),
        }
        if item:
            payload["shipment"] = QualityShipmentBatchSerializer(
                item, context={"request": request}
            ).data
        elif legacy:
            payload["legacy_shipment"] = QualityShipmentSerializer(
                legacy, context={"request": request}
            ).data
        return Response(payload)


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
        .prefetch_related("inspectors")
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

    @action(detail=False, methods=["get"], url_path="candidates")
    def candidates(self, request):
        return QualityShippingCandidatesView().get(request)

    @action(detail=False, methods=["get"], url_path="check-shipment-no")
    def check_shipment_no(self, request):
        shipment_no = str(request.query_params.get("shipment_no", "") or "").strip().upper()
        exclude_id = request.query_params.get("exclude_id")
        queryset = QualityShipment.objects.filter(shipment_no__iexact=shipment_no)
        if exclude_id and str(exclude_id).isdigit():
            queryset = queryset.exclude(pk=int(exclude_id))
        item = queryset.select_related("order", "inspector").first()
        weighted = QualityShipmentBatch.objects.filter(shipment_no__iexact=shipment_no).first()
        payload = {
            "exists": bool(item or weighted),
            "duplicate": bool(item or weighted),
            "can_resume": bool(weighted and weighted.status == QualityShipmentBatch.Status.DRAFT),
            "status": "LEGACY" if item else (weighted.status if weighted else None),
        }
        if item:
            payload["shipment"] = QualityShipmentSerializer(
                item, context={"request": request}
            ).data
        elif weighted:
            payload["weighted_shipment"] = QualityShipmentBatchSerializer(
                weighted, context={"request": request}
            ).data
        return Response(payload)

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
        weighted_batches = list(
            QualityShipmentBatch.objects.filter(
                status=QualityShipmentBatch.Status.CONFIRMED,
                shipment_date__isnull=False,
                shipment_date__gte=date_from,
                shipment_date__lte=date_to,
            )
            .select_related("inspector", "order")
            .prefetch_related(
                "inspectors",
                Prefetch(
                    "lines",
                    queryset=QualityShipmentLine.objects.select_related(
                        "order", "process_card__order"
                    ),
                ),
            )
        )
        reworks = ReturnRework.objects.filter(
            rework_date__gte=date_from, rework_date__lte=date_to
        )

        def weighted_line_quantity(line, batch):
            quantity = line.piece_quantity
            if quantity is None:
                card = line.process_card if line.process_card_id else None
                unit_weight = (
                    line.unit_weight_g_snapshot
                    or (card.unit_weight_g if card else None)
                    or batch.unit_weight_g
                )
                if unit_weight and line.net_weight_kg:
                    quantity = (
                        Decimal(line.net_weight_kg)
                        * Decimal("1000")
                        / Decimal(unit_weight)
                    ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            # Batch count is a convenience note only.  It is used as a final
            # compatibility fallback for very old rows that have neither a
            # persisted piece count nor enough weight/unit data; confirmed new
            # rows always carry the scale-derived quantity above.
            if quantity is None and line.product_batch_count and line.pieces_per_batch:
                quantity = line.product_batch_count * line.pieces_per_batch
            return max(0, _integer(quantity))

        # Resolve the weighted rows once so every view of the summary uses the
        # same quantity and a multi-inspector batch never multiplies company or
        # order totals.
        weighted_rows = []
        for batch in weighted_batches:
            batch_quantity = 0
            order_quantities = {}
            for line in batch.lines.all():
                quantity = weighted_line_quantity(line, batch)
                batch_quantity += quantity
                card = line.process_card if line.process_card_id else None
                order = line.order or (card.order if card else None) or batch.order
                if order is not None:
                    order_quantities[order.pk] = (
                        order_quantities.get(order.pk, 0) + quantity
                    )
            people = list(batch.inspectors.all())
            if not people and batch.inspector_id:
                people = [batch.inspector]
            people = list({person.pk: person for person in people}.values())
            weighted_rows.append((batch, batch_quantity, order_quantities, people))

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
        totals["shipped_quantity"] += sum(
            batch_quantity for _, batch_quantity, _, _ in weighted_rows
        )
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
        for _, _, order_quantities, _ in weighted_rows:
            order_ids.update(order_quantities)
        totals.update(
            {
                "shipment_count": _integer(shipment_totals["shipment_count"])
                + len(weighted_rows),
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
            daily[cursor] = {
                "date": cursor.isoformat(),
                **_empty_quantities(),
                "shipment_count": 0,
            }
            cursor += timedelta(days=1)
        for item in shipments.values("shipment_date").annotate(
            inspection_quantity=Sum("inspection_quantity"),
            qualified_quantity=Sum("qualified_quantity"),
            defective_quantity=Sum("defective_quantity"),
            shipped_quantity=Sum("shipped_quantity"),
            shipment_count=Count("id"),
        ):
            row = daily[item["shipment_date"]]
            for key in (
                "inspection_quantity",
                "qualified_quantity",
                "defective_quantity",
                "shipped_quantity",
            ):
                row[key] = _integer(item[key])
            row["shipment_count"] = _integer(item["shipment_count"])
        for batch, batch_quantity, _, _ in weighted_rows:
            row = daily[batch.shipment_date]
            row["shipped_quantity"] += batch_quantity
            row["shipment_count"] += 1
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
        for _, _, weighted_order_quantities, _ in weighted_rows:
            for order_id, quantity in weighted_order_quantities.items():
                row = order_quantities.setdefault(
                    order_id,
                    {**_empty_quantities(), "shipment_count": 0, "rework_count": 0},
                )
                row["shipped_quantity"] += quantity
                row["shipment_count"] += 1
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
        employee_inspection_dates = {}
        for employee_id, shipment_date in shipments.values_list(
            "inspector_id", "shipment_date"
        ).distinct():
            employee_inspection_dates.setdefault(employee_id, set()).add(shipment_date)
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
        for batch, batch_quantity, _, people in weighted_rows:
            if not people:
                continue
            base, remainder = divmod(batch_quantity, len(people))
            for index, person in enumerate(people):
                row = employee_quantities.setdefault(
                    person.pk,
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
                row["shipped_quantity"] += base + (1 if index < remainder else 0)
                row["shipment_count"] += 1
                employee_inspection_dates.setdefault(person.pk, set()).add(
                    batch.shipment_date
                )
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
            row["inspection_days"] = len(
                employee_inspection_dates.get(employee_id, set())
            )
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
