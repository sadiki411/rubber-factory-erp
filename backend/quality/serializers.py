from decimal import Decimal
from typing import Any

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from rest_framework import serializers

from orders.models import BusinessRecordRevision, ProductSpecification
from molds.models import MoldModel
from orders.services import model_snapshot, order_identity_exists, record_revision

from .models import (
    QualityEmployee, QualityOrder, QualityShipment, ReturnRework,
    ProductUnitWeight, ProcessCard, QualityShipmentBatch, QualityShipmentLine,
    QualityReworkCase, QualityReworkAttempt,
)


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
    source_batch_id = serializers.UUIDField(read_only=True)
    last_source_batch_id = serializers.UUIDField(read_only=True)
    created_by_name = serializers.SerializerMethodField()
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    product_specification = serializers.SerializerMethodField()
    last_data_updated_at = serializers.SerializerMethodField()
    product_specification_id = serializers.PrimaryKeyRelatedField(
        source="product_specification",
        queryset=ProductSpecification.objects.filter(is_active=True),
        required=False,
        allow_null=True,
    )

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
            "process_card_count",
            "process_card_covered_quantity",
            "process_card_text",
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

    def get_created_by_name(self, obj) -> str:
        return obj.created_by.get_full_name() or obj.created_by.get_username()

    def get_last_data_updated_at(self, obj) -> str | None:
        value = getattr(obj, "last_data_updated_at_value", None) or obj.updated_at
        return serializers.DateTimeField().to_representation(value) if value else None

    def get_product_specification(self, obj) -> dict | None:
        product = obj.product_specification
        if not product:
            return None
        mold_model = product.mold_model
        return {
            "id": product.pk,
            "product_name": product.product_name,
            "customer_product_no": product.customer_product_no,
            "specification": product.specification,
            "material": product.material,
            "mold_model_id": product.mold_model_id,
            "mold_model": (
                {
                    "id": mold_model.pk,
                    "code": mold_model.code,
                    "product_name": mold_model.product_name,
                    "is_active": mold_model.is_active,
                }
                if mold_model
                else None
            ),
            "mold_no": product.mold_no,
            "mold_size": product.mold_size,
            "is_active": product.is_active,
        }

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

    def create(self, validated_data):
        with transaction.atomic():
            instance = super().create(validated_data)
            request = self.context.get("request")
            record_revision(
                instance,
                request.user,
                BusinessRecordRevision.Action.CREATE,
            )
            return instance

    def update(self, instance, validated_data):
        with transaction.atomic():
            locked = QualityOrder.objects.select_for_update().get(pk=instance.pk)
            before = model_snapshot(locked)
            updated = super().update(locked, validated_data)
            request = self.context.get("request")
            record_revision(
                updated,
                request.user,
                BusinessRecordRevision.Action.UPDATE,
                before=before,
            )
            return updated


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
    inspectors = QualityEmployeeSerializer(many=True, read_only=True)
    inspector_ids = serializers.PrimaryKeyRelatedField(
        source="inspectors",
        queryset=QualityEmployee.objects.filter(
            is_active=True,
            role__in=[QualityEmployee.Role.INSPECTOR, QualityEmployee.Role.BOTH],
        ),
        many=True,
        required=False,
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
            "inspectors",
            "inspector_ids",
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

    def get_created_by_name(self, obj) -> str:
        return obj.created_by.get_full_name() or obj.created_by.get_username()

    def to_representation(self, instance):
        payload = super().to_representation(instance)
        # M2M rows have no insertion order in the legacy schema.  Expose the
        # single inspector first so old consumers and new multi-select UIs use
        # the same deterministic primary responsibility.
        if payload.get("inspector_id") and payload.get("inspector_ids"):
            primary = payload["inspector_id"]
            payload["inspector_ids"] = [primary] + [
                value for value in payload["inspector_ids"] if value != primary
            ]
            inspectors = payload.get("inspectors") or []
            payload["inspectors"] = [
                *[item for item in inspectors if item.get("id") == primary],
                *[item for item in inspectors if item.get("id") != primary],
            ]
        return payload

    def to_internal_value(self, data):
        data = data.copy()
        if "inspector_ids" not in data and isinstance(data.get("inspectors"), (list, tuple)):
            data["inspector_ids"] = data.get("inspectors")
        return super().to_internal_value(data)

    def create(self, validated_data):
        inspectors = validated_data.pop("inspectors", serializers.empty)
        instance = super().create(validated_data)
        if inspectors is not serializers.empty:
            instance.inspectors.set(inspectors)
            if inspectors:
                instance.inspector_id = inspectors[0].pk
                QualityShipment.objects.filter(pk=instance.pk).update(
                    inspector_id=inspectors[0].pk
                )
        else:
            instance.inspectors.set([instance.inspector])
        return instance

    def update(self, instance, validated_data):
        inspectors = validated_data.pop("inspectors", serializers.empty)
        instance = super().update(instance, validated_data)
        if inspectors is not serializers.empty:
            instance.inspectors.set(inspectors)
            first = inspectors[0] if inspectors else None
            if first is not None:
                instance.inspector_id = first.pk
                QualityShipment.objects.filter(pk=instance.pk).update(
                    inspector_id=first.pk
                )
        elif "inspector" in validated_data and instance.inspector_id:
            instance.inspectors.set([instance.inspector])
        return instance


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

    def get_created_by_name(self, obj) -> str:
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


class ProductUnitWeightSerializer(ValidatedModelSerializer):
    product_specification_id = serializers.PrimaryKeyRelatedField(source="product_specification", queryset=ProductSpecification.objects.all(), required=False, allow_null=True)
    mold_model_id = serializers.PrimaryKeyRelatedField(source="mold_model", queryset=MoldModel.objects.all(), required=False, allow_null=True)
    class Meta:
        model = ProductUnitWeight
        fields = ["id", "product_specification_id", "mold_model_id", "sample_count", "sample_total_weight_g", "unit_weight_g", "measured_on", "backfill_reason", "is_active", "notes", "created_by", "created_at", "updated_at"]
        read_only_fields = ["created_by", "created_at", "updated_at"]


class ProcessCardSerializer(ValidatedModelSerializer):
    order_id = serializers.PrimaryKeyRelatedField(source="order", queryset=QualityOrder.objects.all())
    product_specification_id = serializers.PrimaryKeyRelatedField(source="product_specification", queryset=ProductSpecification.objects.all(), required=False, allow_null=True)
    unit_weight_config_id = serializers.PrimaryKeyRelatedField(source="unit_weight_config", queryset=ProductUnitWeight.objects.all(), required=False, allow_null=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    theoretical_weight_kg = serializers.DecimalField(max_digits=14, decimal_places=3, read_only=True)
    max_allowed_weight_kg = serializers.DecimalField(max_digits=14, decimal_places=3, read_only=True)
    shipped_net_weight_kg = serializers.DecimalField(max_digits=14, decimal_places=3, read_only=True)
    delivered_net_weight_kg = serializers.DecimalField(max_digits=14, decimal_places=3, read_only=True)
    remaining_weight_kg = serializers.DecimalField(max_digits=14, decimal_places=3, read_only=True)
    expected_weight_kg = serializers.DecimalField(source="theoretical_weight_kg", max_digits=14, decimal_places=3, read_only=True)
    shipped_weight_kg = serializers.DecimalField(source="shipped_net_weight_kg", max_digits=14, decimal_places=3, read_only=True)
    shipped_quantity = serializers.SerializerMethodField()
    returned_piece_quantity = serializers.SerializerMethodField()
    delivered_piece_quantity = serializers.SerializerMethodField()
    rework_count = serializers.SerializerMethodField()
    due_date = serializers.DateField(source="order.due_date", read_only=True)
    material_weight_g = serializers.SerializerMethodField()
    class Meta:
        model = ProcessCard
        fields = ["id", "card_no", "order_id", "source_order_no", "source_item_no", "product_specification_id", "product_name_snapshot", "product_code_snapshot", "formula_code_snapshot", "specification_snapshot", "material_snapshot", "customer_snapshot", "department_snapshot", "special_requirements", "qr_text", "original_image", "material_issue_weight_kg", "material_weight_g", "demand_date", "due_date", "quantity", "unit_weight_config_id", "unit_weight_g", "sample_count_snapshot", "sample_total_weight_g_snapshot", "measured_on_snapshot", "mold_model_code_snapshot", "status", "status_display", "received_on", "backfill_reason", "reprint_count", "notes", "raw_data", "theoretical_weight_kg", "expected_weight_kg", "max_allowed_weight_kg", "remaining_weight_kg", "shipped_net_weight_kg", "shipped_weight_kg", "shipped_quantity", "returned_piece_quantity", "delivered_piece_quantity", "delivered_net_weight_kg", "rework_count", "created_by", "created_at", "updated_at"]
        read_only_fields = ["status_display", "theoretical_weight_kg", "max_allowed_weight_kg", "remaining_weight_kg", "shipped_net_weight_kg", "shipped_quantity", "returned_piece_quantity", "delivered_piece_quantity", "delivered_net_weight_kg", "rework_count", "created_by", "created_at", "updated_at"]

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if self.instance is not None and self.instance.shipment_lines.exists():
            # Once a card has any shipment line, its identity, quantity and
            # weight snapshot are historical facts.  Notes and operational
            # metadata may still be corrected, but changing these fields
            # would make old shipment calculations impossible to reproduce.
            immutable = (
                "card_no", "order", "quantity", "unit_weight_g",
                "unit_weight_config", "product_specification", "source_item_no",
            )
            changed = [field for field in immutable if field in attrs and attrs[field] != getattr(self.instance, field)]
            if changed:
                raise serializers.ValidationError({field: "已有出货记录，不能修改历史字段。" for field in changed})
        config = attrs.get("unit_weight_config")
        if config and "unit_weight_g" not in attrs:
            attrs["unit_weight_g"] = config.unit_weight_g
            attrs["sample_count_snapshot"] = config.sample_count
            attrs["sample_total_weight_g_snapshot"] = config.sample_total_weight_g
            attrs["measured_on_snapshot"] = config.measured_on
        return attrs

    def get_shipped_quantity(self, obj) -> int:
        return obj.shipped_piece_quantity

    def get_returned_piece_quantity(self, obj) -> int:
        return obj.returned_piece_quantity

    def get_delivered_piece_quantity(self, obj) -> int:
        return obj.delivered_piece_quantity

    def get_rework_count(self, obj) -> int:
        return obj.rework_cases.exclude(status=QualityReworkCase.Status.CANCELLED).count()

    def get_material_weight_g(self, obj) -> Decimal | None:
        return (obj.material_issue_weight_kg * Decimal("1000")) if obj.material_issue_weight_kg is not None else None


class QualityShipmentLineSerializer(ValidatedModelSerializer):
    process_card = ProcessCardSerializer(read_only=True)
    process_card_id = serializers.PrimaryKeyRelatedField(
        source="process_card", queryset=ProcessCard.objects.all(), required=False,
        allow_null=True,
    )
    order_id = serializers.PrimaryKeyRelatedField(
        source="order", queryset=QualityOrder.objects.all(), required=False,
        allow_null=True,
    )
    product_specification_id = serializers.PrimaryKeyRelatedField(
        source="product_specification", queryset=ProductSpecification.objects.filter(is_active=True),
        required=False, allow_null=True,
    )
    batch_id = serializers.PrimaryKeyRelatedField(source="batch", queryset=QualityShipmentBatch.objects.all(), required=False, write_only=True)
    specification_snapshot = serializers.CharField(required=False, allow_blank=True)
    material_snapshot = serializers.CharField(required=False, allow_blank=True)
    unit_weight_g_snapshot = serializers.DecimalField(
        max_digits=14, decimal_places=5, required=False, allow_null=True
    )
    product_batch_count = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    pieces_per_batch = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    # A batch-level ``total_net_weight_kg`` may be used by the one-line form;
    # the parent serializer copies it into this field before model creation.
    # Keep the field optional at the nested validation stage while the model
    # still rejects a genuinely missing value for standalone line requests.
    net_weight_kg = serializers.DecimalField(
        max_digits=14,
        decimal_places=3,
        required=False,
        allow_null=True,
    )
    class Meta:
        model = QualityShipmentLine
        fields = [
            "id", "batch_id", "process_card", "process_card_id", "order_id",
            "product_specification_id", "specification_snapshot", "material_snapshot",
            "net_weight_kg", "piece_quantity", "unit_weight_g_snapshot",
            "product_batch_count", "pieces_per_batch", "theoretical_weight_kg_snapshot",
            "max_allowed_weight_kg_snapshot", "notes", "created_at", "updated_at",
        ]
        read_only_fields = [
            "theoretical_weight_kg_snapshot", "max_allowed_weight_kg_snapshot",
            "created_at", "updated_at",
        ]

    def to_internal_value(self, data):
        data = data.copy()
        if "process_card_id" not in data and "card_id" in data:
            data["process_card_id"] = data.pop("card_id")
        if "order_id" not in data and "order" in data and isinstance(data.get("order"), (int, str)):
            data["order_id"] = data.pop("order")
        if "product_specification_id" not in data and "specification_id" in data:
            data["product_specification_id"] = data.pop("specification_id")
        if "net_weight_kg" not in data and "weight_kg" in data:
            data["net_weight_kg"] = data.pop("weight_kg")
        if "net_weight_kg" not in data and "actual_weight_kg" in data:
            data["net_weight_kg"] = data.pop("actual_weight_kg")
        if "net_weight_kg" not in data and "total_net_weight_kg" in data:
            data["net_weight_kg"] = data.pop("total_net_weight_kg")
        if "unit_weight_g_snapshot" not in data and "unit_weight_g" in data:
            data["unit_weight_g_snapshot"] = data.get("unit_weight_g")
        if "piece_quantity" not in data and "quantity" in data:
            data["piece_quantity"] = data.pop("quantity")
        if "product_batch_count" not in data and "batch_count" in data:
            data["product_batch_count"] = data.pop("batch_count")
        return super().to_internal_value(data)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        card = attrs.get("process_card")
        order = attrs.get("order")
        if card and order and card.order_id != order.pk:
            raise serializers.ValidationError({"order_id": "出货明细订单必须与流程卡一致。"})
        if card and not order:
            # Keep the order FK populated for candidate/search responses while
            # retaining the process-card association as the source of truth.
            attrs["order"] = card.order
        if not card and not order:
            raise serializers.ValidationError({"process_card_id": "请指定流程卡或订单。"})
        target_order = order or (card.order if card else None)
        expected_spec = str(
            (card.specification_snapshot if card else "")
            or (target_order.specification if target_order else "")
        ).strip()
        expected_material = str(
            (card.material_snapshot if card else "")
            or (target_order.material if target_order else "")
        ).strip()
        for field, expected in (
            ("specification_snapshot", expected_spec),
            ("material_snapshot", expected_material),
        ):
            supplied = str(attrs.get(field, "") or "").strip()
            if supplied and expected and supplied != expected:
                raise serializers.ValidationError({field: "规格/材质必须与所选流程卡或订单一致。"})
            if not supplied and expected:
                attrs[field] = expected
        if attrs.get("product_specification") is None:
            linked = getattr(card, "product_specification", None) if card else None
            linked = linked or getattr(target_order, "product_specification", None)
            if linked:
                attrs["product_specification"] = linked
        return attrs


class QualityShipmentBatchSerializer(ValidatedModelSerializer):
    client_key = serializers.CharField(required=False, allow_blank=True, validators=[])
    order_id = serializers.PrimaryKeyRelatedField(
        source="order", queryset=QualityOrder.objects.all(), required=False,
        allow_null=True,
    )
    product_specification_id = serializers.PrimaryKeyRelatedField(
        source="product_specification", queryset=ProductSpecification.objects.filter(is_active=True),
        required=False, allow_null=True,
    )
    inspector_id = serializers.PrimaryKeyRelatedField(source="inspector", queryset=QualityEmployee.objects.filter(is_active=True, role__in=[QualityEmployee.Role.INSPECTOR, QualityEmployee.Role.BOTH]), required=False, allow_null=True)
    inspector = QualityEmployeeSerializer(read_only=True)
    inspectors = QualityEmployeeSerializer(many=True, read_only=True)
    inspector_ids = serializers.PrimaryKeyRelatedField(
        source="inspectors",
        queryset=QualityEmployee.objects.filter(
            is_active=True,
            role__in=[QualityEmployee.Role.INSPECTOR, QualityEmployee.Role.BOTH],
        ),
        many=True,
        required=False,
    )
    lines = QualityShipmentLineSerializer(many=True, required=False)
    net_weight_kg = serializers.DecimalField(max_digits=14, decimal_places=3, read_only=True)
    total_net_weight_kg = serializers.DecimalField(
        max_digits=14, decimal_places=3, required=False, allow_null=True
    )
    actual_weight_kg = serializers.DecimalField(max_digits=14, decimal_places=3, read_only=True)
    shipped_quantity = serializers.IntegerField(read_only=True)
    # Accepted as a legacy one-line input alias; the line serializer still
    # recalculates it for the new contract before confirmation.
    piece_quantity = serializers.IntegerField(required=False, allow_null=True)
    line_count = serializers.IntegerField(read_only=True)
    date_pending = serializers.BooleanField(read_only=True)
    warnings = serializers.SerializerMethodField()
    class Meta:
        model = QualityShipmentBatch
        fields = [
            "id", "shipment_no", "client_key", "shipment_date", "order_id",
            "product_specification_id", "product_name_snapshot", "specification_snapshot",
            "material_snapshot", "unit_weight_g", "product_batch_count", "pieces_per_batch",
            "inspector", "inspector_id", "inspectors", "inspector_ids", "status", "customer",
            "delivery_info", "backfill_reason", "notes", "lines", "net_weight_kg",
            "total_net_weight_kg", "actual_weight_kg", "shipped_quantity", "piece_quantity",
            "line_count", "date_pending", "warnings", "created_by", "created_at", "updated_at",
        ]
        read_only_fields = [
            "created_by", "created_at", "updated_at", "net_weight_kg", "actual_weight_kg",
            "shipped_quantity", "line_count", "date_pending", "warnings",
        ]

    def to_internal_value(self, data):
        data = data.copy()
        if "lines" not in data and "items" in data:
            data["lines"] = data.pop("items")
        if "shipment_no" not in data and "batch_no" in data:
            data["shipment_no"] = data.pop("batch_no")
        if "inspector_ids" not in data and "inspectors" in data:
            inspectors = data.get("inspectors")
            if isinstance(inspectors, (list, tuple)):
                data["inspector_ids"] = inspectors
        if "order_id" not in data and "order" in data and isinstance(data.get("order"), (int, str)):
            data["order_id"] = data.pop("order")
        if "product_specification_id" not in data and "specification_id" in data:
            data["product_specification_id"] = data.pop("specification_id")
        if "product_name_snapshot" not in data and "product_name" in data:
            data["product_name_snapshot"] = data.pop("product_name")
        if "specification_snapshot" not in data and "specification" in data:
            data["specification_snapshot"] = data.pop("specification")
        if "material_snapshot" not in data and "material" in data:
            data["material_snapshot"] = data.pop("material")
        if "total_net_weight_kg" not in data and "net_weight_kg" in data:
            data["total_net_weight_kg"] = data.get("net_weight_kg")
        # Batch-level aliases are accepted for the one-line entry form.  The
        # canonical values are copied to the line during create/update below.
        if "product_batch_count" not in data and "batch_count" in data:
            data["product_batch_count"] = data.get("batch_count")
        if "unit_weight_g" not in data and "unit_weight_g_snapshot" in data:
            data["unit_weight_g"] = data.get("unit_weight_g_snapshot")
        return super().to_internal_value(data)

    def to_representation(self, instance):
        payload = super().to_representation(instance)
        if payload.get("inspector_id") and payload.get("inspector_ids"):
            primary = payload["inspector_id"]
            payload["inspector_ids"] = [primary] + [
                value for value in payload["inspector_ids"] if value != primary
            ]
            inspectors = payload.get("inspectors") or []
            payload["inspectors"] = [
                *[item for item in inspectors if item.get("id") == primary],
                *[item for item in inspectors if item.get("id") != primary],
            ]
        return payload

    def get_warnings(self, obj) -> list[str]:
        warnings: list[str] = []
        for line in obj.lines.select_related("process_card", "order"):
            card = line.process_card
            theoretical = line.theoretical_weight_kg_snapshot
            if theoretical is None and card:
                theoretical = card.theoretical_weight_kg
            if theoretical and Decimal(line.net_weight_kg) < Decimal(theoretical):
                label = card.card_no if card else (line.order.order_no if line.order_id else line.pk)
                warnings.append(f"{label} 实际净重低于理论重量，请复核称重。")
        return warnings

    def validate(self, attrs):
        attrs = super().validate(attrs)
        lines = attrs.get("lines", serializers.empty)
        status = attrs.get("status", getattr(self.instance, "status", QualityShipmentBatch.Status.DRAFT))
        if self.instance is None and status != QualityShipmentBatch.Status.DRAFT:
            raise serializers.ValidationError({"status": "批次必须先保存为草稿，再通过确认操作入账。"})
        if self.instance is not None and "status" in attrs and status != self.instance.status:
            raise serializers.ValidationError({"status": "出货批次状态只能通过确认或作废操作变更。"})
        if lines is not serializers.empty and not lines and status != QualityShipmentBatch.Status.DRAFT:
            raise serializers.ValidationError({"lines": "At least one shipment line is required."})
        order = attrs.get("order")
        if order and lines is not serializers.empty:
            for line in lines:
                line_order = line.get("order")
                card = line.get("process_card")
                if card and card.order_id != order.pk:
                    raise serializers.ValidationError({"order_id": "批次订单必须与出货明细一致。"})
                if line_order and line_order.pk != order.pk:
                    raise serializers.ValidationError({"order_id": "批次订单必须与出货明细一致。"})
        return attrs

    def create(self, validated_data):
        # ``client_key`` is the idempotency token used by mobile retries.  It
        # takes precedence over the human-facing shipment number: a retry of
        # an already-confirmed request must return that exact batch.
        key = validated_data.get("client_key")
        try:
            with transaction.atomic():
                if key:
                    existing = QualityShipmentBatch.objects.select_for_update().filter(client_key=key).first()
                    if existing:
                        return existing

                # Shipment numbers are operator-facing and are commonly
                # reused while a paper form is still being filled.  A draft
                # with the same number is therefore resumed/updated instead
                # of being rejected.  Confirmed and void documents are
                # immutable audit records and must use a new number.
                shipment_no = str(validated_data.get("shipment_no") or "").strip().upper()
                if shipment_no:
                    existing = (
                        QualityShipmentBatch.objects.select_for_update()
                        .filter(shipment_no__iexact=shipment_no)
                        .first()
                    )
                    if existing:
                        if existing.status == QualityShipmentBatch.Status.DRAFT:
                            resume_data = dict(validated_data)
                            # Creator and idempotency key belong to the
                            # original draft.  Resuming by the human-facing
                            # number must not rewrite either audit identity.
                            resume_data.pop("created_by", None)
                            resume_data.pop("client_key", None)
                            return self.update(existing, resume_data)
                        status_label = (
                            "已确认" if existing.status == QualityShipmentBatch.Status.CONFIRMED else "已作废"
                        )
                        raise serializers.ValidationError(
                            {
                                "shipment_no": (
                                    f"出货单号 {shipment_no} 已存在（{status_label}），请使用新的出货单号。"
                                )
                            }
                        )
                    legacy = (
                        QualityShipment.objects.filter(shipment_no__iexact=shipment_no)
                        .select_for_update()
                        .first()
                    )
                    if legacy:
                        raise serializers.ValidationError(
                            {
                                "shipment_no": (
                                    f"出货单号 {shipment_no} 已存在于历史出货台账，请使用新的出货单号。"
                                )
                            }
                        )

                lines = validated_data.pop("lines", [])
                inspectors = validated_data.pop("inspectors", serializers.empty)
                batch_piece_quantity = validated_data.pop("piece_quantity", None)
                # ``total_net_weight_kg`` is a serializer-only alias; keep a
                # local copy so it can be propagated to a one-line payload.
                batch_total_weight = validated_data.pop("total_net_weight_kg", None)
                # A one-line payload may put the order/product context and
                # weight aliases on the batch rather than the line.  Copy them
                # down before model validation so snapshots are persisted with
                # the immutable line record.
                validated_data["_total_net_weight_kg_input"] = batch_total_weight
                self._copy_batch_defaults_to_lines(validated_data, lines, batch_piece_quantity)
                validated_data.pop("_total_net_weight_kg_input", None)
                if lines and validated_data.get("order") is None:
                    first_order = lines[0].get("order")
                    first_card = lines[0].get("process_card")
                    validated_data["order"] = first_order or (first_card.order if first_card else None)
                batch = QualityShipmentBatch.objects.create(**validated_data)
                for item in lines:
                    item.pop("batch", None)
                    QualityShipmentLine.objects.create(batch=batch, **item)
                if inspectors is not serializers.empty:
                    batch.inspectors.set(inspectors)
                    first = inspectors[0] if inspectors else None
                    if first is not None and batch.inspector_id != first.pk:
                        QualityShipmentBatch.objects.filter(pk=batch.pk).update(inspector=first)
                        batch.inspector = first
                elif batch.inspector_id:
                    batch.inspectors.set([batch.inspector])
                return batch
        except IntegrityError as exc:
            # A mobile retry may race with the first request.  Let the
            # conditional unique constraint select the winner, then return
            # that batch instead of turning the retry into a 500 response.
            if key:
                existing = QualityShipmentBatch.objects.filter(client_key=key).first()
                if existing:
                    return existing
            raise serializers.ValidationError({"detail": self.conflict_message}) from exc

    def update(self, instance, validated_data):
        next_shipment_no = str(
            validated_data.get("shipment_no", instance.shipment_no) or ""
        ).strip().upper()
        if next_shipment_no:
            conflict = QualityShipmentBatch.objects.filter(
                shipment_no__iexact=next_shipment_no
            ).exclude(pk=instance.pk).first()
            if conflict:
                raise serializers.ValidationError(
                    {"shipment_no": f"出货单号已被{conflict.get_status_display()}记录使用。"}
                )
            if QualityShipment.objects.filter(shipment_no__iexact=next_shipment_no).exists():
                raise serializers.ValidationError(
                    {"shipment_no": "出货单号已被历史出货记录使用。"}
                )
        lines = validated_data.pop("lines", serializers.empty)
        inspectors = validated_data.pop("inspectors", serializers.empty)
        batch_piece_quantity = validated_data.pop("piece_quantity", None)
        batch_total_weight = validated_data.pop("total_net_weight_kg", None)
        if instance.status in (QualityShipmentBatch.Status.VOID, QualityShipmentBatch.Status.CONFIRMED):
            raise serializers.ValidationError({"status": "Only draft shipment batches can be edited."})
        with transaction.atomic():
            instance = QualityShipmentBatch.objects.select_for_update().get(pk=instance.pk)
            if instance.status in (QualityShipmentBatch.Status.VOID, QualityShipmentBatch.Status.CONFIRMED):
                raise serializers.ValidationError({"status": "只有草稿出货批次可以编辑。"})
            instance = super().update(instance, validated_data)
            if lines is not serializers.empty:
                batch_defaults = {
                    "order": instance.order,
                    "product_specification": instance.product_specification,
                    "specification_snapshot": instance.specification_snapshot,
                    "material_snapshot": instance.material_snapshot,
                    "unit_weight_g": instance.unit_weight_g,
                    "product_batch_count": instance.product_batch_count,
                    "pieces_per_batch": instance.pieces_per_batch,
                    "_total_net_weight_kg_input": batch_total_weight,
                }
                self._copy_batch_defaults_to_lines(batch_defaults, lines, batch_piece_quantity)
                instance.lines.all().delete()
                for item in lines:
                    item.pop("batch", None)
                    QualityShipmentLine.objects.create(batch=instance, **item)
            if inspectors is not serializers.empty:
                instance.inspectors.set(inspectors)
                first = inspectors[0] if inspectors else None
                instance.inspector = first
                QualityShipmentBatch.objects.filter(pk=instance.pk).update(
                    inspector_id=first.pk if first else None
                )
            elif "inspector" in validated_data and instance.inspector_id:
                instance.inspectors.set([instance.inspector])
            return instance

    @staticmethod
    def _copy_batch_defaults_to_lines(batch_data, lines, batch_piece_quantity=None):
        """Normalize batch-level aliases into a one-line payload.

        The first weighted API accepted only process-card lines.  The current
        entry form also supports a manually selected order and submits common
        values at the batch level.  Keeping this normalization here lets both
        contracts share the same immutable line/snapshot implementation.
        """
        if not lines:
            return
        defaults = {
            "order": batch_data.get("order"),
            "product_specification": batch_data.get("product_specification"),
            "specification_snapshot": batch_data.get("specification_snapshot", ""),
            "material_snapshot": batch_data.get("material_snapshot", ""),
            "unit_weight_g_snapshot": batch_data.get("unit_weight_g"),
            "product_batch_count": batch_data.get("product_batch_count"),
            "pieces_per_batch": batch_data.get("pieces_per_batch"),
            "piece_quantity": batch_piece_quantity,
        }
        # ``total_net_weight_kg`` is a write-only batch input.  A line's own
        # value always wins; otherwise use it for the one-line form.
        total = batch_data.get("_total_net_weight_kg_input", batch_data.get("total_net_weight_kg"))
        if total is not None and len(lines) != 1:
            raise serializers.ValidationError(
                {
                    "total_net_weight_kg": (
                        "总净重快捷字段仅适用于单条出货明细；多条明细请分别填写各自净重。"
                    )
                }
            )
        # A free-order form supplies both the calculated quantity and the
        # total weight.  The server remains authoritative: when no explicit
        # batch-count calculation was requested, discard a client-supplied
        # quantity and let QualityShipmentLine.clean derive it from kg / g.
        force_weight_quantity = total is not None
        for line in lines:
            for key, value in defaults.items():
                if value is not None and value != "" and line.get(key) in (None, ""):
                    line[key] = value
            if total is not None and line.get("net_weight_kg") in (None, ""):
                line["net_weight_kg"] = total
            if force_weight_quantity and line.get("process_card") is None:
                line["piece_quantity"] = None


class QualityReworkCaseSerializer(ValidatedModelSerializer):
    process_card_id = serializers.PrimaryKeyRelatedField(source="process_card", queryset=ProcessCard.objects.all(), required=False, allow_null=True)
    shipment_line_id = serializers.PrimaryKeyRelatedField(source="shipment_line", queryset=QualityShipmentLine.objects.all(), required=False, allow_null=True)
    responsible_inspector_id = serializers.PrimaryKeyRelatedField(source="responsible_inspector", queryset=QualityEmployee.objects.filter(is_active=True, role__in=[QualityEmployee.Role.INSPECTOR, QualityEmployee.Role.BOTH]), required=False, allow_null=True)
    attempt_count = serializers.IntegerField(read_only=True)
    attempts = serializers.SerializerMethodField()
    class Meta:
        model = QualityReworkCase
        fields = ["id", "case_no", "origin", "process_card_id", "shipment_line_id", "opened_on", "backfill_reason", "reason_category", "reason", "responsible_inspector_id", "affected_quantity", "affected_weight_kg", "status", "closed_on", "notes", "attempt_count", "attempts", "created_by", "created_at", "updated_at"]
        read_only_fields = ["case_no", "attempt_count", "attempts", "created_by", "created_at", "updated_at"]

    def validate(self, attrs):
        attrs = super().validate(attrs)
        process_card = attrs.get("process_card", getattr(self.instance, "process_card", None))
        shipment_line = attrs.get("shipment_line", getattr(self.instance, "shipment_line", None))
        if process_card is not None and shipment_line is not None and process_card.pk != shipment_line.process_card_id:
            raise serializers.ValidationError({"shipment_line": "返工关联的出货明细必须属于所选流程卡。"})
        if self.instance is not None and self.instance.attempts.exists():
            immutable = ("origin", "process_card", "shipment_line", "affected_quantity", "affected_weight_kg")
            changed = [field for field in immutable if field in attrs and attrs[field] != getattr(self.instance, field)]
            if changed:
                raise serializers.ValidationError({field: "已有返工轮次，不能修改该历史数量或关联。" for field in changed})
        return attrs

    def get_attempts(self, obj) -> list[dict[str, Any]]:
        return [
            {
                "id": attempt.pk,
                "case_id": obj.pk,
                "attempt_no": attempt.attempt_no,
                "attempt_label": f"R{attempt.attempt_no}" if attempt.attempt_no else None,
                "attempt_date": attempt.attempt_date,
                "input_quantity": attempt.input_quantity,
                "reworked_quantity": attempt.reworked_quantity,
                "recovered_quantity": attempt.recovered_quantity,
                "scrap_quantity": attempt.scrap_quantity,
                "input_weight_kg": attempt.input_weight_kg,
                "reworked_weight_kg": attempt.reworked_weight_kg,
                "recovered_weight_kg": attempt.recovered_weight_kg,
                "scrap_weight_kg": attempt.scrap_weight_kg,
                "status": attempt.status,
                "notes": attempt.notes,
            }
            for attempt in obj.attempts.all()
        ]


class QualityReworkAttemptSerializer(ValidatedModelSerializer):
    case_id = serializers.PrimaryKeyRelatedField(source="case", queryset=QualityReworkCase.objects.all())
    attempt_label = serializers.SerializerMethodField()
    rework_employee_id = serializers.PrimaryKeyRelatedField(source="rework_employee", queryset=QualityEmployee.objects.filter(is_active=True, role__in=[QualityEmployee.Role.REWORKER, QualityEmployee.Role.BOTH]), required=False, allow_null=True)
    class Meta:
        model = QualityReworkAttempt
        fields = ["id", "case_id", "attempt_no", "attempt_label", "attempt_date", "backfill_reason", "rework_employee_id", "input_quantity", "reworked_quantity", "recovered_quantity", "scrap_quantity", "input_weight_kg", "reworked_weight_kg", "recovered_weight_kg", "scrap_weight_kg", "status", "notes", "created_by", "created_at", "updated_at"]
        read_only_fields = ["attempt_no", "attempt_label", "created_by", "created_at", "updated_at"]

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if self.instance is not None and "case" in attrs and attrs["case"].pk != self.instance.case_id:
            raise serializers.ValidationError({"case_id": "返工轮次的主案关联创建后不能更换。"})
        return attrs

    def create(self, validated_data):
        case = validated_data.get("case")
        if case is None:
            return super().create(validated_data)
        with transaction.atomic():
            locked_case = QualityReworkCase.objects.select_for_update().get(pk=case.pk)
            validated_data["case"] = locked_case
            return super().create(validated_data)

    def update(self, instance, validated_data):
        with transaction.atomic():
            locked_instance = QualityReworkAttempt.objects.select_for_update().select_related("case").get(pk=instance.pk)
            locked_case = QualityReworkCase.objects.select_for_update().get(pk=locked_instance.case_id)
            validated_data["case"] = locked_case
            return super().update(locked_instance, validated_data)

    def get_attempt_label(self, obj) -> str | None:
        return f"R{obj.attempt_no}" if obj.attempt_no else None
