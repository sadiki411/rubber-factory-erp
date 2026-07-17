import copy
import math
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import serializers

from molds.models import MoldAsset, RackSlot
from molds.serializers import MachineSerializer
from orders.models import ProductSpecification
from quality.models import QualityOrder

from .models import (
    ProductionDailyLog,
    ProductionRun,
    ProductionSettlementRevision,
    ProductionStation,
    normalize_operator,
)
from .services import invalidate_settlement


class ProductionStationSerializer(serializers.ModelSerializer):
    machine = MachineSerializer(read_only=True)

    class Meta:
        model = ProductionStation
        fields = [
            "id",
            "code",
            "group",
            "position_no",
            "machine",
            "is_active",
            "created_at",
            "updated_at",
        ]


class ProductionMoldSerializer(serializers.ModelSerializer):
    model_code = serializers.CharField(source="mold_model.code", read_only=True)
    product_name = serializers.CharField(source="mold_model.product_name", read_only=True)

    class Meta:
        model = MoldAsset
        fields = ["id", "asset_code", "model_code", "product_name", "status"]


class ProductionDailyLogSerializer(serializers.ModelSerializer):
    date = serializers.DateField(source="production_date")

    class Meta:
        model = ProductionDailyLog
        fields = [
            "id",
            "date",
            "operator",
            "produced_mold_count",
            "notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at"]

    def validate(self, attrs):
        run = self.context.get("run") or getattr(self.instance, "run", None)
        production_date = attrs.get(
            "production_date", getattr(self.instance, "production_date", None)
        )
        operator = normalize_operator(
            attrs.get("operator", getattr(self.instance, "operator", ""))
        )
        if not operator:
            raise serializers.ValidationError({"operator": "作业员不能为空。"})
        attrs["operator"] = operator
        if run and production_date and operator:
            duplicate = ProductionDailyLog.objects.filter(
                run=run, production_date=production_date, operator=operator
            )
            if self.instance:
                duplicate = duplicate.exclude(pk=self.instance.pk)
            if duplicate.exists():
                raise serializers.ValidationError(
                    {"operator": "该作业员当天在此订单的生产记录已存在，请修改原记录。"}
                )

        if run and production_date:
            if run.status == ProductionRun.Status.PLANNED:
                raise serializers.ValidationError(
                    {"date": "待上机订单不能填写生产日报。"}
                )
            if run.status == ProductionRun.Status.CANCELLED and not run.loaded_at:
                raise serializers.ValidationError(
                    {"date": "未上模即取消的订单不能填写生产日报。"}
                )

            def local_date(value):
                if value is None:
                    return None
                if timezone.is_naive(value):
                    value = timezone.make_aware(
                        value, timezone.get_current_timezone()
                    )
                return timezone.localtime(value).date()

            loaded_date = local_date(run.loaded_at)
            unloaded_date = local_date(run.unloaded_at)
            if loaded_date and production_date < loaded_date:
                raise serializers.ValidationError(
                    {"date": "生产日期不能早于上模日期。"}
                )
            if (
                run.status == ProductionRun.Status.RUNNING
                and production_date > timezone.localdate()
            ):
                raise serializers.ValidationError(
                    {"date": "生产中订单的日报日期不能晚于今天。"}
                )
            if (
                run.status
                in (ProductionRun.Status.COMPLETED, ProductionRun.Status.CANCELLED)
                and unloaded_date
                and production_date > unloaded_date
            ):
                raise serializers.ValidationError(
                    {"date": "生产日期不能晚于下机日期。"}
                )

        produced = attrs.get(
            "produced_mold_count",
            getattr(self.instance, "produced_mold_count", 0),
        )
        if produced < 1:
            raise serializers.ValidationError(
                {"produced_mold_count": "生产模数必须大于0。"}
            )
        return attrs


