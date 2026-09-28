from decimal import Decimal

from django.db import transaction
from rest_framework import serializers

from .models import (
    InventoryBatch,
    InventoryContainer,
    InventoryLocation,
    InventoryOutbound,
    InventoryOutboundLine,
    InventoryProduct,
    InventoryTransaction,
    MaterialRemainder,
    MaterialRemainderUse,
)
from .services import create_inventory_outbound, create_inventory_receipt
from quality.models import QualityEmployee
from quality.unit_weights import latest_saved_product_unit_weight


class InventoryProductSerializer(serializers.ModelSerializer):
    latest_quality_unit_weight_g = serializers.SerializerMethodField()
    effective_unit_weight_g = serializers.SerializerMethodField()

    class Meta:
        model = InventoryProduct
        fields = [
            "id", "product_code", "product_name", "specification", "material",
            "unit_weight_g", "latest_quality_unit_weight_g", "effective_unit_weight_g",
            "product_specification", "is_active", "notes",
            "created_at", "updated_at",
        ]

    def get_latest_quality_unit_weight_g(self, obj):
        # Product list queries annotate this field in one database query.  Nested
        # product serializers (locations/containers/batches) deliberately avoid
        # looking it up row-by-row, which would make the inventory screen issue
        # hundreds of extra queries as the warehouse grows.
        if not hasattr(obj, "latest_quality_unit_weight_g"):
            return None
        annotated = getattr(obj, "latest_quality_unit_weight_g", None)
        if annotated is not None:
            return str(Decimal(annotated).quantize(Decimal("0.00001")))
        latest = latest_saved_product_unit_weight(
            product_specification_id=obj.product_specification_id,
            specification=obj.specification,
            material=obj.material,
        )
        return str(latest.unit_weight_g.quantize(Decimal("0.00001"))) if latest else None

    def get_effective_unit_weight_g(self, obj):
        latest = self.get_latest_quality_unit_weight_g(obj)
        if latest is not None:
            return latest
        return str(obj.unit_weight_g.quantize(Decimal("0.00001"))) if obj.unit_weight_g is not None else None


class InventoryLocationSerializer(serializers.ModelSerializer):
    container = serializers.SerializerMethodField()

    class Meta:
        model = InventoryLocation
        fields = [
            "id", "code", "rack_code", "rack_type", "level_no", "position_no",
            "label", "is_active", "allows_basket", "allows_bag", "container",
        ]

    def get_container(self, obj):
        container = getattr(obj, "inventory_container", None)
        if not container:
            return None
        return {
            "id": container.pk,
            "container_code": container.container_code,
            "container_type": container.container_type,
            "batch_no": container.batch.batch_no,
            "product_id": container.batch.product_id,
            "product_name": container.batch.product.product_name,
            "product_code": container.batch.product.product_code,
            "specification": container.batch.product.specification,
            "material": container.batch.product.material,
            "quantity": container.quantity,
            "quality_status": container.batch.quality_status,
            "bag_count": container.bag_count,
            "pieces_per_bag": container.pieces_per_bag,
            "inspector_name": (
                container.batch.inspector.name if container.batch.inspector_id else ""
            ),
            "received_on": container.batch.received_on,
            "unit_weight_g": container.batch.product.unit_weight_g,
        }


class InventoryBatchSerializer(serializers.ModelSerializer):
    product = InventoryProductSerializer(read_only=True)
    total_quantity = serializers.SerializerMethodField()
    total_containers = serializers.SerializerMethodField()

    class Meta:
        model = InventoryBatch
        fields = [
            "id", "batch_no", "product", "received_on", "source_type", "source_note",
            "quality_status", "inspector", "inspected_on", "notes", "total_quantity",
            "total_containers", "created_at", "updated_at",
        ]

    def get_total_quantity(self, obj):
        return sum(item.quantity for item in obj.containers.filter(status=InventoryContainer.Status.ACTIVE))

    def get_total_containers(self, obj):
        return obj.containers.filter(status=InventoryContainer.Status.ACTIVE).count()


