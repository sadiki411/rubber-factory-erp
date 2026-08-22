import re
from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.db.models import Sum
from rest_framework import serializers

from molds.models import MoldModel
from quality.models import QualityOrder

from .models import (
    BusinessRecordRevision,
    MaterialReceipt,
    ProductInspectionCriterion,
    ProductSpecification,
)
from .services import model_snapshot, order_identity_exists, record_revision


ZERO = Decimal("0")


def _latest_product_unit_weight(instance):
    """Return the last active finished-piece weight saved in ERP."""
    # Import lazily: quality models already point at orders.ProductSpecification
    # and importing them at module load would create an app-import cycle.
    from quality.models import ProductUnitWeight

    return (
        ProductUnitWeight.objects.filter(
            product_specification_id=instance.pk,
            is_active=True,
            unit_weight_g__gt=0,
        )
        # ``measured_on`` may intentionally be a historical shipment date.
        # The last value saved/confirmed is nevertheless the value operators
        # expect to see pre-filled on the next shipment.
        .order_by("-created_at", "-id")
        .first()
    )


def _validation_details(exc):
    if hasattr(exc, "message_dict"):
        return exc.message_dict
    return {"detail": exc.messages}


def _request_user(serializer):
    request = serializer.context.get("request")
    return getattr(request, "user", None)


class AuditedModelSerializer(serializers.ModelSerializer):
    record_type = None
    conflict_message = "数据与现有记录冲突，请刷新后重试。"

    def create(self, validated_data):
        instance = self.Meta.model(**validated_data)
        try:
            with transaction.atomic():
                instance.save()
                record_revision(
                    instance,
                    _request_user(self),
                    BusinessRecordRevision.Action.CREATE,
                )
        except DjangoValidationError as exc:
            raise serializers.ValidationError(_validation_details(exc)) from exc
        except IntegrityError as exc:
            raise serializers.ValidationError({"detail": self.conflict_message}) from exc
        return instance

    def update(self, instance, validated_data):
        try:
            with transaction.atomic():
                instance = self.Meta.model.objects.select_for_update().get(pk=instance.pk)
                before = model_snapshot(instance)
                was_active = getattr(instance, "is_active", None)
                for field, value in validated_data.items():
                    setattr(instance, field, value)
                instance.save()
                action = BusinessRecordRevision.Action.UPDATE
                if was_active is True and getattr(instance, "is_active", None) is False:
                    action = BusinessRecordRevision.Action.DEACTIVATE
                record_revision(instance, _request_user(self), action, before=before)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(_validation_details(exc)) from exc
        except IntegrityError as exc:
            raise serializers.ValidationError({"detail": self.conflict_message}) from exc
        return instance


class MoldModelSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = MoldModel
        fields = ["id", "code", "product_name", "is_active"]


class ProductSpecificationSerializer(AuditedModelSerializer):
    source_batch_id = serializers.UUIDField(read_only=True)
    latest_unit_weight_g = serializers.SerializerMethodField()
    latest_unit_weight_measured_on = serializers.SerializerMethodField()
    unit_weight_history_count = serializers.SerializerMethodField()
    mold_model = MoldModelSummarySerializer(read_only=True)
    mold_model_id = serializers.PrimaryKeyRelatedField(
        source="mold_model",
        queryset=MoldModel.objects.all(),
        required=False,
        allow_null=True,
    )

    def validate_mold_model_id(self, value):
        if value is None or value.is_active:
            return value
        if self.instance is not None and self.instance.mold_model_id == value.pk:
            return value
        raise serializers.ValidationError("只能新关联已启用的模具型号。")

    class Meta:
        model = ProductSpecification
        fields = [
            "id",
            "product_name",
            "customer_product_no",
            "specification",
            "material",
            "material_length",
            "cut_weight",
            "strip_count",
            "primary_curing",
            "secondary_curing",
            "total_cavities",
            "effective_cavities",
            "mold_in_stock",
            "mold_model",
            "mold_model_id",
            "mold_no",
            "mold_size",
            "notes",
            "normalized_key",
            "is_active",
            "source_batch_id",
            "source_sheet",
            "source_row",
            "source_key",
            "raw_data",
            "latest_unit_weight_g",
            "latest_unit_weight_measured_on",
            "unit_weight_history_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "normalized_key",
            "source_sheet",
            "source_row",
            "source_key",
            "raw_data",
            "latest_unit_weight_g",
            "latest_unit_weight_measured_on",
            "unit_weight_history_count",
            "created_at",
            "updated_at",
        ]

    def get_latest_unit_weight_g(self, obj) -> str | None:
        weight = _latest_product_unit_weight(obj)
        return str(weight.unit_weight_g) if weight else None

    def get_latest_unit_weight_measured_on(self, obj) -> str | None:
        weight = _latest_product_unit_weight(obj)
        return weight.measured_on.isoformat() if weight and weight.measured_on else None

    def get_unit_weight_history_count(self, obj) -> int:
        from quality.models import ProductUnitWeight

        return ProductUnitWeight.objects.filter(product_specification_id=obj.pk).count()