class ProductionRunSerializer(serializers.ModelSerializer):
    station = ProductionStationSerializer(read_only=True)
    station_id = serializers.PrimaryKeyRelatedField(
        source="station", queryset=ProductionStation.objects.filter(is_active=True)
    )
    mold = ProductionMoldSerializer(read_only=True)
    mold_id = serializers.PrimaryKeyRelatedField(
        source="mold",
        queryset=MoldAsset.objects.filter(is_active=True).select_related("mold_model"),
        allow_null=True,
        required=False,
    )
    order_id = serializers.PrimaryKeyRelatedField(
        source="order",
        queryset=QualityOrder.objects.all(),
        allow_null=True,
        required=False,
    )
    product_specification_id = serializers.PrimaryKeyRelatedField(
        source="product_specification",
        queryset=ProductSpecification.objects.all(),
        allow_null=True,
        required=False,
    )
    daily_logs = ProductionDailyLogSerializer(many=True, read_only=True)
    produced_mold_count = serializers.IntegerField(read_only=True)
    good_quantity = serializers.IntegerField(read_only=True)
    defective_quantity = serializers.IntegerField(read_only=True)
    material_kg = serializers.DecimalField(
        max_digits=14, decimal_places=3, read_only=True
    )
    is_settled = serializers.BooleanField(read_only=True)
    actual_hours = serializers.DecimalField(
        max_digits=14, decimal_places=2, read_only=True
    )
    progress_percent = serializers.DecimalField(
        max_digits=12, decimal_places=2, read_only=True
    )
    remaining_mold_count = serializers.IntegerField(read_only=True)
    revenue = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    total_cost = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    profit = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    hourly_efficiency = serializers.DecimalField(
        max_digits=12, decimal_places=2, read_only=True
    )
    created_by_name = serializers.SerializerMethodField()
    settled_by_name = serializers.SerializerMethodField()

    class Meta:
        model = ProductionRun
        fields = [
            "id",
            "station",
            "station_id",
            "mold",
            "mold_id",
            "order_id",
            "product_specification_id",
            "order_no",
            "specification",
            "material",
            "order_quantity",
            "cavities",
            "estimated_defect_rate",
            "planned_mold_count",
            "compound_size",
            "strip_weight_kg",
            "strips_per_batch",
            "curing_seconds",
            "estimated_hours",
            "loaded_at",
            "expected_change_at",
            "material_changed_at",
            "unloaded_at",
            "status",
            "operator",
            "unit_price",
            "material_unit_price",
            "actual_good_quantity",
            "actual_defective_quantity",
            "total_material_kg",
            "labor_cost",
            "energy_cost",
            "other_cost",
            "settlement_notes",
            "is_settled",
            "settled_at",
            "settled_by_name",
            "notes",
            "daily_logs",
            "produced_mold_count",
            "good_quantity",
            "defective_quantity",
            "material_kg",
            "actual_hours",
            "progress_percent",
            "remaining_mold_count",
            "revenue",
            "total_cost",
            "profit",
            "hourly_efficiency",
            "created_by_name",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "actual_good_quantity",
            "actual_defective_quantity",
            "total_material_kg",
            "labor_cost",
            "energy_cost",
            "other_cost",
            "settlement_notes",
            "is_settled",
            "settled_at",
            "settled_by_name",
            "created_by_name",
            "created_at",
            "updated_at",
        ]
        extra_kwargs = {
            "planned_mold_count": {"required": False},
            "estimated_hours": {"required": False},
            "expected_change_at": {"required": False, "allow_null": True},
            "material_changed_at": {"required": False, "allow_null": True},
        }

    def get_created_by_name(self, obj) -> str:
        return obj.created_by.get_full_name() or obj.created_by.get_username()

    def get_settled_by_name(self, obj) -> str | None:
        if not obj.settled_by:
            return None
        return obj.settled_by.get_full_name() or obj.settled_by.get_username()

    def validate_estimated_defect_rate(self, value):
        if value < 0 or value > 100:
            raise serializers.ValidationError("预估不良率必须在0至100之间。")
        return value

    def validate(self, attrs):
        instance = self.instance

        if instance:
            requested_status = attrs.get("status", instance.status)
            allowed_status_changes = {
                (
                    ProductionRun.Status.PLANNED,
                    ProductionRun.Status.CANCELLED,
                ),
                (
                    ProductionRun.Status.RUNNING,
                    ProductionRun.Status.COMPLETED,
                ),
                (
                    ProductionRun.Status.RUNNING,
                    ProductionRun.Status.CANCELLED,
                ),
            }
            if (
                requested_status != instance.status
                and (instance.status, requested_status) not in allowed_status_changes
            ):
                raise serializers.ValidationError(
                    {
                        "status": (
                            "不允许通过普通编辑执行该状态变化；已开始或已结束的"
                            "订单不能退回待上机。"
                        )
                    }
                )
            if instance.status != ProductionRun.Status.PLANNED:
                if (
                    "station" in attrs
                    and attrs["station"].pk != instance.station_id
                ):
                    raise serializers.ValidationError(
                        {"station_id": "订单开始生产后不能更换机台。"}
                    )
                requested_mold = attrs.get("mold", instance.mold)
                requested_mold_id = requested_mold.pk if requested_mold else None
                if "mold" in attrs and requested_mold_id != instance.mold_id:
                    raise serializers.ValidationError(
                        {"mold_id": "订单开始生产后不能更换模具。"}
                    )
                requested_order = attrs.get("order", instance.order)
                requested_order_id = requested_order.pk if requested_order else None
                if "order" in attrs and requested_order_id != instance.order_id:
                    raise serializers.ValidationError(
                        {"order_id": "订单开始生产后不能更换关联订单明细。"}
                    )
                requested_specification = attrs.get(
                    "product_specification", instance.product_specification
                )
                requested_specification_id = (
                    requested_specification.pk if requested_specification else None
                )
                if (
                    "product_specification" in attrs
                    and requested_specification_id != instance.product_specification_id
                ):
                    raise serializers.ValidationError(
                        {"product_specification_id": "订单开始生产后不能更换关联产品规格。"}
                    )
            if (
                instance.status == ProductionRun.Status.PLANNED
                and attrs.get("loaded_at") is not None
            ):
                raise serializers.ValidationError(
                    {
                        "loaded_at": (
                            "待上机订单不能通过普通编辑填写上模时间，"
                            "请使用“确认上机”操作。"
                        )
                    }
                )

        def current(name, default=None):
            if name in attrs:
                return attrs[name]
            return getattr(instance, name, default) if instance else default

        order_quantity = current("order_quantity", 0) or 0
        cavities = current("cavities", 0) or 0
        defect_rate = current("estimated_defect_rate", Decimal("0")) or Decimal("0")
        if order_quantity < 1:
            raise serializers.ValidationError(
                {"order_quantity": "订单数量必须大于0。"}
            )
        if cavities < 1:
            raise serializers.ValidationError({"cavities": "模具孔数必须大于0。"})

        plan_drivers_changed = any(
            field in attrs
            for field in ("order_quantity", "cavities", "estimated_defect_rate")
        )
        if "planned_mold_count" not in attrs and (not instance or plan_drivers_changed):
            target_quantity = Decimal(order_quantity) * (
                Decimal("1") + Decimal(defect_rate) / Decimal("100")
            )
            attrs["planned_mold_count"] = max(
                math.ceil(target_quantity / Decimal(cavities)), 1
            )
        planned_mold_count = attrs.get(
            "planned_mold_count", current("planned_mold_count", 0)
        )
        if planned_mold_count < 1:
            raise serializers.ValidationError(
                {"planned_mold_count": "计划生产模数必须大于0。"}
            )

        curing_seconds = current("curing_seconds", 0) or 0
        hour_drivers_changed = any(
            field in attrs for field in ("planned_mold_count", "curing_seconds")
        ) or plan_drivers_changed
        should_calculate_hours = (
            (not instance and "estimated_hours" not in attrs)
            or (
                instance
                and "estimated_hours" not in attrs
                and hour_drivers_changed
            )
            or (
                "estimated_hours" in attrs
                and attrs.get("estimated_hours") == Decimal("0")
                and curing_seconds
            )
        )
        if should_calculate_hours:
            attrs["estimated_hours"] = (
                Decimal(planned_mold_count)
                * Decimal(curing_seconds)
                / Decimal("3600")
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        loaded_at = current("loaded_at")
        unloaded_at = current("unloaded_at")
        status_value = current("status", ProductionRun.Status.PLANNED)
        if "status" not in attrs:
            if not instance and unloaded_at:
                status_value = ProductionRun.Status.COMPLETED
                attrs["status"] = status_value
            elif (
                not instance
                and loaded_at
            ):
                status_value = ProductionRun.Status.RUNNING
                attrs["status"] = status_value
            elif (
                instance
                and instance.status == ProductionRun.Status.RUNNING
                and "unloaded_at" in attrs
                and unloaded_at
            ):
                status_value = ProductionRun.Status.COMPLETED
                attrs["status"] = status_value

        if (
            instance
            and instance.status != ProductionRun.Status.RUNNING
            and status_value == ProductionRun.Status.RUNNING
        ):
            raise serializers.ValidationError(
                {"status": "订单不能通过编辑直接改为生产中；待上机订单请使用“确认上机”操作。"}
            )
        if (
            instance
            and instance.status == ProductionRun.Status.PLANNED
            and status_value == ProductionRun.Status.COMPLETED
        ):
            raise serializers.ValidationError(
                {"status": "待上机订单必须先确认上机，不能直接标记为已完成。"}
            )

        estimated_hours = attrs.get(
            "estimated_hours", current("estimated_hours", Decimal("0"))
        ) or Decimal("0")
        expected_change_at = current("expected_change_at")
        explicit_expected_override = (
            "expected_change_at" in attrs and attrs["expected_change_at"] is not None
        )
        expected_drivers_changed = (
            instance is None
            or "loaded_at" in attrs
            or "estimated_hours" in attrs
        )
        if not loaded_at:
            if explicit_expected_override:
                raise serializers.ValidationError(
                    {"expected_change_at": "未填写上模时间时不能填写预计换模时间。"}
                )
            expected_change_at = None
            attrs["expected_change_at"] = None
        elif not explicit_expected_override and (
            expected_drivers_changed
            or expected_change_at is None
            or attrs.get("expected_change_at", object()) is None
        ):
            expected_change_at = loaded_at + timedelta(
                seconds=float(Decimal(estimated_hours) * Decimal("3600"))
            )
            attrs["expected_change_at"] = expected_change_at
        if loaded_at and expected_change_at and expected_change_at < loaded_at:
            raise serializers.ValidationError(
                {"expected_change_at": "预计换模时间不能早于上模时间。"}
            )

        candidate = copy.copy(instance) if instance else ProductionRun()
        for field_name in [
            "station",
            "mold",
            "order",
            "product_specification",
            "order_no",
            "specification",
            "material",
            "order_quantity",
            "cavities",
            "estimated_defect_rate",
            "planned_mold_count",
            "compound_size",
            "strip_weight_kg",
            "strips_per_batch",
            "curing_seconds",
            "estimated_hours",
            "loaded_at",
            "expected_change_at",
            "material_changed_at",
            "unloaded_at",
            "status",
            "operator",
            "unit_price",
            "material_unit_price",
            "notes",
        ]:
            if field_name in attrs:
                setattr(candidate, field_name, attrs[field_name])
        invalidating_fields = ("cavities", "unit_price", "material_unit_price")
        if instance and instance.settled_at and any(
            field in attrs and attrs[field] != getattr(instance, field)
            for field in invalidating_fields
        ):
            candidate.settled_at = None
            candidate.settled_by = None
        try:
            candidate.clean()
        except DjangoValidationError as exc:
            details = exc.message_dict
            if "station" in details:
                details["station_id"] = details.pop("station")
            if "mold" in details:
                details["mold_id"] = details.pop("mold")
            raise serializers.ValidationError(details) from exc

        if status_value in ProductionRun.ACTIVE_STATUSES:
            station = current("station")
            mold = current("mold")
            active = ProductionRun.objects.filter(status__in=ProductionRun.ACTIVE_STATUSES)
            if instance:
                active = active.exclude(pk=instance.pk)
            if station and active.filter(station=station).exists():
                raise serializers.ValidationError(
                    {"station_id": "该机台已有待上机或生产中的订单。"}
                )
            if mold and active.filter(mold=mold).exists():
                raise serializers.ValidationError(
                    {"mold_id": "该模具已用于另一条未结束的生产订单。"}
                )
        station = current("station")
        order_no = current("order_no", "")
        duplicate_order = ProductionRun.objects.filter(
            station=station, order_no=order_no
        )
        if instance:
            duplicate_order = duplicate_order.exclude(pk=instance.pk)
        if station and order_no and duplicate_order.exists():
            raise serializers.ValidationError(
                {"order_no": "该机台已存在相同订单号的生产记录。"}
            )
        return attrs

    def _has_explicit_expected_override(self):
        raw = self.initial_data.get("expected_change_at") if self.initial_data else None
        return raw not in (None, "")

    def create(self, validated_data):
        instance = ProductionRun(**validated_data)
        instance._preserve_expected_change = self._has_explicit_expected_override()
        try:
            with transaction.atomic():
                instance.station = ProductionStation.objects.select_for_update().get(
                    pk=instance.station_id
                )
                if instance.mold_id:
                    locked_mold = (
                        MoldAsset.objects.select_for_update()
                        .filter(pk=instance.mold_id)
                        .first()
                    )
                    if locked_mold is None or not locked_mold.is_active:
                        raise serializers.ValidationError(
                            {"mold_id": "所选模具已删除，请刷新后重新选择。"}
                        )
                    instance.mold = locked_mold
                instance.full_clean()
                instance.save()
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.message_dict) from exc
        except IntegrityError as exc:
            raise serializers.ValidationError(
                {"detail": "生产记录与现有机台、模具或订单发生冲突，请刷新后重试。"}
            ) from exc
        return instance

    def update(self, instance, validated_data):
        try:
            with transaction.atomic():
                instance = ProductionRun.objects.select_for_update().get(pk=instance.pk)
                original_mold_id = instance.mold_id
                invalidating_fields = (
                    "cavities",
                    "unit_price",
                    "material_unit_price",
                )
                invalidates_settlement = bool(
                    instance.settled_at
                    and any(
                        field in validated_data
                        and validated_data[field] != getattr(instance, field)
                        for field in invalidating_fields
                    )
                )
                if invalidates_settlement:
                    request = self.context.get("request")
                    invalidate_settlement(instance, request.user)
                for attribute, value in validated_data.items():
                    setattr(instance, attribute, value)
                instance._preserve_expected_change = (
                    self._has_explicit_expected_override()
                )
                instance.station = ProductionStation.objects.select_for_update().get(
                    pk=instance.station_id
                )
                mold_ids = {
                    mold_id
                    for mold_id in [original_mold_id, instance.mold_id]
                    if mold_id
                }
                locked_molds = {}
                if mold_ids:
                    locked_molds = {
                        mold.pk: mold
                        for mold in MoldAsset.objects.select_for_update()
                        .filter(pk__in=mold_ids)
                        .order_by("pk")
                    }
                if instance.mold_id:
                    locked_mold = locked_molds.get(instance.mold_id)
                    if locked_mold is None:
                        raise serializers.ValidationError(
                            {"mold_id": "所选模具不存在，请刷新后重新选择。"}
                        )
                    instance.mold = locked_mold
                    if not instance.mold.is_active and (
                        instance.mold_id != original_mold_id
                        or instance.status in ProductionRun.ACTIVE_STATUSES
                    ):
                        raise serializers.ValidationError(
                            {"mold_id": "所选模具已删除，请刷新后重新选择。"}
                        )
                instance.full_clean()
                instance.save()
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.message_dict) from exc
        except IntegrityError as exc:
            raise serializers.ValidationError(
                {"detail": "生产记录与现有机台、模具或订单发生冲突，请刷新后重试。"}
            ) from exc
        return instance


