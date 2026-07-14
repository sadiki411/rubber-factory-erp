from collections import Counter
from datetime import datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID
from zipfile import BadZipFile

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import OperationalError, transaction
from django.db.models import BigIntegerField, Count, F, Prefetch, Q, Sum
from django.db.models.functions import Coalesce
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_date
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from openpyxl.utils.exceptions import InvalidFileException
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from molds.models import MoldAsset

from .imports import (
    commit_production_batch,
    create_production_template,
    create_production_error_report,
    preview_production_workbook,
)
from .models import (
    ProductionDailyLog,
    ProductionImportBatch,
    ProductionRun,
    ProductionSettlementRevision,
    ProductionStation,
    normalize_production_station_code,
)
from .serializers import (
    CompleteAndPutawayProductionRunSerializer,
    CompleteProductionRunSerializer,
    ProductionDailyLogSerializer,
    ProductionMoldSerializer,
    ProductionRunSerializer,
    ProductionSettlementDetailSerializer,
    ProductionSettlementRevisionSerializer,
    ProductionSettlementSerializer,
    ProductionStationSerializer,
    StartProductionRunSerializer,
)
from .services import (
    complete_and_putaway_production_run,
    invalidate_settlement,
    record_settlement_revision,
    start_production_run,
)


class ProductionPagination(PageNumberPagination):
    page_size = 30
    page_size_query_param = "page_size"
    max_page_size = 500


def _run_queryset():
    return ProductionRun.objects.select_related(
        "station__machine", "mold__mold_model", "created_by", "settled_by"
    ).prefetch_related("daily_logs")


class ProductionStationViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ProductionStationSerializer
    pagination_class = None

    def get_queryset(self):
        queryset = ProductionStation.objects.select_related("machine")
        active = str(self.request.query_params.get("active", "true")).strip().lower()
        if active in {"1", "true", "yes"}:
            queryset = queryset.filter(is_active=True)
        group = str(self.request.query_params.get("group", "")).strip().upper()
        if group:
            queryset = queryset.filter(group=group)
        return queryset.order_by("group", "position_no")


