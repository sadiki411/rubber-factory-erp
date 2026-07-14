import copy
from calendar import monthrange
from datetime import date, datetime

from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from rest_framework import serializers

from molds.models import Machine
from molds.serializers import MachineSerializer
from production.models import ProductionStation
from quality.models import QualityEmployee
from quality.serializers import QualityEmployeeSerializer

from .models import ManualFinancialEntry, ManualPerformanceEntry


class DashboardQuerySerializer(serializers.Serializer):
    month = serializers.CharField(required=False, allow_blank=True)
    date_from = serializers.DateField(required=False)
    date_to = serializers.DateField(required=False)
    group = serializers.CharField(required=False, allow_blank=True)
    machine_id = serializers.IntegerField(required=False, min_value=1)

    def validate(self, attrs):
        raw_month = str(attrs.get("month", "") or "").strip()
        date_from = attrs.get("date_from")
        date_to = attrs.get("date_to")
        if raw_month and (date_from or date_to):
            raise serializers.ValidationError(
                {"month": "月份与自定义日期范围不能同时使用。"}
            )

        today = timezone.localdate()
        if raw_month:
            try:
                month_start = datetime.strptime(raw_month, "%Y-%m").date().replace(day=1)
            except ValueError as exc:
                raise serializers.ValidationError(
                    {"month": "月份格式应为YYYY-MM。"}
                ) from exc
            if month_start.strftime("%Y-%m") != raw_month:
                raise serializers.ValidationError({"month": "月份格式应为YYYY-MM。"})
            month_end = date(
                month_start.year,
                month_start.month,
                monthrange(month_start.year, month_start.month)[1],
            )
            date_from = month_start
            date_to = today if month_start <= today <= month_end else month_end
        else:
            date_from = date_from or (
                date_to.replace(day=1) if date_to else today.replace(day=1)
            )
            date_to = date_to or today

        if date_from > date_to:
            raise serializers.ValidationError(
                {"date_to": "结束日期不能早于开始日期。"}
            )
        if (date_to - date_from).days > 366:
            raise serializers.ValidationError(
                {"date_to": "单次分析的日期范围不能超过367天。"}
            )
        attrs["month"] = raw_month or None
        attrs["date_from"] = date_from
        attrs["date_to"] = date_to
        attrs["group"] = str(attrs.get("group", "") or "").strip().upper() or None
        if attrs["group"] and not ProductionStation.objects.filter(
            group__iexact=attrs["group"]
        ).exists():
            raise serializers.ValidationError({"group": "所选机台组不存在。"})
        if attrs.get("machine_id") and not Machine.objects.filter(
            pk=attrs["machine_id"]
        ).exists():
            raise serializers.ValidationError({"machine_id": "所选机台不存在。"})
        return attrs


class SoftVoidSerializerMixin:
    def get_created_by_name(self, obj) -> str:
        return obj.created_by.get_full_name() or obj.created_by.get_username()

    def get_voided_by_name(self, obj) -> str | None:
        if not obj.voided_by:
            return None
        return obj.voided_by.get_full_name() or obj.voided_by.get_username()

    @staticmethod
    def validate_candidate(candidate):
        try:
            candidate.clean()
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.message_dict) from exc


class ManualPerformanceEntrySerializer(
    SoftVoidSerializerMixin, serializers.ModelSerializer
):
    created_by_name = serializers.SerializerMethodField()
    voided_by_name = serializers.SerializerMethodField()
    machine = MachineSerializer(read_only=True)
    machine_id = serializers.PrimaryKeyRelatedField(
        source="machine",
        queryset=Machine.objects.filter(is_active=True),
        required=False,
        allow_null=True,
    )
    quality_employee = QualityEmployeeSerializer(read_only=True)
    quality_employee_id = serializers.PrimaryKeyRelatedField(
        source="quality_employee",
        queryset=QualityEmployee.objects.filter(is_active=True),
        required=False,
        allow_null=True,
    )
    entry_type_display = serializers.CharField(
        source="get_entry_type_display", read_only=True
    )
    reason_category_display = serializers.CharField(
        source="get_reason_category_display", read_only=True
    )

    class Meta:
        model = ManualPerformanceEntry
        fields = [
            "id",
            "entry_date",
            "entry_type",
            "entry_type_display",
            "staff_name",
            "order_no",
            "machine",
            "machine_id",
            "quality_employee",
            "quality_employee_id",
            "produced_mold_count",
            "production_hours",
            "inspection_quantity",
            "qualified_quantity",
            "defective_quantity",
            "shipped_quantity",
            "returned_quantity",
            "reason_category",
            "reason_category_display",
            "reworked_quantity",
            "recovered_quantity",
            "scrap_quantity",
            "rework_hours",
            "notes",
            "created_by_name",
            "voided_at",
            "voided_by_name",
            "void_reason",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "created_by_name",
            "voided_at",
            "voided_by_name",
            "void_reason",
            "created_at",
            "updated_at",
        ]

    def validate(self, attrs):
        candidate = copy.copy(self.instance) if self.instance else ManualPerformanceEntry()
        for field_name, value in attrs.items():
            setattr(candidate, field_name, value)
        self.validate_candidate(candidate)
        attrs["staff_name"] = candidate.staff_name
        attrs["order_no"] = candidate.order_no
        if candidate.entry_type == ManualPerformanceEntry.EntryType.REWORK:
            attrs["reason_category"] = candidate.reason_category
        return attrs


class ManualFinancialEntrySerializer(
    SoftVoidSerializerMixin, serializers.ModelSerializer
):
    created_by_name = serializers.SerializerMethodField()
    voided_by_name = serializers.SerializerMethodField()
    machine = MachineSerializer(read_only=True)
    machine_id = serializers.PrimaryKeyRelatedField(
        source="machine",
        queryset=Machine.objects.filter(is_active=True),
        required=False,
        allow_null=True,
    )
    direction_display = serializers.CharField(
        source="get_direction_display", read_only=True
    )
    category_display = serializers.CharField(
        source="get_category_display", read_only=True
    )
    profit_effect = serializers.DecimalField(
        max_digits=15, decimal_places=2, read_only=True
    )

    class Meta:
        model = ManualFinancialEntry
        fields = [
            "id",
            "occurred_on",
            "direction",
            "direction_display",
            "category",
            "category_display",
            "amount",
            "profit_effect",
            "machine",
            "machine_id",
            "staff_name",
            "order_no",
            "description",
            "notes",
            "created_by_name",
            "voided_at",
            "voided_by_name",
            "void_reason",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "profit_effect",
            "created_by_name",
            "voided_at",
            "voided_by_name",
            "void_reason",
            "created_at",
            "updated_at",
        ]

    def validate(self, attrs):
        candidate = copy.copy(self.instance) if self.instance else ManualFinancialEntry()
        for field_name, value in attrs.items():
            setattr(candidate, field_name, value)
        self.validate_candidate(candidate)
        attrs["staff_name"] = candidate.staff_name
        attrs["order_no"] = candidate.order_no
        attrs["description"] = candidate.description
        attrs["notes"] = candidate.notes
        return attrs