class StartProductionRunSerializer(serializers.Serializer):
    loaded_at = serializers.DateTimeField(required=False, allow_null=True)
    note = serializers.CharField(required=False, allow_blank=True, max_length=1000)
    confirm_warnings = serializers.BooleanField(required=False, default=False)


class CompleteProductionRunSerializer(serializers.Serializer):
    unloaded_at = serializers.DateTimeField(required=False)


class CompleteAndPutawayProductionRunSerializer(serializers.Serializer):
    slot_id = serializers.PrimaryKeyRelatedField(
        source="slot",
        queryset=RackSlot.objects.select_related("zone__level__rack"),
    )
    unloaded_at = serializers.DateTimeField(required=False)
    note = serializers.CharField(required=False, allow_blank=True, max_length=1000)
    confirm_warnings = serializers.BooleanField(required=False, default=False)


class ProductionSettlementSerializer(serializers.Serializer):
    actual_good_quantity = serializers.IntegerField(min_value=0)
    actual_defective_quantity = serializers.IntegerField(min_value=0)
    total_material_kg = serializers.DecimalField(
        max_digits=14, decimal_places=3, min_value=Decimal("0")
    )
    labor_cost = serializers.DecimalField(
        max_digits=14, decimal_places=2, min_value=Decimal("0")
    )
    energy_cost = serializers.DecimalField(
        max_digits=14, decimal_places=2, min_value=Decimal("0")
    )
    other_cost = serializers.DecimalField(
        max_digits=14, decimal_places=2, min_value=Decimal("0")
    )
    settlement_notes = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        run = self.context["run"]
        may_settle = run.status == ProductionRun.Status.COMPLETED or (
            run.status == ProductionRun.Status.CANCELLED and run.loaded_at is not None
        )
        if not may_settle:
            raise serializers.ValidationError(
                {"detail": "只有已完成或已上模后取消的订单可以结算。"}
            )
        expected_quantity = run.produced_mold_count * run.cavities
        actual_quantity = (
            attrs["actual_good_quantity"] + attrs["actual_defective_quantity"]
        )
        if actual_quantity != expected_quantity:
            raise serializers.ValidationError(
                {
                    "actual_good_quantity": (
                        f"实际良品与实际不良之和必须等于累计生产模数乘以模具孔数"
                        f"（当前应为{expected_quantity}件）。"
                    )
                }
            )
        return attrs


class ProductionSettlementRevisionSerializer(serializers.ModelSerializer):
    changed_by_name = serializers.SerializerMethodField()

    class Meta:
        model = ProductionSettlementRevision
        fields = [
            "revision_no",
            "action",
            "cavities",
            "produced_mold_count",
            "unit_price",
            "material_unit_price",
            "actual_good_quantity",
            "actual_defective_quantity",
            "total_material_kg",
            "labor_cost",
            "energy_cost",
            "other_cost",
            "settlement_notes",
            "changed_by_name",
            "changed_at",
        ]

    def get_changed_by_name(self, obj) -> str:
        return obj.changed_by.get_full_name() or obj.changed_by.get_username()


class ProductionSettlementDetailSerializer(serializers.Serializer):
    run = ProductionRunSerializer(read_only=True)
    revisions = ProductionSettlementRevisionSerializer(many=True, read_only=True)