class ProductionRunViewSet(viewsets.ModelViewSet):
    serializer_class = ProductionRunSerializer
    pagination_class = ProductionPagination
    http_method_names = ["get", "post", "put", "patch", "head", "options"]

    def get_queryset(self):
        queryset = _run_queryset()
        params = self.request.query_params
        keyword = str(params.get("q", "")).strip()
        if keyword:
            queryset = queryset.filter(
                Q(order_no__icontains=keyword)
                | Q(specification__icontains=keyword)
                | Q(material__icontains=keyword)
                | Q(operator__icontains=keyword)
                | Q(mold__asset_code__icontains=keyword)
                | Q(mold__mold_model__code__icontains=keyword)
                | Q(mold__mold_model__product_name__icontains=keyword)
            )

        run_status = str(params.get("status", "")).strip().upper()
        if run_status:
            statuses = [item.strip() for item in run_status.split(",") if item.strip()]
            invalid = [item for item in statuses if item not in ProductionRun.Status.values]
            if invalid:
                raise DRFValidationError({"status": f"无效的生产状态：{', '.join(invalid)}"})
            queryset = queryset.filter(status__in=statuses)

        station = str(params.get("station", "")).strip()
        if station:
            normalized_station = normalize_production_station_code(station)
            station_record = ProductionStation.objects.filter(
                code__iexact=normalized_station
            ).first()
            if station_record is not None:
                queryset = queryset.filter(station=station_record)
            elif station.isdigit():
                queryset = queryset.filter(station_id=int(station))
            else:
                queryset = queryset.none()

        group = str(params.get("group", "")).strip().upper()
        if group:
            queryset = queryset.filter(station__group=group)

        mold = str(params.get("mold", "")).strip()
        if mold:
            if mold.isdigit():
                queryset = queryset.filter(mold_id=int(mold))
            else:
                queryset = queryset.filter(mold__asset_code__iexact=mold)

        date_from = str(params.get("date_from", "")).strip()
        date_to = str(params.get("date_to", "")).strip()
        parsed_from = parse_date(date_from) if date_from else None
        parsed_to = parse_date(date_to) if date_to else None
        if date_from and parsed_from is None:
            raise DRFValidationError({"date_from": "日期格式应为yyyy-mm-dd。"})
        if date_to and parsed_to is None:
            raise DRFValidationError({"date_to": "日期格式应为yyyy-mm-dd。"})
        if parsed_from and parsed_to and parsed_from > parsed_to:
            raise DRFValidationError({"date_to": "结束日期不能早于开始日期。"})
        if parsed_from or parsed_to:
            current_tz = timezone.get_current_timezone()
            period_start = (
                timezone.make_aware(
                    datetime.combine(parsed_from, time.min), current_tz
                )
                if parsed_from
                else None
            )
            period_end = (
                timezone.make_aware(
                    datetime.combine(parsed_to + timedelta(days=1), time.min),
                    current_tz,
                )
                if parsed_to
                else None
            )
            log_filter = Q()
            overlap_filter = Q(loaded_at__isnull=False)
            planned_created_filter = Q(
                status=ProductionRun.Status.PLANNED,
                loaded_at__isnull=True,
            )
            if parsed_from:
                log_filter &= Q(daily_logs__production_date__gte=parsed_from)
                overlap_filter &= Q(unloaded_at__gt=period_start) | Q(
                    unloaded_at__isnull=True
                )
                planned_created_filter &= Q(created_at__gte=period_start)
            if parsed_to:
                log_filter &= Q(daily_logs__production_date__lte=parsed_to)
                overlap_filter &= Q(loaded_at__lt=period_end)
                planned_created_filter &= Q(created_at__lt=period_end)
            queryset = queryset.filter(
                log_filter | overlap_filter | planned_created_filter
            ).distinct()

        ordering = str(params.get("ordering", "")).strip()
        allowed = {
            "loaded_at",
            "-loaded_at",
            "expected_change_at",
            "-expected_change_at",
            "created_at",
            "-created_at",
            "order_no",
            "-order_no",
        }
        if ordering:
            if ordering not in allowed:
                raise DRFValidationError({"ordering": "不支持该排序字段。"})
            queryset = queryset.order_by(ordering, "-id")
        return queryset

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    @extend_schema(request=ProductionDailyLogSerializer, responses=ProductionRunSerializer)
    @action(detail=True, methods=["post"], url_path="daily-logs")
    def daily_logs(self, request, pk=None):
        run = self.get_object()
        if run.status == ProductionRun.Status.PLANNED:
            raise DRFValidationError({"detail": "待上机订单不能新增生产日报。"})
        if run.status == ProductionRun.Status.CANCELLED:
            raise DRFValidationError({"detail": "已取消订单不能新增生产日报。"})
        try:
            with transaction.atomic():
                run = ProductionRun.objects.select_for_update().get(pk=run.pk)
                if run.status == ProductionRun.Status.PLANNED:
                    raise DRFValidationError(
                        {"detail": "待上机订单不能新增生产日报。"}
                    )
                if run.status == ProductionRun.Status.CANCELLED:
                    raise DRFValidationError(
                        {"detail": "已取消订单不能新增生产日报。"}
                    )
                serializer = ProductionDailyLogSerializer(
                    data=request.data,
                    context={"run": run, "request": request},
                )
                serializer.is_valid(raise_exception=True)
                invalidate_settlement(run, request.user)
                serializer.save(run=run)
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.message_dict) from exc
        refreshed = _run_queryset().get(pk=run.pk)
        return Response(
            ProductionRunSerializer(refreshed, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        request=ProductionDailyLogSerializer,
        responses=ProductionRunSerializer,
        parameters=[
            OpenApiParameter(
                name="log_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                description="生产日报记录ID",
            )
        ],
    )
    @action(
        detail=True,
        methods=["patch"],
        url_path=r"daily-logs/(?P<log_id>[^/.]+)",
    )
    def update_daily_log(self, request, pk=None, log_id=None):
        run = self.get_object()
        try:
            with transaction.atomic():
                run = ProductionRun.objects.select_for_update().get(pk=run.pk)
                log = get_object_or_404(
                    ProductionDailyLog.objects.select_for_update(),
                    run=run,
                    pk=log_id,
                )
                previous_mold_count = log.produced_mold_count
                serializer = ProductionDailyLogSerializer(
                    log,
                    data=request.data,
                    partial=True,
                    context={"run": run, "request": request},
                )
                serializer.is_valid(raise_exception=True)
                next_mold_count = serializer.validated_data.get(
                    "produced_mold_count", previous_mold_count
                )
                if next_mold_count != previous_mold_count:
                    invalidate_settlement(run, request.user)
                serializer.save()
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.message_dict) from exc
        refreshed = _run_queryset().get(pk=run.pk)
        return Response(
            ProductionRunSerializer(refreshed, context={"request": request}).data
        )

    @extend_schema(
        request=StartProductionRunSerializer,
        responses=ProductionRunSerializer,
    )
    @action(detail=True, methods=["post"])
    def start(self, request, pk=None):
        input_serializer = StartProductionRunSerializer(data=request.data or {})
        input_serializer.is_valid(raise_exception=True)
        try:
            run = start_production_run(
                self.get_object(),
                request.user,
                **input_serializer.validated_data,
            )
            refreshed = _run_queryset().get(pk=run.pk)
        except OperationalError as exc:
            raise DRFValidationError(
                {"detail": "数据库正忙，确认上机未执行，请稍后重试。"}
            ) from exc
        return Response(
            ProductionRunSerializer(
                refreshed,
                context={"request": request},
            ).data
        )

    @extend_schema(request=CompleteProductionRunSerializer, responses=ProductionRunSerializer)
    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        input_serializer = CompleteProductionRunSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        try:
            with transaction.atomic():
                run = get_object_or_404(ProductionRun, pk=pk)
                if run.status == ProductionRun.Status.COMPLETED:
                    refreshed = _run_queryset().get(pk=run.pk)
                    return Response(
                        ProductionRunSerializer(
                            refreshed, context={"request": request}
                        ).data
                    )
                if run.status == ProductionRun.Status.CANCELLED:
                    raise DRFValidationError({"detail": "已取消订单不能执行完成。"})
                if run.status != ProductionRun.Status.RUNNING or not run.loaded_at:
                    raise DRFValidationError({"loaded_at": "请先填写上模时间。"})
                unloaded_at = input_serializer.validated_data.get(
                    "unloaded_at", timezone.now()
                )
                if unloaded_at < run.loaded_at:
                    raise DRFValidationError(
                        {"unloaded_at": "下机时间不能早于上模时间。"}
                    )
                run.unloaded_at = unloaded_at
                run.status = ProductionRun.Status.COMPLETED
                run.full_clean()
                updated = ProductionRun.objects.filter(
                    pk=run.pk,
                    status=ProductionRun.Status.RUNNING,
                    unloaded_at__isnull=True,
                ).update(
                    unloaded_at=unloaded_at,
                    status=ProductionRun.Status.COMPLETED,
                    updated_at=timezone.now(),
                )
                if updated != 1:
                    run.refresh_from_db()
                    if run.status != ProductionRun.Status.COMPLETED:
                        raise DRFValidationError(
                            {"detail": "生产记录状态已变化，请刷新后重试。"}
                        )
            refreshed = _run_queryset().get(pk=run.pk)
        except OperationalError as exc:
            raise DRFValidationError(
                {"detail": "数据库正忙，下机操作未执行，请稍后重试。"}
            ) from exc
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.message_dict) from exc
        return Response(ProductionRunSerializer(refreshed, context={"request": request}).data)

    @extend_schema(
        request=CompleteAndPutawayProductionRunSerializer,
        responses=ProductionRunSerializer,
    )
    @action(detail=True, methods=["post"], url_path="complete-and-putaway")
    def complete_and_putaway(self, request, pk=None):
        input_serializer = CompleteAndPutawayProductionRunSerializer(
            data=request.data or {}
        )
        input_serializer.is_valid(raise_exception=True)
        try:
            run = complete_and_putaway_production_run(
                self.get_object(),
                request.user,
                **input_serializer.validated_data,
            )
            refreshed = _run_queryset().get(pk=run.pk)
        except OperationalError as exc:
            raise DRFValidationError(
                {
                    "detail": (
                        "数据库正忙，结束生产并归位未执行，请稍后刷新后重试。"
                    )
                }
            ) from exc
        return Response(
            ProductionRunSerializer(
                refreshed,
                context={"request": request},
            ).data
        )

    @extend_schema(
        methods=["GET"],
        responses=ProductionSettlementDetailSerializer,
    )
    @extend_schema(
        methods=["POST"],
        request=ProductionSettlementSerializer,
        responses=ProductionRunSerializer,
    )
    @action(detail=True, methods=["get", "post"])
    def settlement(self, request, pk=None):
        if request.method == "GET":
            run = self.get_object()
            revisions = run.settlement_revisions.select_related("changed_by").all()
            return Response(
                {
                    "run": ProductionRunSerializer(
                        run, context={"request": request}
                    ).data,
                    "revisions": ProductionSettlementRevisionSerializer(
                        revisions, many=True
                    ).data,
                }
            )

        try:
            with transaction.atomic():
                run = (
                    ProductionRun.objects.select_for_update()
                    .select_related("settled_by")
                    .get(pk=pk)
                )
                # Lock the mold-count source while validating and saving settlement.
                list(
                    ProductionDailyLog.objects.select_for_update()
                    .filter(run=run)
                    .values_list("pk", flat=True)
                )
                input_serializer = ProductionSettlementSerializer(
                    data=request.data, context={"run": run}
                )
                input_serializer.is_valid(raise_exception=True)
                for field, value in input_serializer.validated_data.items():
                    setattr(run, field, value)
                run.settled_at = timezone.now()
                run.settled_by = request.user
                run.save(
                    update_fields=[
                        "actual_good_quantity",
                        "actual_defective_quantity",
                        "total_material_kg",
                        "labor_cost",
                        "energy_cost",
                        "other_cost",
                        "settlement_notes",
                        "settled_at",
                        "settled_by",
                        "updated_at",
                    ]
                )
                record_settlement_revision(
                    run, request.user, ProductionSettlementRevision.Action.SETTLED
                )
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.message_dict) from exc
        except OperationalError as exc:
            raise DRFValidationError(
                {"detail": "数据库正忙，结算未保存，请稍后重试。"}
            ) from exc
        refreshed = _run_queryset().get(pk=run.pk)
        return Response(
            ProductionRunSerializer(refreshed, context={"request": request}).data
        )