class ProductSpecificationSummarySerializer(serializers.ModelSerializer):
    latest_unit_weight_g = serializers.SerializerMethodField()
    latest_unit_weight_measured_on = serializers.SerializerMethodField()
    mold_model = MoldModelSummarySerializer(read_only=True)
    mold_model_id = serializers.IntegerField(read_only=True)

    class Meta:
        model = ProductSpecification
        fields = [
            "id",
            "product_name",
            "customer_product_no",
            "specification",
            "material",
            "mold_model",
            "mold_model_id",
            "mold_no",
            "mold_size",
            "is_active",
            "latest_unit_weight_g",
            "latest_unit_weight_measured_on",
        ]

    def get_latest_unit_weight_g(self, obj) -> str | None:
        weight = _latest_product_unit_weight(obj)
        return str(weight.unit_weight_g) if weight else None

    def get_latest_unit_weight_measured_on(self, obj) -> str | None:
        weight = _latest_product_unit_weight(obj)
        return weight.measured_on.isoformat() if weight and weight.measured_on else None


class BusinessOrderSerializer(AuditedModelSerializer):
    source_batch_id = serializers.UUIDField(read_only=True)
    last_source_batch_id = serializers.UUIDField(read_only=True)
    product_specification = ProductSpecificationSummarySerializer(read_only=True)
    product_specification_id = serializers.PrimaryKeyRelatedField(
        source="product_specification",
        queryset=ProductSpecification.objects.filter(is_active=True),
        required=False,
        allow_null=True,
    )
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    created_by_name = serializers.SerializerMethodField()
    imported_received_material_kg = serializers.SerializerMethodField()
    received_material_kg = serializers.SerializerMethodField()
    material_gap_kg = serializers.SerializerMethodField()
    material_status = serializers.SerializerMethodField()
    process_card_status = serializers.SerializerMethodField()
    last_data_updated_at = serializers.SerializerMethodField()
    weighted_shipped_quantity = serializers.SerializerMethodField()
    weighted_remaining_quantity = serializers.SerializerMethodField()
    shipment_status = serializers.SerializerMethodField()

    class Meta:
        model = QualityOrder
        fields = [
            "id",
            "order_no",
            "item_no",
            "batch_no",
            "product_code",
            "product_name",
            "specification",
            "material",
            "product_specification",
            "product_specification_id",
            "order_quantity",
            "order_date",
            "due_date",
            "mold_size",
            "forming_hours",
            "production_required",
            "legacy_shipment_text",
            "required_material_kg",
            "manual_received_material_kg",
            "imported_received_material_kg",
            "received_material_kg",
            "material_gap_kg",
            "material_status",
            "process_card_count",
            "process_card_covered_quantity",
            "process_card_text",
            "process_card_status",
            "production_quantity",
            "shipment_date",
            "shipped_quantity",
            "status",
            "status_display",
            "notes",
            "source_batch_id",
            "last_source_batch_id",
            "source_sheet",
            "source_row",
            "source_key",
            "source_system",
            "external_key",
            "source_document_at",
            "last_imported_at",
            "raw_data",
            "last_data_updated_at",
            "weighted_shipped_quantity",
            "weighted_remaining_quantity",
            "shipment_status",
            "created_by_name",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "source_sheet",
            "source_row",
            "source_key",
            "source_system",
            "external_key",
            "source_document_at",
            "last_imported_at",
            "raw_data",
            "last_data_updated_at",
            "created_by_name",
            "created_at",
            "updated_at",
        ]

    def validate(self, attrs):
        attrs = super().validate(attrs)
        order_no = attrs.get("order_no", getattr(self.instance, "order_no", ""))
        item_no = attrs.get("item_no", getattr(self.instance, "item_no", ""))
        if order_identity_exists(
            order_no,
            item_no,
            exclude_pk=getattr(self.instance, "pk", None),
        ):
            raise serializers.ValidationError(
                {"item_no": "订单号和项次已存在；同一订单号可使用不同项次。"}
            )
        return attrs

    def get_created_by_name(self, obj) -> str:
        return obj.created_by.get_full_name() or obj.created_by.get_username()

    def _imported(self, obj):
        annotated = getattr(obj, "imported_received_material_kg_value", None)
        if annotated is not None:
            return Decimal(annotated or 0)
        return Decimal(obj.material_receipts.aggregate(total=Sum("weight_kg"))["total"] or 0)

    def get_imported_received_material_kg(self, obj) -> str:
        return format(self._imported(obj).quantize(Decimal("0.001")), "f")

    def _received(self, obj):
        return self._imported(obj) + Decimal(obj.manual_received_material_kg or 0)

    def get_received_material_kg(self, obj) -> str:
        return format(self._received(obj).quantize(Decimal("0.001")), "f")

    def get_material_gap_kg(self, obj) -> str | None:
        if obj.required_material_kg is None:
            return None
        gap = max(Decimal(obj.required_material_kg) - self._received(obj), ZERO)
        return format(gap.quantize(Decimal("0.001")), "f")

    def get_material_status(self, obj) -> str:
        required = obj.required_material_kg
        if required is None:
            return "UNKNOWN"
        received = self._received(obj)
        required = Decimal(required)
        if received <= ZERO:
            return "NOT_RECEIVED"
        if received < required:
            return "PARTIAL"
        if received == required:
            return "SUFFICIENT"
        return "OVER"

    def get_process_card_status(self, obj) -> str:
        count_value = obj.process_card_count
        covered = obj.process_card_covered_quantity
        if count_value is not None or covered is not None:
            count = int(count_value or 0)
            if count <= 0 and (covered is None or int(covered) <= 0):
                return "NOT_RECEIVED"
            if covered is not None and int(covered) < int(obj.order_quantity or 0):
                return "PARTIAL"
            return "RECEIVED"

        text = re.sub(r"[\s:：,，;；。]+", "", str(obj.process_card_text or "").casefold())
        if not text:
            return "NOT_RECEIVED"
        if (
            text in {"无", "否", "没有", "未收到", "未提供", "未发", "0", "no", "n", "false"}
            or text.startswith(("没有", "未收到", "未提供", "未发", "无流程卡"))
        ):
            return "NOT_RECEIVED"
        quantity_match = re.search(r"(\d+)张", text)
        if quantity_match:
            return "RECEIVED" if int(quantity_match.group(1)) > 0 else "NOT_RECEIVED"
        if text in {"有", "是", "已收到", "已提供", "已发", "收到", "yes", "y", "true"}:
            return "RECEIVED"
        if text.startswith(("有流程卡", "已收到", "已提供", "已发")):
            return "RECEIVED"
        return "NOT_RECEIVED"

    def get_last_data_updated_at(self, obj) -> str | None:
        value = getattr(obj, "last_data_updated_at_value", None) or obj.updated_at
        return serializers.DateTimeField().to_representation(value) if value else None

    def get_weighted_shipped_quantity(self, obj) -> int:
        # Despite the legacy field name, this is the canonical delivered
        # quantity used everywhere: confirmed weighted rows plus historical
        # shipments, less valid customer returns.
        return self._delivered_quantity(obj)

    def _delivered_quantity(self, obj) -> int:
        from quality.services import delivered_quantities_by_order

        supplied = self.context.get("delivered_quantities_by_order")
        if supplied is not None and obj.pk in supplied:
            return int(supplied[obj.pk] or 0)
        cache = self.context.setdefault("_order_delivered_quantity_cache", {})
        if obj.pk not in cache:
            cache[obj.pk] = delivered_quantities_by_order([obj.pk]).get(obj.pk, 0)
        return int(cache[obj.pk] or 0)

    def get_weighted_remaining_quantity(self, obj) -> int:
        return max(int(obj.order_quantity or 0) - self._delivered_quantity(obj), 0)

    def get_shipment_status(self, obj) -> str:
        if obj.status == QualityOrder.Status.CANCELLED:
            return "CANCELLED"
        shipped = self._delivered_quantity(obj)
        if shipped <= 0:
            return "UNSHIPPED"
        if shipped >= int(obj.order_quantity or 0):
            return "SHIPPED"
        return "PARTIAL"


class OrderSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = QualityOrder
        fields = ["id", "order_no", "item_no", "specification", "material", "order_quantity"]


class MaterialReceiptSerializer(AuditedModelSerializer):
    order_no = serializers.CharField(required=False, allow_blank=True, max_length=100)
    source_batch_id = serializers.UUIDField(read_only=True)
    last_source_batch_id = serializers.UUIDField(read_only=True)
    order = OrderSummarySerializer(read_only=True)
    order_id = serializers.PrimaryKeyRelatedField(
        source="order", queryset=QualityOrder.objects.all(), required=False, allow_null=True
    )

    class Meta:
        model = MaterialReceipt
        fields = [
            "id",
            "order",
            "order_id",
            "order_no",
            "item_no",
            "finished_product_name",
            "specification",
            "material",
            "batch_no",
            "sheet_size",
            "weight_kg",
            "issued_on",
            "manufactured_on",
            "source_batch_id",
            "last_source_batch_id",
            "source_sheet",
            "source_row",
            "source_key",
            "source_system",
            "external_key",
            "source_document_at",
            "last_imported_at",
            "raw_data",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "source_sheet",
            "source_row",
            "source_key",
            "source_system",
            "external_key",
            "source_document_at",
            "last_imported_at",
            "raw_data",
            "created_at",
            "updated_at",
        ]

    def validate(self, attrs):
        order = attrs.get("order") or getattr(self.instance, "order", None)
        order_no = attrs.get("order_no", getattr(self.instance, "order_no", ""))
        item_no = attrs.get("item_no", getattr(self.instance, "item_no", ""))
        if order and order_no and order.order_no != order_no:
            raise serializers.ValidationError(
                {"order_id": "关联订单与收料记录中的订单号不一致。"}
            )
        if order and order.item_no and item_no and order.item_no != item_no:
            raise serializers.ValidationError(
                {"order_id": "关联订单与收料记录中的项次不一致。"}
            )
        if order and not order_no:
            attrs["order_no"] = order.order_no
        if order and order.item_no and not item_no:
            attrs["item_no"] = order.item_no
        return attrs