class InventoryReceiptSerializer(serializers.Serializer):
    product_id = serializers.IntegerField(required=False, allow_null=True)
    product = InventoryProductSerializer(required=False)
    unit_weight_g = serializers.DecimalField(
        max_digits=14, decimal_places=5, required=False, allow_null=True,
        min_value=Decimal("0.00001"),
    )
    batch_no = serializers.CharField(required=False, allow_blank=True, max_length=80)
    received_on = serializers.DateField(required=False)
    source_type = serializers.ChoiceField(choices=InventoryBatch.SourceType.choices, default=InventoryBatch.SourceType.MANUAL)
    source_note = serializers.CharField(required=False, allow_blank=True, max_length=500)
    quality_status = serializers.ChoiceField(choices=InventoryBatch.QualityStatus.choices, default=InventoryBatch.QualityStatus.WAITING)
    inspector_id = serializers.IntegerField(required=False, allow_null=True)
    inspected_on = serializers.DateField(required=False, allow_null=True)
    notes = serializers.CharField(required=False, allow_blank=True)
    container_code = serializers.CharField(required=False, allow_blank=True, max_length=80)
    container_type = serializers.ChoiceField(choices=InventoryContainer.ContainerType.choices)
    location_id = serializers.IntegerField()
    bag_count = serializers.IntegerField(required=False, min_value=1, default=1)
    pieces_per_bag = serializers.IntegerField(required=False, min_value=1, allow_null=True)
    quantity = serializers.IntegerField(min_value=1)

    def validate(self, attrs):
        if not attrs.get("product_id") and not attrs.get("product"):
            raise serializers.ValidationError({"product": "请选择已有产品或填写临时产品资料。"})
        if attrs.get("quality_status") == InventoryBatch.QualityStatus.PASSED and not attrs.get("inspector_id"):
            raise serializers.ValidationError({"inspector_id": "已检合格库存必须记录品检员。"})
        if attrs.get("inspector_id") and not QualityEmployee.objects.filter(pk=attrs["inspector_id"], is_active=True).exists():
            raise serializers.ValidationError({"inspector_id": "品检员不存在或已停用。"})
        return attrs

    def create(self, validated_data):
        return create_inventory_receipt(validated_data, created_by=self.context["request"].user)


class InventoryContainerSerializer(serializers.ModelSerializer):
    batch = InventoryBatchSerializer(read_only=True)
    location_code = serializers.CharField(source="location.code", read_only=True)

    class Meta:
        model = InventoryContainer
        fields = [
            "id", "container_code", "batch", "container_type", "location", "location_code",
            "bag_count", "pieces_per_bag", "quantity", "status", "notes", "created_at", "updated_at",
        ]


class InventoryTransactionSerializer(serializers.ModelSerializer):
    created_by_name = serializers.SerializerMethodField()

    class Meta:
        model = InventoryTransaction
        fields = [
            "id", "transaction_type", "batch", "container", "quantity", "from_location",
            "to_location", "outbound", "reason", "created_by_name", "created_at",
        ]

    def get_created_by_name(self, obj):
        return obj.created_by.get_full_name() or obj.created_by.get_username()


class InventoryOutboundLineInputSerializer(serializers.Serializer):
    container_id = serializers.IntegerField()
    quantity = serializers.IntegerField(min_value=1)


class InventoryOutboundSerializer(serializers.Serializer):
    outbound_no = serializers.CharField(required=False, allow_blank=True, max_length=80)
    order_id = serializers.IntegerField(required=False, allow_null=True)
    shipment_ref = serializers.CharField(required=False, allow_blank=True, max_length=100)
    note = serializers.CharField(required=False, allow_blank=True)
    lines = InventoryOutboundLineInputSerializer(many=True, allow_empty=False)

    def create(self, validated_data):
        return create_inventory_outbound(validated_data, created_by=self.context["request"].user)


class InventoryOutboundReadSerializer(serializers.ModelSerializer):
    lines = serializers.SerializerMethodField()
    created_by_name = serializers.SerializerMethodField()

    class Meta:
        model = InventoryOutbound
        fields = [
            "id", "outbound_no", "order", "shipment_ref", "status", "note", "lines",
            "created_by_name", "created_at",
        ]

    def get_lines(self, obj):
        return [
            {
                "container_id": item.container_id,
                "container_code": item.container.container_code,
                "product_name": item.container.batch.product.product_name,
                "specification": item.container.batch.product.specification,
                "quantity": item.quantity,
            }
            for item in obj.lines.select_related("container__batch__product")
        ]

    def get_created_by_name(self, obj):
        return obj.created_by.get_full_name() or obj.created_by.get_username()


class MaterialRemainderSerializer(serializers.ModelSerializer):
    remaining_weight_kg = serializers.SerializerMethodField()
    created_by_name = serializers.SerializerMethodField()

    class Meta:
        model = MaterialRemainder
        fields = [
            "id", "material", "weight_kg", "remaining_weight_kg", "stored_on", "fridge_code",
            "note", "created_by_name", "created_at", "updated_at",
        ]
        read_only_fields = ["remaining_weight_kg", "created_by_name"]

    def get_remaining_weight_kg(self, obj):
        from django.db.models import Sum

        used = obj.uses.aggregate(total=Sum("weight_kg"))["total"] or Decimal("0")
        return max(Decimal(obj.weight_kg) - used, Decimal("0")).quantize(Decimal("0.001"))

    def get_created_by_name(self, obj):
        return obj.created_by.get_full_name() or obj.created_by.get_username()


class MaterialRemainderUseSerializer(serializers.ModelSerializer):
    class Meta:
        model = MaterialRemainderUse
        fields = ["id", "remainder", "weight_kg", "note", "created_at"]
