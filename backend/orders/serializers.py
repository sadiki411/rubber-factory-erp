from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.db.models import Sum
from rest_framework import serializers

from quality.models import QualityOrder

from .models import (
    BusinessRecordRevision,
    MaterialReceipt,
    ProductInspectionCriterion,
    ProductSpecification,
)
from .services import model_snapshot, record_revision


ZERO = Decimal("0")


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


class ProductSpecificationSerializer(AuditedModelSerializer):
    source_batch_id = serializers.UUIDField(read_only=True)

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
            "mold_no",
            "mold_size",
            "standard_hours",
            "notes",
            "normalized_key",
            "is_active",
            "source_batch_id",
            "source_sheet",
            "source_row",
            "source_key",
            "raw_data",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "normalized_key",
            "source_sheet",
            "source_row",
            "source_key",
            "raw_data",
            "created_at",
            "updated_at",
        ]


class ProductSpecificationSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductSpecification
        fields = [
            "id",
            "product_name",
            "customer_product_no",
            "specification",
            "material",
            "mold_no",
            "mold_size",
            "is_active",
        ]


class BusinessOrderSerializer(AuditedModelSerializer):
    source_batch_id = serializers.UUIDField(read_only=True)
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
            "process_card_status",
            "status",
            "status_display",
            "notes",
            "source_batch_id",
            "source_sheet",
            "source_row",
            "source_key",
            "raw_data",
            "created_by_name",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "source_sheet",
            "source_row",
            "source_key",
            "raw_data",
            "created_by_name",
            "created_at",
            "updated_at",
        ]

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
        count = int(obj.process_card_count or 0)
        covered = obj.process_card_covered_quantity
        if count <= 0 and not covered:
            return "NOT_RECEIVED"
        if covered is not None and int(covered) < int(obj.order_quantity or 0):
            return "PARTIAL"
        return "RECEIVED"


class OrderSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = QualityOrder
        fields = ["id", "order_no", "item_no", "specification", "material", "order_quantity"]


class MaterialReceiptSerializer(AuditedModelSerializer):
    order_no = serializers.CharField(required=False, allow_blank=True, max_length=100)
    source_batch_id = serializers.UUIDField(read_only=True)
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
            "manufactured_on",
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
