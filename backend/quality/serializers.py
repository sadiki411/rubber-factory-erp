from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from rest_framework import serializers

from .models import QualityEmployee, QualityOrder, QualityShipment, ReturnRework


def _validation_details(exc):
    if hasattr(exc, "message_dict"):
        return exc.message_dict
    return {"detail": exc.messages}


class ValidatedModelSerializer(serializers.ModelSerializer):
    conflict_message = "数据与现有记录冲突，请刷新后重试。"

    def create(self, validated_data):
        instance = self.Meta.model(**validated_data)
        try:
            instance.save()
        except DjangoValidationError as exc:
            raise serializers.ValidationError(_validation_details(exc)) from exc
        except IntegrityError as exc:
            raise serializers.ValidationError({"detail": self.conflict_message}) from exc
        return instance

    def update(self, instance, validated_data):
        for field, value in validated_data.items():
            setattr(instance, field, value)
        try:
            instance.save()
        except DjangoValidationError as exc:
            raise serializers.ValidationError(_validation_details(exc)) from exc
        except IntegrityError as exc:
            raise serializers.ValidationError({"detail": self.conflict_message}) from exc
        return instance


class QualityEmployeeSerializer(ValidatedModelSerializer):
    role_display = serializers.CharField(source="get_role_display", read_only=True)

    class Meta:
        model = QualityEmployee
        fields = [
            "id",
            "employee_no",
            "name",
            "team",
            "role",
            "role_display",
            "is_active",
            "notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at"]


class QualityOrderSerializer(ValidatedModelSerializer):
    created_by_name = serializers.SerializerMethodField()
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = QualityOrder
        fields = [
            "id",
            "order_no",
            "batch_no",
            "product_code",
            "product_name",
            "specification",
            "material",
            "order_quantity",
            "order_date",
            "due_date",
            "mold_size",
            "status",
            "status_display",
            "notes",
            "created_by_name",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_by_name", "created_at", "updated_at"]

    def get_created_by_name(self, obj):
        return obj.created_by.get_full_name() or obj.created_by.get_username()


class QualityShipmentSerializer(ValidatedModelSerializer):
    order = QualityOrderSerializer(read_only=True)
    order_id = serializers.PrimaryKeyRelatedField(
        source="order", queryset=QualityOrder.objects.all()
    )
    inspector = QualityEmployeeSerializer(read_only=True)
    inspector_id = serializers.PrimaryKeyRelatedField(
        source="inspector",
        queryset=QualityEmployee.objects.filter(
            is_active=True,
            role__in=[QualityEmployee.Role.INSPECTOR, QualityEmployee.Role.BOTH],
        ),
    )
    rework_count = serializers.IntegerField(read_only=True)
    returned_quantity = serializers.IntegerField(read_only=True)
    created_by_name = serializers.SerializerMethodField()

    class Meta:
        model = QualityShipment
        fields = [
            "id",
            "shipment_no",
            "shipment_date",
            "order",
            "order_id",
            "inspector",
            "inspector_id",
            "inspection_quantity",
            "qualified_quantity",
            "defective_quantity",
            "shipped_quantity",
            "rework_count",
            "returned_quantity",
            "notes",
            "created_by_name",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "rework_count",
            "returned_quantity",
            "created_by_name",
            "created_at",
            "updated_at",
        ]

    def get_created_by_name(self, obj):
        return obj.created_by.get_full_name() or obj.created_by.get_username()


class ReturnReworkSerializer(ValidatedModelSerializer):
    shipment = QualityShipmentSerializer(read_only=True)
    shipment_id = serializers.PrimaryKeyRelatedField(
        source="shipment", queryset=QualityShipment.objects.all()
    )
    responsible_inspector = QualityEmployeeSerializer(read_only=True)
    responsible_inspector_id = serializers.PrimaryKeyRelatedField(
        source="responsible_inspector",
        queryset=QualityEmployee.objects.filter(
            is_active=True,
            role__in=[QualityEmployee.Role.INSPECTOR, QualityEmployee.Role.BOTH],
        ),
        required=False,
    )
    rework_employee = QualityEmployeeSerializer(read_only=True)
    rework_employee_id = serializers.PrimaryKeyRelatedField(
        source="rework_employee",
        queryset=QualityEmployee.objects.filter(
            is_active=True,
            role__in=[QualityEmployee.Role.REWORKER, QualityEmployee.Role.BOTH],
        ),
    )
    order_id = serializers.IntegerField(source="shipment.order_id", read_only=True)
    created_by_name = serializers.SerializerMethodField()
    reason_category_display = serializers.CharField(
        source="get_reason_category_display", read_only=True
    )
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = ReturnRework
        fields = [
            "id",
            "shipment",
            "shipment_id",
            "order_id",
            "rework_date",
            "reason_category",
            "reason_category_display",
            "reason",
            "responsible_inspector",
            "responsible_inspector_id",
            "rework_employee",
            "rework_employee_id",
            "returned_quantity",
            "reworked_quantity",
            "recovered_quantity",
            "scrap_quantity",
            "status",
            "status_display",
            "work_hours",
            "notes",
            "created_by_name",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["order_id", "created_by_name", "created_at", "updated_at"]

    def get_created_by_name(self, obj):
        return obj.created_by.get_full_name() or obj.created_by.get_username()

    def validate(self, attrs):
        shipment = attrs.get("shipment") or getattr(self.instance, "shipment", None)
        if shipment and "responsible_inspector" not in attrs and not self.instance:
            attrs["responsible_inspector"] = shipment.inspector
        return attrs

    def create(self, validated_data):
        shipment = validated_data["shipment"]
        try:
            with transaction.atomic():
                validated_data["shipment"] = QualityShipment.objects.select_for_update().get(
                    pk=shipment.pk
                )
                return super().create(validated_data)
        except IntegrityError as exc:
            raise serializers.ValidationError({"detail": self.conflict_message}) from exc

    def update(self, instance, validated_data):
        target_shipment = validated_data.get("shipment", instance.shipment)
        try:
            with transaction.atomic():
                shipment_ids = sorted({instance.shipment_id, target_shipment.pk})
                locked = {
                    item.pk: item
                    for item in QualityShipment.objects.select_for_update().filter(
                        pk__in=shipment_ids
                    )
                }
                validated_data["shipment"] = locked[target_shipment.pk]
                locked_instance = ReturnRework.objects.select_for_update().get(pk=instance.pk)
                return super().update(locked_instance, validated_data)
        except IntegrityError as exc:
            raise serializers.ValidationError({"detail": self.conflict_message}) from exc