class BoardRunSerializer(serializers.ModelSerializer):
    order_no = serializers.CharField()
    station_id = serializers.IntegerField()
    station_code = serializers.CharField(source="station.code")
    mold_id = serializers.IntegerField(allow_null=True)
    mold_code = serializers.CharField(source="mold.asset_code", allow_null=True)
    mold_model_code = serializers.CharField(source="mold.mold_model.code", allow_null=True)
    mold_product_name = serializers.CharField(
        source="mold.mold_model.product_name", allow_null=True
    )
    produced_mold_count = serializers.IntegerField(read_only=True)
    good_quantity = serializers.IntegerField(read_only=True)
    progress_percent = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    remaining_mold_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = ProductionRun
        fields = [
            "id",
            "order_no",
            "station_id",
            "station_code",
            "mold_id",
            "mold_code",
            "mold_model_code",
            "mold_product_name",
            "specification",
            "material",
            "order_quantity",
            "planned_mold_count",
            "produced_mold_count",
            "good_quantity",
            "progress_percent",
            "remaining_mold_count",
            "operator",
            "status",
            "loaded_at",
            "expected_change_at",
            "material_changed_at",
            "estimated_hours",
        ]


class ProductionBoardView(APIView):
    @extend_schema(responses=dict)
    def get(self, request):
        try:
            reminder_minutes = int(request.query_params.get("reminder_minutes", 30))
        except (TypeError, ValueError) as exc:
            raise DRFValidationError(
                {"reminder_minutes": "提醒分钟数必须为整数。"}
            ) from exc
        if not 1 <= reminder_minutes <= 1440:
            raise DRFValidationError(
                {"reminder_minutes": "提醒分钟数必须在1至1440之间。"}
            )

        active_runs = _run_queryset().filter(status__in=ProductionRun.ACTIVE_STATUSES)
        mounted_molds = MoldAsset.objects.filter(
            is_active=True,
            status=MoldAsset.Status.ON_MACHINE,
        ).select_related("mold_model").order_by("asset_code")
        stations = ProductionStation.objects.filter(is_active=True).select_related(
            "machine"
        ).prefetch_related(
            Prefetch("runs", queryset=active_runs, to_attr="active_board_runs"),
            Prefetch(
                "machine__current_molds",
                queryset=mounted_molds,
                to_attr="board_mounted_molds",
            ),
        ).order_by("group", "position_no")
        now = timezone.now()
        threshold = now + timedelta(minutes=reminder_minutes)
        counts = Counter()
        groups = []
        stations_by_group = {}
        for station in stations:
            stations_by_group.setdefault(station.group, []).append(station)
        for group_code, group_stations in stations_by_group.items():
            station_payloads = []
            for station in group_stations:
                runs = list(station.active_board_runs)
                run = runs[0] if runs else None
                station_molds = (
                    list(getattr(station.machine, "board_mounted_molds", []))
                    if station.machine
                    else []
                )
                minutes_to_change = None
                if run is None:
                    if station_molds:
                        reminder_status = "MOUNTED"
                    else:
                        reminder_status = "IDLE"
                        counts["idle"] += 1
                elif run.status == ProductionRun.Status.PLANNED:
                    reminder_status = "PLANNED"
                    counts["planned"] += 1
                else:
                    counts["running"] += 1
                    if run.expected_change_at:
                        minutes_to_change = int(
                            (run.expected_change_at - now).total_seconds() // 60
                        )
                    if run.expected_change_at and run.expected_change_at <= now:
                        reminder_status = "OVERDUE"
                        counts["overdue"] += 1
                    elif run.expected_change_at and run.expected_change_at <= threshold:
                        reminder_status = "DUE_SOON"
                        counts["due_soon"] += 1
                    else:
                        reminder_status = "NORMAL"
                        counts["normal"] += 1
                station_data = ProductionStationSerializer(station).data
                station_data.update(
                    {
                        "run": BoardRunSerializer(run).data if run else None,
                        "mounted_molds": ProductionMoldSerializer(
                            station_molds, many=True
                        ).data,
                        "reminder_status": reminder_status,
                        "minutes_to_change": minutes_to_change,
                    }
                )
                station_payloads.append(station_data)
                if station_molds:
                    counts["mounted"] += 1
                if station_molds or (
                    run is not None and run.status == ProductionRun.Status.RUNNING
                ):
                    counts["occupied"] += 1
            groups.append({"group": group_code, "stations": station_payloads})
        counts["total"] = sum(len(group["stations"]) for group in groups)
        return Response(
            {
                "generated_at": now,
                "reminder_window_minutes": reminder_minutes,
                "counts": {
                    key: counts.get(key, 0)
                    for key in [
                        "total",
                        "idle",
                        "occupied",
                        "mounted",
                        "planned",
                        "running",
                        "normal",
                        "due_soon",
                        "overdue",
                    ]
                },
                "groups": groups,
            }
        )


