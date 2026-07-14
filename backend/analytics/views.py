from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from production.models import ProductionStation

from .models import ManualFinancialEntry, ManualPerformanceEntry
from .serializers import (
    DashboardQuerySerializer,
    ManualFinancialEntrySerializer,
    ManualPerformanceEntrySerializer,
)
from .services import build_dashboard


class AnalyticsDashboardView(APIView):
    @extend_schema(parameters=[DashboardQuerySerializer], responses=dict)
    def get(self, request):
        query = DashboardQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        payload = build_dashboard(**query.validated_data)
        manual_entries = payload.pop("manual_entries")
        financial_entries = payload.pop("manual_financial_entries")
        payload["manual_entries"] = ManualPerformanceEntrySerializer(
            manual_entries, many=True, context={"request": request}
        ).data
        payload["manual_financial_entries"] = ManualFinancialEntrySerializer(
            financial_entries, many=True, context={"request": request}
        ).data
        return Response(payload)


class AnalyticsPagination(PageNumberPagination):
    page_size = 30
    page_size_query_param = "page_size"
    max_page_size = 1000


class SoftVoidViewSet(viewsets.ModelViewSet):
    pagination_class = AnalyticsPagination
    date_field = None
    search_fields = ()

    def base_queryset(self):
        raise NotImplementedError

    def get_queryset(self):
        queryset = self.base_queryset()
        params = self.request.query_params
        include_voided = str(params.get("include_voided", "")).strip().lower()
        if include_voided not in {"1", "true", "yes"}:
            queryset = queryset.filter(voided_at__isnull=True)

        if any(params.get(field) for field in ("month", "date_from", "date_to")):
            query = DashboardQuerySerializer(data=params)
            query.is_valid(raise_exception=True)
            queryset = queryset.filter(
                **{
                    f"{self.date_field}__gte": query.validated_data["date_from"],
                    f"{self.date_field}__lte": query.validated_data["date_to"],
                }
            )

        q = str(params.get("q", "") or "").strip()
        if q and self.search_fields:
            query_filter = Q()
            for field in self.search_fields:
                query_filter |= Q(**{f"{field}__icontains": q})
            queryset = queryset.filter(query_filter)
        return queryset

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def perform_update(self, serializer):
        if serializer.instance.voided_at:
            raise DRFValidationError({"detail": "已作废记录不能修改，请先恢复。"})
        serializer.save()

    def _void_entry(self, request, pk):
        entry = get_object_or_404(
            self.base_queryset().select_for_update(), pk=pk
        )
        if entry.voided_at is None:
            reason = str(
                request.data.get("void_reason", request.data.get("reason", "用户作废"))
                or "用户作废"
            ).strip()
            if len(reason) > 200:
                raise DRFValidationError({"void_reason": "作废原因最多200个字符。"})
            entry.voided_at = timezone.now()
            entry.voided_by = request.user
            entry.void_reason = reason
            entry.save(
                update_fields=["voided_at", "voided_by", "void_reason", "updated_at"]
            )
        return entry

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        self._void_entry(request, kwargs["pk"])
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"])
    @transaction.atomic
    def void(self, request, pk=None):
        entry = self._void_entry(request, pk)
        return Response(self.get_serializer(entry).data)

    @action(detail=True, methods=["post"])
    @transaction.atomic
    def restore(self, request, pk=None):
        entry = get_object_or_404(self.base_queryset().select_for_update(), pk=pk)
        if entry.voided_at is not None:
            entry.voided_at = None
            entry.voided_by = None
            entry.void_reason = ""
            entry.save(
                update_fields=["voided_at", "voided_by", "void_reason", "updated_at"]
            )
        return Response(self.get_serializer(entry).data)


class ManualPerformanceEntryViewSet(SoftVoidViewSet):
    serializer_class = ManualPerformanceEntrySerializer
    date_field = "entry_date"
    search_fields = ("staff_name", "order_no", "notes", "void_reason")

    def base_queryset(self):
        return ManualPerformanceEntry.objects.select_related(
            "machine", "quality_employee", "created_by", "voided_by"
        )

    def get_queryset(self):
        queryset = super().get_queryset()
        params = self.request.query_params
        production_filter = Q(
            entry_type=ManualPerformanceEntry.EntryType.PRODUCTION
        )
        machine_id = str(params.get("machine_id", "") or "").strip()
        if machine_id:
            if not machine_id.isdigit():
                raise DRFValidationError({"machine_id": "机台ID必须是整数。"})
            queryset = queryset.filter(
                ~production_filter | Q(machine_id=int(machine_id))
            )
        group = str(params.get("group", "") or "").strip().upper()
        if group:
            if not ProductionStation.objects.filter(group__iexact=group).exists():
                raise DRFValidationError({"group": "所选机台组不存在。"})
            queryset = queryset.filter(
                ~production_filter
                | Q(machine__production_station__group__iexact=group)
            )
        entry_type = str(params.get("entry_type", "") or "").strip().upper()
        if entry_type:
            if entry_type not in ManualPerformanceEntry.EntryType.values:
                raise DRFValidationError({"entry_type": "无效的绩效记录类型。"})
            queryset = queryset.filter(entry_type=entry_type)
        employee_id = str(params.get("quality_employee_id", "") or "").strip()
        if employee_id:
            if not employee_id.isdigit():
                raise DRFValidationError(
                    {"quality_employee_id": "员工ID必须是整数。"}
                )
            queryset = queryset.filter(quality_employee_id=int(employee_id))
        return queryset.order_by("-entry_date", "-id")


class ManualFinancialEntryViewSet(SoftVoidViewSet):
    serializer_class = ManualFinancialEntrySerializer
    date_field = "occurred_on"
    search_fields = (
        "staff_name",
        "order_no",
        "description",
        "notes",
        "void_reason",
    )

    def base_queryset(self):
        return ManualFinancialEntry.objects.select_related(
            "machine", "created_by", "voided_by"
        )

    def get_queryset(self):
        queryset = super().get_queryset()
        params = self.request.query_params
        machine_id = str(params.get("machine_id", "") or "").strip()
        if machine_id:
            if not machine_id.isdigit():
                raise DRFValidationError({"machine_id": "机台ID必须是整数。"})
            queryset = queryset.filter(machine_id=int(machine_id))
        group = str(params.get("group", "") or "").strip().upper()
        if group:
            if not ProductionStation.objects.filter(group__iexact=group).exists():
                raise DRFValidationError({"group": "所选机台组不存在。"})
            queryset = queryset.filter(
                machine__production_station__group__iexact=group
            )
        direction = str(params.get("direction", "") or "").strip().upper()
        if direction:
            if direction not in ManualFinancialEntry.Direction.values:
                raise DRFValidationError({"direction": "无效的收支方向。"})
            queryset = queryset.filter(direction=direction)
        category = str(params.get("category", "") or "").strip().upper()
        if category:
            if category not in ManualFinancialEntry.Category.values:
                raise DRFValidationError({"category": "无效的财务分类。"})
            queryset = queryset.filter(category=category)
        return queryset.order_by("-occurred_on", "-id")