class ProductInspectionCriterionSerializer(AuditedModelSerializer):
    source_batch_id = serializers.UUIDField(read_only=True)
    product_specification = ProductSpecificationSummarySerializer(read_only=True)
    product_specification_id = serializers.PrimaryKeyRelatedField(
        source="product_specification", queryset=ProductSpecification.objects.all()
    )
    order = OrderSummarySerializer(read_only=True)
    order_id = serializers.PrimaryKeyRelatedField(
        source="order", queryset=QualityOrder.objects.all(), required=False, allow_null=True
    )

    class Meta:
        model = ProductInspectionCriterion
        fields = [
            "id",
            "product_specification",
            "product_specification_id",
            "order",
            "order_id",
            "item_no",
            "project_no",
            "customer",
            "category",
            "version",
            "inspection_item",
            "lower_limit",
            "upper_limit",
            "unit",
            "source_batch_id",
            "source_sheet",
            "source_row",
            "source_key",
            "raw_data",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "source_sheet",
            "source_row",
            "source_key",
            "raw_data",
            "created_at",
            "updated_at",
        ]


class BusinessRecordRevisionSerializer(serializers.ModelSerializer):
    source_batch_id = serializers.UUIDField(read_only=True)
    operator_name = serializers.SerializerMethodField()
    action_display = serializers.CharField(source="get_action_display", read_only=True)

    class Meta:
        model = BusinessRecordRevision
        fields = [
            "id",
            "record_type",
            "record_id",
            "action",
            "action_display",
            "snapshot",
            "changes",
            "source_batch_id",
            "operator_name",
            "created_at",
        ]

    def get_operator_name(self, obj):
        return obj.operator.get_full_name() or obj.operator.get_username()