def _quantize(value):
    return Decimal(value or 0).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _decimal_text(value):
    return format(_quantize(value), "f")


class ProductionSummaryView(APIView):
    @extend_schema(responses=dict)
    def get(self, request):
        params = request.query_params
        date_from = str(params.get("date_from", "")).strip()
        date_to = str(params.get("date_to", "")).strip()
        group = str(params.get("group", "")).strip().upper()

        parsed_from = parse_date(date_from) if date_from else None
        parsed_to = parse_date(date_to) if date_to else None
        if date_from and parsed_from is None:
            raise DRFValidationError({"date_from": "日期格式应为yyyy-mm-dd。"})
        if date_to and parsed_to is None:
            raise DRFValidationError({"date_to": "日期格式应为yyyy-mm-dd。"})
        if parsed_from and parsed_to and parsed_from > parsed_to:
            raise DRFValidationError({"date_to": "结束日期不能早于开始日期。"})

        current_tz = timezone.get_current_timezone()
        period_start = (
            timezone.make_aware(datetime.combine(parsed_from, time.min), current_tz)
            if parsed_from
            else None
        )
        period_end = (
            timezone.make_aware(
                datetime.combine(parsed_to + timedelta(days=1), time.min), current_tz
            )
            if parsed_to
            else None
        )

        log_queryset = ProductionDailyLog.objects.all()
        if parsed_from:
            log_queryset = log_queryset.filter(production_date__gte=parsed_from)
        if parsed_to:
            log_queryset = log_queryset.filter(production_date__lte=parsed_to)

        queryset = ProductionRun.objects.select_related(
            "station__machine", "mold__mold_model", "created_by"
        ).prefetch_related(
            Prefetch("daily_logs", queryset=log_queryset, to_attr="summary_logs")
        )
        if group:
            queryset = queryset.filter(station__group=group)
        if parsed_from or parsed_to:
            log_filter = Q()
            overlap_filter = Q(loaded_at__isnull=False)
            created_filter = Q(
                status=ProductionRun.Status.PLANNED,
                loaded_at__isnull=True,
            )
            if parsed_from:
                log_filter &= Q(daily_logs__production_date__gte=parsed_from)
                overlap_filter &= Q(unloaded_at__gt=period_start) | Q(
                    unloaded_at__isnull=True
                )
                created_filter &= Q(created_at__gte=period_start)
            if parsed_to:
                log_filter &= Q(daily_logs__production_date__lte=parsed_to)
                overlap_filter &= Q(loaded_at__lt=period_end)
                created_filter &= Q(created_at__lt=period_end)
            queryset = queryset.filter(
                log_filter | overlap_filter | created_filter
            ).distinct()

        settlement_queryset = ProductionRun.objects.filter(settled_at__isnull=False)
        if group:
            settlement_queryset = settlement_queryset.filter(station__group=group)
        if period_start:
            settlement_queryset = settlement_queryset.filter(
                settled_at__gte=period_start
            )
        if period_end:
            settlement_queryset = settlement_queryset.filter(settled_at__lt=period_end)
        settled_runs = list(settlement_queryset)

        runs = list(queryset)
        status_counts = Counter(run.status for run in runs)
        run_count = len(runs)
        produced_mold_count = 0
        actual_hours = Decimal("0")
        progress_values = []
        efficiency_values = []
        now = timezone.now()
        for run in runs:
            logs = list(run.summary_logs)
            run_molds = sum(log.produced_mold_count for log in logs)
            produced_mold_count += run_molds

            if run.loaded_at:
                actual_start = max(
                    value
                    for value in [run.loaded_at, period_start]
                    if value is not None
                )
                actual_end = min(
                    value
                    for value in [run.unloaded_at or now, period_end]
                    if value is not None
                )
                run_actual_hours = Decimal(
                    str(max((actual_end - actual_start).total_seconds(), 0))
                ) / Decimal("3600")
            else:
                run_actual_hours = Decimal("0")
            actual_hours += run_actual_hours

            run_progress = (
                Decimal(run_molds)
                / Decimal(run.planned_mold_count)
                * Decimal("100")
                if run.planned_mold_count
                else Decimal("0")
            )
            progress_values.append(run_progress)
            if run_actual_hours > 0 and run.estimated_hours > 0 and run.planned_mold_count:
                earned_hours = (
                    run.estimated_hours
                    * Decimal(run_molds)
                    / Decimal(run.planned_mold_count)
                )
                efficiency_values.append(
                    earned_hours / run_actual_hours * Decimal("100")
                )
        good_quantity = sum(run.actual_good_quantity for run in settled_runs)
        defective_quantity = sum(
            run.actual_defective_quantity for run in settled_runs
        )
        material_kg = sum(
            (run.total_material_kg for run in settled_runs), Decimal("0")
        )
        revenue = sum((run.revenue for run in settled_runs), Decimal("0"))
        total_cost = sum((run.total_cost for run in settled_runs), Decimal("0"))
        profit = revenue - total_cost
        avg_progress = (
            sum(progress_values, Decimal("0")) / len(progress_values)
            if progress_values
            else Decimal("0")
        )
        avg_efficiency = (
            sum(efficiency_values, Decimal("0")) / len(efficiency_values)
            if efficiency_values
            else Decimal("0")
        )
        return Response(
            {
                "period": {
                    "date_from": date_from or None,
                    "date_to": date_to or None,
                    "group": group or None,
                },
                "run_count": run_count,
                "completed_run_count": status_counts.get(ProductionRun.Status.COMPLETED, 0),
                "settled_run_count": len(settled_runs),
                "unsettled_completed_run_count": sum(
                    1
                    for run in runs
                    if run.status == ProductionRun.Status.COMPLETED
                    and not run.is_settled
                ),
                "financial_basis": "settled_at",
                "planned_quantity": sum(run.order_quantity for run in runs),
                "produced_mold_count": produced_mold_count,
                "good_quantity": good_quantity,
                "defective_quantity": defective_quantity,
                "material_kg": _decimal_text(material_kg),
                "actual_hours": _decimal_text(actual_hours),
                "revenue": _decimal_text(revenue),
                "total_cost": _decimal_text(total_cost),
                "profit": _decimal_text(profit),
                "average_progress_percent": _decimal_text(avg_progress),
                "average_hourly_efficiency": _decimal_text(avg_efficiency),
                "status_counts": {
                    value: status_counts.get(value, 0) for value in ProductionRun.Status.values
                },
            }
        )


class ProductionMonthlyPerformanceView(APIView):
    @extend_schema(
        responses=dict,
        parameters=[
            OpenApiParameter(
                name="month",
                type=str,
                location=OpenApiParameter.QUERY,
                required=True,
                description="绩效月份，格式YYYY-MM",
            ),
            OpenApiParameter(
                name="group",
                type=str,
                location=OpenApiParameter.QUERY,
                required=False,
                description="可选机台组编号，例如A、B、C或自定义分组",
            ),
        ],
    )
    def get(self, request):
        month = str(request.query_params.get("month", "")).strip()
        try:
            month_start = datetime.strptime(month, "%Y-%m").date().replace(day=1)
        except ValueError as exc:
            raise DRFValidationError({"month": "月份格式应为YYYY-MM。"}) from exc
        if month_start.strftime("%Y-%m") != month:
            raise DRFValidationError({"month": "月份格式应为YYYY-MM。"})
        next_month = (
            month_start.replace(year=month_start.year + 1, month=1)
            if month_start.month == 12
            else month_start.replace(month=month_start.month + 1)
        )
        group = str(request.query_params.get("group", "")).strip().upper()

        logs = ProductionDailyLog.objects.filter(
            production_date__gte=month_start,
            production_date__lt=next_month,
        )
        if group:
            logs = logs.filter(run__station__group=group)
        grouped = (
            logs.values("operator")
            .annotate(
                total_mold_count=Coalesce(Sum("produced_mold_count"), 0),
                production_days=Count("production_date", distinct=True),
                participated_run_count=Count("run_id", distinct=True),
                production_seconds=Coalesce(
                    Sum(
                        F("produced_mold_count") * F("curing_seconds_snapshot"),
                        output_field=BigIntegerField(),
                    ),
                    0,
                ),
            )
            .order_by("-total_mold_count", "operator")
        )
        operators = []
        total_production_seconds = 0
        for item in grouped:
            total_mold_count = int(item["total_mold_count"] or 0)
            production_days = int(item["production_days"] or 0)
            production_hours = (
                Decimal(item["production_seconds"] or 0) / Decimal("3600")
            )
            total_production_seconds += int(item["production_seconds"] or 0)
            average_daily = (
                Decimal(total_mold_count) / Decimal(production_days)
                if production_days
                else Decimal("0")
            )
            operators.append(
                {
                    "operator": item["operator"],
                    "total_mold_count": total_mold_count,
                    "production_days": production_days,
                    "participated_run_count": int(item["participated_run_count"] or 0),
                    "average_daily_mold_count": _decimal_text(average_daily),
                    "production_hours": _decimal_text(production_hours),
                }
            )

        distinct_days = logs.values("production_date").distinct().count()
        distinct_runs = logs.values("run_id").distinct().count()
        total_molds = sum(item["total_mold_count"] for item in operators)
        total_hours = Decimal(total_production_seconds) / Decimal("3600")
        return Response(
            {
                "month": month,
                "group": group or None,
                "operators": operators,
                "totals": {
                    "operator_count": len(operators),
                    "total_mold_count": total_molds,
                    "production_days": distinct_days,
                    "operator_day_count": sum(
                        item["production_days"] for item in operators
                    ),
                    "participated_run_count": distinct_runs,
                    "production_hours": _decimal_text(total_hours),
                },
            }
        )


class ProductionImportTemplateView(APIView):
    @extend_schema(
        responses={(200, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"): bytes}
    )
    def get(self, request):
        response = HttpResponse(
            create_production_template(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = (
            "attachment; filename*=UTF-8''production-order-card-template.xlsx"
        )
        return response


class ProductionImportPreviewView(APIView):
    @extend_schema(request={"multipart/form-data": dict}, responses=dict)
    def post(self, request):
        uploaded_file = request.FILES.get("file")
        if uploaded_file is None:
            raise DRFValidationError({"file": "请选择Excel文件。"})
        if not uploaded_file.name.lower().endswith(".xlsx"):
            raise DRFValidationError({"file": "仅支持.xlsx文件。"})
        try:
            result = preview_production_workbook(uploaded_file, request.user)
        except (ValueError, KeyError, OSError, BadZipFile, InvalidFileException) as exc:
            raise DRFValidationError({"file": str(exc)}) from exc
        return Response(result)


class ProductionImportCommitView(APIView):
    @extend_schema(request=dict, responses=dict)
    def post(self, request):
        token = str(request.data.get("token", "")).strip()
        try:
            batch_id = UUID(token)
        except (ValueError, TypeError, AttributeError) as exc:
            raise DRFValidationError({"token": "无效的生产导入批次标识。"}) from exc
        batch = get_object_or_404(
            ProductionImportBatch, pk=batch_id, created_by=request.user
        )
        try:
            result = commit_production_batch(batch, request.user)
        except ValueError as exc:
            raise DRFValidationError({"detail": str(exc)}) from exc
        return Response(result)


class ProductionImportErrorReportView(APIView):
    @extend_schema(
        responses={(200, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"): bytes}
    )
    def get(self, request, token):
        batch = get_object_or_404(
            ProductionImportBatch, pk=token, created_by=request.user
        )
        response = HttpResponse(
            create_production_error_report(batch),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = (
            f"attachment; filename=production-import-errors-{batch.pk}.xlsx"
        )
        return response
