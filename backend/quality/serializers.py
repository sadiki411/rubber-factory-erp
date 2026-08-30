from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from rest_framework import serializers

from orders.models import BusinessRecordRevision, ProductSpecification
from molds.models import MoldModel
from orders.services import model_snapshot, order_identity_exists, record_revision

from .models import (
    DefectReason,
    QualityEmployee, QualityOrder, QualityShipment, ReturnRework,
    ProductUnitWeight, ProcessCard, ProcessCardUnitBinding,
    QualityShipmentBatch, QualityShipmentLine,
    QualityReworkCase, QualityReworkAttempt,
)
from .services import (
    create_whole_batch_return_case,
    legacy_reason_category,
    serialize_rework_source,
    sync_order_status_from_delivery,
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


class ProductSpecificationReferenceSerializer(serializers.ModelSerializer):
    """Small read-only product identity used by quality workflow responses."""

    class Meta:
        model = ProductSpecification
        fields = [
            "id",
            "product_name",
            "specification",
            "material",
            "customer_product_no",
            "is_active",
        ]


class MoldModelReferenceSerializer(serializers.ModelSerializer):
    """Small read-only mold identity used by unit-weight responses."""

    class Meta:
        model = MoldModel
        fields = ["id", "code", "product_name", "is_active"]


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
        sync_order_status_from_delivery(
            [instance.order_id],
            source="SHIPMENT",
            operator=instance.created_by,
            reason_prefix=f"登记出货 {instance.shipment_no}。",
        )
        return instance

    def update(self, instance, validated_data):
        previous_order_id = instance.order_id
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
        sync_order_status_from_delivery(
            [previous_order_id, instance.order_id],
            source="SHIPMENT",
            operator=instance.created_by,
            reason_prefix=f"更新出货 {instance.shipment_no}。",
        )
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
                instance = super().create(validated_data)
                sync_order_status_from_delivery(
                    [instance.shipment.order_id],
                    source="CUSTOMER_RETURN",
                    operator=instance.created_by,
                    reason_prefix=f"登记客户退货 {instance.pk}。",
                )
                return instance
        except IntegrityError as exc:
            raise serializers.ValidationError({"detail": self.conflict_message}) from exc

    def update(self, instance, validated_data):
        target_shipment = validated_data.get("shipment", instance.shipment)
        previous_order_id = instance.shipment.order_id
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
                updated = super().update(locked_instance, validated_data)
                sync_order_status_from_delivery(
                    [previous_order_id, updated.shipment.order_id],
                    source="CUSTOMER_RETURN",
                    operator=updated.created_by,
                    reason_prefix=f"更新客户退货 {updated.pk}。",
                )
                return updated
        except IntegrityError as exc:
            raise serializers.ValidationError({"detail": self.conflict_message}) from exc


class ProductUnitWeightSerializer(ValidatedModelSerializer):
    product_specification = ProductSpecificationReferenceSerializer(read_only=True)
    product_specification_id = serializers.PrimaryKeyRelatedField(source="product_specification", queryset=ProductSpecification.objects.all(), required=False, allow_null=True)
    mold_model = MoldModelReferenceSerializer(read_only=True)
    mold_model_id = serializers.PrimaryKeyRelatedField(source="mold_model", queryset=MoldModel.objects.all(), required=False, allow_null=True)
    class Meta:
        model = ProductUnitWeight
        fields = ["id", "product_specification", "product_specification_id", "mold_model", "mold_model_id", "sample_count", "sample_total_weight_g", "unit_weight_g", "measured_on", "backfill_reason", "is_active", "notes", "created_by", "created_at", "updated_at"]
        read_only_fields = ["created_by", "created_at", "updated_at"]


class DefectReasonSerializer(ValidatedModelSerializer):
    class Meta:
        model = DefectReason
        fields = [
            "id", "code", "name", "is_active", "is_system", "sort_order",
            "notes", "created_at", "updated_at",
        ]
        read_only_fields = ["is_system", "created_at", "updated_at"]


class ProcessCardUnitBindingSerializer(serializers.ModelSerializer):
    card_no = serializers.CharField(source="process_card.card_no", read_only=True)
    shipment_no = serializers.CharField(source="shipment_batch.shipment_no", read_only=True)
    shipment_date = serializers.DateField(
        source="shipment_batch.shipment_date", read_only=True
    )
    order_id = serializers.IntegerField(source="process_card.order_id", read_only=True)
    order_no = serializers.CharField(
        source="process_card.order.order_no", read_only=True
    )
    item_no = serializers.CharField(
        source="process_card.order.item_no", read_only=True
    )
    product_name = serializers.SerializerMethodField()
    specification = serializers.SerializerMethodField()
    material = serializers.SerializerMethodField()

    class Meta:
        model = ProcessCardUnitBinding
        fields = [
            "id", "process_card_id", "card_no", "shipment_batch_id",
            "shipment_no", "shipment_date", "shipment_unit_no", "order_id",
            "order_no", "item_no", "product_name", "specification",
            "material", "piece_quantity", "net_weight_kg", "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_product_name(self, obj) -> str:
        return obj.process_card.product_name_snapshot or obj.process_card.order.product_name

    def get_specification(self, obj) -> str:
        return obj.process_card.specification_snapshot or obj.process_card.order.specification

    def get_material(self, obj) -> str:
        return obj.process_card.material_snapshot or obj.process_card.order.material


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
    replaces_card_no = serializers.CharField(source="replaces.card_no", read_only=True)
    replaced_by_card_no = serializers.SerializerMethodField()
    active_card_id = serializers.SerializerMethodField()
    active_card_no = serializers.SerializerMethodField()
    unit_binding = ProcessCardUnitBindingSerializer(read_only=True)
    current_return = serializers.SerializerMethodField()
    class Meta:
        model = ProcessCard
        fields = ["id", "card_no", "tracking_id", "replaces_card_no", "replaced_by_card_no", "active_card_id", "active_card_no", "unit_binding", "current_return", "order_id", "source_order_no", "source_item_no", "product_specification_id", "product_name_snapshot", "product_code_snapshot", "formula_code_snapshot", "specification_snapshot", "material_snapshot", "customer_snapshot", "department_snapshot", "special_requirements", "qr_text", "original_image", "material_issue_weight_kg", "material_weight_g", "demand_date", "due_date", "quantity", "unit_weight_config_id", "unit_weight_g", "sample_count_snapshot", "sample_total_weight_g_snapshot", "measured_on_snapshot", "mold_model_code_snapshot", "status", "status_display", "received_on", "backfill_reason", "reprint_count", "notes", "raw_data", "theoretical_weight_kg", "expected_weight_kg", "max_allowed_weight_kg", "remaining_weight_kg", "shipped_net_weight_kg", "shipped_weight_kg", "shipped_quantity", "returned_piece_quantity", "delivered_piece_quantity", "delivered_net_weight_kg", "rework_count", "created_by", "created_at", "updated_at"]
        read_only_fields = ["tracking_id", "status_display", "theoretical_weight_kg", "max_allowed_weight_kg", "remaining_weight_kg", "shipped_net_weight_kg", "shipped_quantity", "returned_piece_quantity", "delivered_piece_quantity", "delivered_net_weight_kg", "rework_count", "created_by", "created_at", "updated_at"]

    def validate(self, attrs):
        attrs = super().validate(attrs)
        card_no = str(attrs.get("card_no", getattr(self.instance, "card_no", "")) or "").strip().upper()
        qr_text = str(attrs.get("qr_text", getattr(self.instance, "qr_text", "")) or "").strip().upper()
        if qr_text and qr_text != card_no:
            raise serializers.ValidationError(
                {"qr_text": "标准二维码内容必须与流程卡单号完全一致。"}
            )
        if self.instance is None and card_no and not qr_text:
            attrs["qr_text"] = card_no
        has_tracking_history = bool(
            self.instance is not None
            and (
                self.instance.shipment_lines.exists()
                or ProcessCardUnitBinding.objects.filter(
                    process_card=self.instance
                ).exists()
                or self.instance.rework_cases.exists()
                or self.instance.replaces_id is not None
                or ProcessCard.objects.filter(replaces_id=self.instance.pk).exists()
            )
        )
        if has_tracking_history:
            # Once a card has any shipment line, its identity, quantity and
            # physical-unit binding, return, or replacement, its identity and
            # measurement snapshot are historical facts. Notes and other
            # operational metadata may still be corrected.
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
        return QualityReworkCase.objects.filter(
            process_card__tracking_id=obj.tracking_id,
        ).exclude(status=QualityReworkCase.Status.CANCELLED).count()

    def get_material_weight_g(self, obj) -> Decimal | None:
        return (obj.material_issue_weight_kg * Decimal("1000")) if obj.material_issue_weight_kg is not None else None

    def get_replaced_by_card_no(self, obj) -> str | None:
        replacement = ProcessCard.objects.filter(replaces_id=obj.pk).only("card_no").first()
        return replacement.card_no if replacement else None

    def get_active_card_id(self, obj) -> int:
        return obj.active_replacement.pk

    def get_active_card_no(self, obj) -> str:
        return obj.active_replacement.card_no

    def get_current_return(self, obj) -> dict | None:
        case = (
            QualityReworkCase.objects.filter(
                process_card__tracking_id=obj.tracking_id,
                origin=QualityReworkCase.Origin.CUSTOMER_RETURN,
                is_current_return=True,
            )
            .exclude(status=QualityReworkCase.Status.CANCELLED)
            .order_by("-return_round", "-id")
            .first()
        )
        if case is None:
            return None
        return {
            "id": case.pk,
            "case_no": case.case_no,
            "return_round": case.return_round,
            "return_label": f"第{case.return_round}次退货返工" if case.return_round else "退货返工记录",
            "status": case.status,
            "opened_on": case.opened_on,
        }


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
    order = serializers.SerializerMethodField()
    product_specification_id = serializers.PrimaryKeyRelatedField(
        source="product_specification", queryset=ProductSpecification.objects.filter(is_active=True),
        required=False, allow_null=True,
    )
    product_specification = serializers.SerializerMethodField()
    batch_id = serializers.PrimaryKeyRelatedField(source="batch", queryset=QualityShipmentBatch.objects.all(), required=False, write_only=True)
    specification_snapshot = serializers.CharField(required=False, allow_blank=True)
    material_snapshot = serializers.CharField(required=False, allow_blank=True)
    unit_weight_g_snapshot = serializers.DecimalField(
        max_digits=14, decimal_places=5, required=False, allow_null=True
    )
    single_batch_net_weight_kg = serializers.DecimalField(
        max_digits=14, decimal_places=3, required=False, allow_null=True,
        min_value=Decimal("0.001"),
    )
    process_card_shipment_quantity = serializers.IntegerField(
        required=False, allow_null=True, min_value=1
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
            "id", "batch_id", "process_card", "process_card_id", "order", "order_id",
            "product_specification", "product_specification_id", "specification_snapshot", "material_snapshot",
            "net_weight_kg", "single_batch_net_weight_kg", "piece_quantity",
            "unit_weight_g_snapshot", "process_card_shipment_quantity",
            "product_batch_count", "pieces_per_batch", "theoretical_weight_kg_snapshot",
            "max_allowed_weight_kg_snapshot", "notes", "created_at", "updated_at",
        ]
        read_only_fields = [
            "theoretical_weight_kg_snapshot", "max_allowed_weight_kg_snapshot",
            "created_at", "updated_at",
        ]

    def get_order(self, obj) -> dict | None:
        order = obj.order or (obj.process_card.order if obj.process_card_id else None)
        if order is None:
            return None
        return QualityOrderSerializer(order, context=self.context).data

    def get_product_specification(self, obj) -> dict | None:
        product = obj.product_specification
        if product is None and obj.process_card_id:
            product = obj.process_card.product_specification
        if product is None:
            order = obj.order or (obj.process_card.order if obj.process_card_id else None)
            product = order.product_specification if order else None
        if product is None:
            return None
        return ProductSpecificationReferenceSerializer(product, context=self.context).data

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
        if "single_batch_net_weight_kg" not in data:
            for alias in ("single_batch_weight_kg", "per_batch_net_weight_kg"):
                if alias in data:
                    data["single_batch_net_weight_kg"] = data.pop(alias)
                    break
        if "process_card_shipment_quantity" not in data:
            for alias in ("process_card_quantity", "card_shipment_quantity"):
                if alias in data:
                    data["process_card_shipment_quantity"] = data.pop(alias)
                    break
        if "piece_quantity" not in data and "quantity" in data:
            data["piece_quantity"] = data.pop("quantity")
        if "product_batch_count" not in data and "batch_count" in data:
            data["product_batch_count"] = data.pop("batch_count")
        # Model validation runs after DRF field validation.  Expand the
        # repeated single-batch reading here so a payload can omit the legacy
        # total-weight field without being rejected as null.
        single_weight = data.get("single_batch_net_weight_kg")
        if single_weight not in (None, ""):
            try:
                repeat_count = int(data.get("product_batch_count") or 1)
                data["net_weight_kg"] = str(
                    (Decimal(str(single_weight)) * Decimal(repeat_count)).quantize(
                        Decimal("0.001"), rounding=ROUND_HALF_UP
                    )
                )
            except (ArithmeticError, TypeError, ValueError):
                pass
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
    shipment_no = serializers.CharField(
        required=False, allow_blank=True, validators=[]
    )
    client_key = serializers.CharField(required=False, allow_blank=True, validators=[])
    order_id = serializers.PrimaryKeyRelatedField(
        source="order", queryset=QualityOrder.objects.all(), required=False,
        allow_null=True,
    )
    order = serializers.SerializerMethodField()
    product_specification_id = serializers.PrimaryKeyRelatedField(
        source="product_specification", queryset=ProductSpecification.objects.filter(is_active=True),
        required=False, allow_null=True,
    )
    product_specification = serializers.SerializerMethodField()
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
    single_batch_net_weight_kg = serializers.DecimalField(
        max_digits=14, decimal_places=3, required=False, allow_null=True,
        min_value=Decimal("0.001"),
    )
    process_card_shipment_quantity = serializers.IntegerField(
        required=False, allow_null=True, min_value=1
    )
    actual_weight_kg = serializers.DecimalField(max_digits=14, decimal_places=3, read_only=True)
    shipped_quantity = serializers.IntegerField(read_only=True)
    # Accepted as a legacy one-line input alias; the line serializer still
    # recalculates it for the new contract before confirmation.
    piece_quantity = serializers.IntegerField(required=False, allow_null=True)
    line_count = serializers.IntegerField(read_only=True)
    date_pending = serializers.BooleanField(read_only=True)
    warnings = serializers.SerializerMethodField()
    process_card_bindings = ProcessCardUnitBindingSerializer(many=True, read_only=True)
    class Meta:
        model = QualityShipmentBatch
        fields = [
            "id", "shipment_no", "client_key", "shipment_date", "order", "order_id",
            "product_specification", "product_specification_id", "product_name_snapshot", "specification_snapshot",
            "material_snapshot", "unit_weight_g", "single_batch_net_weight_kg",
            "process_card_shipment_quantity", "product_batch_count", "pieces_per_batch",
            "inspector", "inspector_id", "inspectors", "inspector_ids", "status", "customer",
            "delivery_info", "backfill_reason", "notes", "lines", "net_weight_kg",
            "total_net_weight_kg", "actual_weight_kg", "shipped_quantity", "piece_quantity",
            "line_count", "date_pending", "warnings", "process_card_bindings", "created_by", "created_at", "updated_at",
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
        if "single_batch_net_weight_kg" not in data:
            for alias in ("single_batch_weight_kg", "per_batch_net_weight_kg"):
                if alias in data:
                    data["single_batch_net_weight_kg"] = data.get(alias)
                    break
        # Browser number multiplication can expose an IEEE-754 tail before
        # DRF decimal validation (for example 10.2 * 34 becomes
        # 346.79999999999995).  The single reading and repeat count are the
        # authoritative inputs, so derive and quantize only the total before
        # max_digits checks.  Keep the single reading untouched so its declared
        # three-decimal field still rejects over-precise operator input.
        single_weight = data.get("single_batch_net_weight_kg")
        if single_weight not in (None, ""):
            try:
                single_decimal = Decimal(str(single_weight))
                repeat_value = data.get("product_batch_count")
                if repeat_value in (None, "") and self.instance is not None:
                    repeat_value = self.instance.product_batch_count
                repeat_count = int(repeat_value or 1)
                data["total_net_weight_kg"] = str(
                    (single_decimal * Decimal(repeat_count)).quantize(
                        Decimal("0.001"), rounding=ROUND_HALF_UP
                    )
                )
            except (ArithmeticError, TypeError, ValueError):
                # Let the declared serializer fields return their existing,
                # more specific validation messages for malformed inputs.
                pass
        # During the rolling upgrade, the existing form still calls its one
        # scale reading ``total_net_weight_kg``.  Preserve that input as the
        # single-batch snapshot; the line stores the expanded actual total.
        if "process_card_shipment_quantity" not in data:
            for alias in ("process_card_quantity", "card_shipment_quantity"):
                if alias in data:
                    data["process_card_shipment_quantity"] = data.get(alias)
                    break
        if "unit_weight_g" not in data and "unit_weight_g_snapshot" in data:
            data["unit_weight_g"] = data.get("unit_weight_g_snapshot")
        return super().to_internal_value(data)

    def to_representation(self, instance):
        payload = super().to_representation(instance)
        primary = payload.get("inspector_id")
        if primary:
            inspector_ids = payload.get("inspector_ids") or []
            payload["inspector_ids"] = [primary] + [
                value for value in inspector_ids if value != primary
            ]
            inspectors = payload.get("inspectors") or []
            primary_item = payload.get("inspector")
            payload["inspectors"] = [
                *([primary_item] if primary_item else []),
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

    def _representative_line(self, obj):
        prefetched = getattr(obj, "_prefetched_objects_cache", {}).get("lines")
        if prefetched is not None:
            return prefetched[0] if prefetched else None
        return obj.lines.select_related(
            "order__product_specification",
            "process_card__order__product_specification",
            "product_specification",
        ).order_by("id").first()

    def get_order(self, obj) -> dict | None:
        order = obj.order
        if order is None:
            line = self._representative_line(obj)
            if line is not None:
                order = line.order or (
                    line.process_card.order if line.process_card_id else None
                )
        if order is None:
            return None
        return QualityOrderSerializer(order, context=self.context).data

    def get_product_specification(self, obj) -> dict | None:
        product = obj.product_specification
        if product is None:
            line = self._representative_line(obj)
            if line is not None:
                product = line.product_specification
                if product is None and line.process_card_id:
                    product = line.process_card.product_specification
                if product is None:
                    order = line.order or (
                        line.process_card.order if line.process_card_id else None
                    )
                    product = order.product_specification if order else None
        if product is None and obj.order_id:
            product = obj.order.product_specification
        if product is None:
            return None
        return ProductSpecificationReferenceSerializer(product, context=self.context).data

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
                    "single_batch_net_weight_kg": instance.single_batch_net_weight_kg,
                    "process_card_shipment_quantity": instance.process_card_shipment_quantity,
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
            "single_batch_net_weight_kg": batch_data.get("single_batch_net_weight_kg"),
            "process_card_shipment_quantity": batch_data.get("process_card_shipment_quantity"),
            "product_batch_count": batch_data.get("product_batch_count"),
            "pieces_per_batch": batch_data.get("pieces_per_batch"),
            "piece_quantity": batch_piece_quantity,
        }
        # ``total_net_weight_kg`` is a write-only batch input.  A line's own
        # value always wins; otherwise use it for the one-line form.
        total = batch_data.get("_total_net_weight_kg_input", batch_data.get("total_net_weight_kg"))
        single = batch_data.get("single_batch_net_weight_kg")
        if (total is not None or single is not None) and len(lines) != 1:
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
        for line in lines:
            for key, value in defaults.items():
                if value is not None and value != "" and line.get(key) in (None, ""):
                    line[key] = value
            line_single = line.get("single_batch_net_weight_kg")
            if line_single is None and single is not None:
                line_single = single
                line["single_batch_net_weight_kg"] = single
            if line_single is not None:
                repeat_count = int(line.get("product_batch_count") or 1)
                card = line.get("process_card")
                if line.get("process_card_shipment_quantity") is None and card is not None:
                    line["process_card_shipment_quantity"] = card.quantity
                if line.get("process_card_shipment_quantity") is None:
                    raise serializers.ValidationError(
                        {
                            "process_card_shipment_quantity": (
                                "重复称重出货必须填写单批流程卡出货数量。"
                            )
                        }
                    )
                line["net_weight_kg"] = (
                    Decimal(line_single) * Decimal(repeat_count)
                ).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
                unit = line.get("unit_weight_g_snapshot")
                if unit is None and card is not None:
                    unit = card.unit_weight_g
                if unit:
                    single_pieces = int(
                        (
                            Decimal(line_single) * Decimal("1000") / Decimal(unit)
                        ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
                    )
                    line["pieces_per_batch"] = single_pieces
                    line["piece_quantity"] = single_pieces * repeat_count
                    standard = line.get("process_card_shipment_quantity")
                    if (
                        standard is not None
                        and Decimal(single_pieces) > Decimal(standard) * Decimal("1.10")
                    ):
                        raise serializers.ValidationError(
                            {
                                "process_card_shipment_quantity": (
                                    "单批实际出货数量不能超过流程卡出货数量的110%。"
                                )
                            }
                        )
                else:
                    # Model validation will return the existing unit-weight
                    # error with the correct process-card/order context.
                    line["piece_quantity"] = None
            elif total is not None and line.get("net_weight_kg") in (None, ""):
                line["net_weight_kg"] = total


class QualityReturnableBatchLineSummarySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    shipment_line_id = serializers.IntegerField()
    order_id = serializers.IntegerField(allow_null=True)
    order_no = serializers.CharField(allow_blank=True)
    item_no = serializers.CharField(allow_blank=True)
    process_card_id = serializers.IntegerField(allow_null=True)
    process_card_no = serializers.CharField(allow_blank=True)
    card_no = serializers.CharField(allow_blank=True)
    piece_quantity = serializers.IntegerField()
    net_weight_kg = serializers.DecimalField(max_digits=14, decimal_places=3)


class QualityReturnableInspectorSummarySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    employee_no = serializers.CharField()
    name = serializers.CharField()


class QualityReturnableBatchCandidateSerializer(serializers.Serializer):
    key = serializers.CharField()
    source_type = serializers.CharField()
    shipment_batch_id = serializers.IntegerField()
    shipment_line_id = serializers.IntegerField(allow_null=True)
    shipment_no = serializers.CharField()
    shipment_date = serializers.DateField(allow_null=True)
    order_ids = serializers.ListField(child=serializers.IntegerField())
    order_no = serializers.CharField(allow_blank=True)
    order_nos = serializers.ListField(child=serializers.CharField())
    item_no = serializers.CharField(allow_blank=True)
    item_nos = serializers.ListField(child=serializers.CharField())
    product_name = serializers.CharField(allow_blank=True)
    product_names = serializers.ListField(child=serializers.CharField())
    specification = serializers.CharField(allow_blank=True)
    specifications = serializers.ListField(child=serializers.CharField())
    material = serializers.CharField(allow_blank=True)
    materials = serializers.ListField(child=serializers.CharField())
    single_batch_net_weight_kg = serializers.DecimalField(
        max_digits=14, decimal_places=3
    )
    pieces_per_batch = serializers.IntegerField()
    total_batches = serializers.IntegerField()
    available_batches = serializers.IntegerField()
    available_batch_numbers = serializers.ListField(child=serializers.IntegerField())
    returned_batches = serializers.IntegerField()
    returned_batch_numbers = serializers.ListField(child=serializers.IntegerField())
    rework_count = serializers.IntegerField()
    next_return_no = serializers.IntegerField()
    inspectors = QualityReturnableInspectorSummarySerializer(many=True)
    lines = QualityReturnableBatchLineSummarySerializer(many=True)


class QualityReturnableBatchPageSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    next = serializers.URLField(allow_null=True)
    previous = serializers.URLField(allow_null=True)
    results = QualityReturnableBatchCandidateSerializer(many=True)


class ProcessCardBindingInputSerializer(serializers.Serializer):
    shipment_unit_no = serializers.IntegerField(min_value=1)
    card_no = serializers.CharField(max_length=150)
    order_id = serializers.IntegerField(min_value=1, required=False, allow_null=True)


class ProcessCardBindingRequestSerializer(serializers.Serializer):
    cards = ProcessCardBindingInputSerializer(many=True, allow_empty=False)


class ProcessCardReplaceRequestSerializer(serializers.Serializer):
    new_card_no = serializers.CharField(max_length=150)
    notes = serializers.CharField(required=False, allow_blank=True)


class ScannedReturnRequestSerializer(serializers.Serializer):
    card_no = serializers.CharField(max_length=150)
    shipment_batch_id = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    shipment_unit_no = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    order_id = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    opened_on = serializers.DateField(required=False, allow_null=True)
    date_is_approximate = serializers.BooleanField(required=False, default=False)
    backfill_reason = serializers.CharField(required=False, allow_blank=True)
    reason_category = serializers.ChoiceField(
        choices=ReturnRework.ReasonCategory.choices,
        required=False,
    )
    primary_reason_id = serializers.PrimaryKeyRelatedField(
        source="primary_reason",
        queryset=DefectReason.objects.filter(is_active=True),
        required=False,
        allow_null=True,
    )
    secondary_reason_ids = serializers.PrimaryKeyRelatedField(
        source="secondary_reasons",
        queryset=DefectReason.objects.filter(is_active=True),
        many=True,
        required=False,
    )
    reason = serializers.CharField(required=False, allow_blank=True)
    inspector_ids = serializers.PrimaryKeyRelatedField(
        source="inspectors",
        queryset=QualityEmployee.objects.filter(
            is_active=True,
            role__in=[QualityEmployee.Role.INSPECTOR, QualityEmployee.Role.BOTH],
        ),
        many=True,
        required=False,
    )
    notes = serializers.CharField(required=False, allow_blank=True)


class BulkScannedReturnRequestSerializer(serializers.Serializer):
    cards = serializers.ListField(
        child=serializers.DictField(), allow_empty=False
    )
    opened_on = serializers.DateField(required=False, allow_null=True)
    date_is_approximate = serializers.BooleanField(required=False, default=False)
    backfill_reason = serializers.CharField(required=False, allow_blank=True)
    reason_category = serializers.ChoiceField(
        choices=ReturnRework.ReasonCategory.choices,
        required=False,
    )
    primary_reason_id = serializers.PrimaryKeyRelatedField(
        source="primary_reason",
        queryset=DefectReason.objects.filter(is_active=True),
        required=False,
        allow_null=True,
    )
    secondary_reason_ids = serializers.PrimaryKeyRelatedField(
        source="secondary_reasons",
        queryset=DefectReason.objects.filter(is_active=True),
        many=True,
        required=False,
    )
    reason = serializers.CharField(required=False, allow_blank=True)
    inspector_ids = serializers.PrimaryKeyRelatedField(
        source="inspectors",
        queryset=QualityEmployee.objects.filter(
            is_active=True,
            role__in=[QualityEmployee.Role.INSPECTOR, QualityEmployee.Role.BOTH],
        ),
        many=True,
        required=False,
    )
    notes = serializers.CharField(required=False, allow_blank=True)

    def validate_cards(self, values):
        serializer = ScannedReturnRequestSerializer(data=values, many=True)
        serializer.is_valid(raise_exception=True)
        return serializer.validated_data


class ReturnReshipRequestSerializer(serializers.Serializer):
    shipment_date = serializers.DateField(required=False, allow_null=True)
    net_weight_kg = serializers.DecimalField(
        max_digits=14, decimal_places=3, min_value=Decimal("0.001"),
        required=False,
    )
    piece_quantity = serializers.IntegerField(min_value=1, required=False)
    inspector_ids = serializers.PrimaryKeyRelatedField(
        source="inspectors",
        queryset=QualityEmployee.objects.filter(
            is_active=True,
            role__in=[QualityEmployee.Role.INSPECTOR, QualityEmployee.Role.BOTH],
        ),
        many=True,
        required=False,
    )
    notes = serializers.CharField(required=False, allow_blank=True)


class BindExistingReturnRequestSerializer(serializers.Serializer):
    card_no = serializers.CharField(max_length=150)
    order_id = serializers.IntegerField(min_value=1, required=False, allow_null=True)


class QualityReworkCaseSerializer(ValidatedModelSerializer):
    process_card_id = serializers.PrimaryKeyRelatedField(source="process_card", queryset=ProcessCard.objects.all(), required=False, allow_null=True)
    shipment_line_id = serializers.PrimaryKeyRelatedField(source="shipment_line", queryset=QualityShipmentLine.objects.all(), required=False, allow_null=True)
    shipment_batch_id = serializers.PrimaryKeyRelatedField(source="shipment_batch", queryset=QualityShipmentBatch.objects.all(), required=False, allow_null=True)
    shipment_unit_no = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    responsible_inspector_id = serializers.PrimaryKeyRelatedField(source="responsible_inspector", queryset=QualityEmployee.objects.filter(is_active=True, role__in=[QualityEmployee.Role.INSPECTOR, QualityEmployee.Role.BOTH]), required=False, allow_null=True)
    attempt_count = serializers.IntegerField(read_only=True)
    attempts = serializers.SerializerMethodField()
    source = serializers.SerializerMethodField()
    reason_category_display = serializers.CharField(
        source="get_reason_category_display", read_only=True
    )
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    return_label = serializers.SerializerMethodField()
    process_card_no = serializers.CharField(source="process_card.card_no", read_only=True)
    active_process_card_no = serializers.SerializerMethodField()
    binding_pending = serializers.SerializerMethodField()
    responsible_inspectors = serializers.SerializerMethodField()
    inspector_ids = serializers.PrimaryKeyRelatedField(
        source="inspectors",
        queryset=QualityEmployee.objects.filter(
            is_active=True,
            role__in=[QualityEmployee.Role.INSPECTOR, QualityEmployee.Role.BOTH],
        ),
        many=True,
        required=False,
        write_only=True,
    )
    primary_reason_detail = DefectReasonSerializer(source="primary_reason", read_only=True)
    primary_reason_id = serializers.PrimaryKeyRelatedField(
        source="primary_reason",
        queryset=DefectReason.objects.filter(is_active=True),
        required=False,
        allow_null=True,
        write_only=True,
    )
    secondary_reason_details = serializers.SerializerMethodField()
    secondary_reason_ids = serializers.PrimaryKeyRelatedField(
        source="secondary_reasons",
        queryset=DefectReason.objects.filter(is_active=True),
        many=True,
        required=False,
        write_only=True,
    )
    class Meta:
        model = QualityReworkCase
        fields = ["id", "case_no", "origin", "process_card_id", "process_card_no", "active_process_card_no", "binding_pending", "shipment_line_id", "shipment_batch_id", "shipment_unit_no", "source", "return_round", "return_label", "is_current_return", "opened_on", "date_is_approximate", "backfill_reason", "reason_category", "reason_category_display", "primary_reason_id", "primary_reason_detail", "secondary_reason_ids", "secondary_reason_details", "reason", "responsible_inspector_id", "responsible_inspectors", "inspector_ids", "affected_quantity", "affected_weight_kg", "status", "status_display", "closed_on", "notes", "attempt_count", "attempts", "created_by", "created_at", "updated_at"]
        read_only_fields = ["case_no", "source", "return_round", "is_current_return", "attempt_count", "attempts", "created_by", "created_at", "updated_at"]
        # The conditional database uniqueness rule applies only to new
        # whole-batch customer returns.  DRF's generated validator incorrectly
        # makes both nullable fields mandatory for internal/historical cases
        # and raises KeyError on status-only PATCH requests, so creation-time
        # locking plus model validation enforce it instead.
        validators = []

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if "primary_reason" in attrs and "reason_category" not in attrs:
            attrs["reason_category"] = legacy_reason_category(attrs["primary_reason"])
        primary_reason = attrs.get(
            "primary_reason", getattr(self.instance, "primary_reason", None)
        )
        secondary_reasons = attrs.get("secondary_reasons")
        if (
            primary_reason is not None
            and secondary_reasons is not None
            and any(value.pk == primary_reason.pk for value in secondary_reasons)
        ):
            raise serializers.ValidationError(
                {"secondary_reason_ids": "主要原因不能同时作为次要问题标签。"}
            )
        if (
            self.instance is not None
            and self.instance.status == QualityReworkCase.Status.CANCELLED
            and attrs
        ):
            raise serializers.ValidationError(
                {"detail": "已取消的退货登记是审计记录，不能再修改。"}
            )
        process_card = attrs.get("process_card", getattr(self.instance, "process_card", None))
        shipment_line = attrs.get("shipment_line", getattr(self.instance, "shipment_line", None))
        shipment_batch = attrs.get("shipment_batch", getattr(self.instance, "shipment_batch", None))
        shipment_unit_no = attrs.get("shipment_unit_no", getattr(self.instance, "shipment_unit_no", None))
        origin = attrs.get("origin", getattr(self.instance, "origin", QualityReworkCase.Origin.INTERNAL))
        immutable = ("origin", "process_card", "shipment_line", "shipment_batch", "shipment_unit_no", "affected_quantity", "affected_weight_kg")
        customer_return = bool(
            self.instance is not None
            and self.instance.origin == QualityReworkCase.Origin.CUSTOMER_RETURN
        )
        if customer_return:
            # Reject every supplied source fact, even when its value equals the
            # current value.  This makes the update contract unambiguous and
            # prevents clients from treating immutable audit fields as edits.
            # The same protection applies to historical customer-return rows
            # that predate explicit physical unit numbers.
            changed = [field for field in immutable if field in attrs]
            if changed:
                raise serializers.ValidationError(
                    {
                        field: "整批退货创建后不能修改来源、批号、件数或重量。"
                        for field in changed
                    }
                )
        if (
            process_card is not None
            and shipment_line is not None
            and shipment_line.process_card_id is not None
            and process_card.pk != shipment_line.process_card_id
        ):
            raise serializers.ValidationError({"shipment_line": "返工关联的出货明细必须属于所选流程卡。"})
        if shipment_line is not None and shipment_batch is not None and shipment_line.batch_id != shipment_batch.pk:
            raise serializers.ValidationError({"shipment_line_id": "原出货明细不属于所选出货批次。"})
        if shipment_batch is not None and origin != QualityReworkCase.Origin.CUSTOMER_RETURN:
            raise serializers.ValidationError({"origin": "原出货批次仅适用于客户退货返工。"})
        if self.instance is None and shipment_batch is not None and shipment_unit_no is None:
            raise serializers.ValidationError({"shipment_unit_no": "请选择要退回的整批序号。"})
        if self.instance is None and shipment_unit_no is not None and shipment_batch is None:
            raise serializers.ValidationError({"shipment_batch_id": "请选择原出货记录。"})
        if self.instance is None and shipment_batch is not None and attrs.get("status") == QualityReworkCase.Status.CANCELLED:
            raise serializers.ValidationError({"status": "不能直接创建已取消的退货返工记录。"})
        if self.instance is not None and not customer_return and self.instance.attempts.exists():
            changed = [field for field in immutable if field in attrs and attrs[field] != getattr(self.instance, field)]
            if changed:
                raise serializers.ValidationError(
                    {field: "已有返工轮次，不能修改该历史数量或关联。" for field in changed}
                )
        return attrs

    def create(self, validated_data):
        inspectors = validated_data.pop("inspectors", [])
        secondary_reasons = validated_data.pop("secondary_reasons", [])
        if (
            validated_data.get("origin") == QualityReworkCase.Origin.CUSTOMER_RETURN
            and validated_data.get("shipment_batch") is not None
            and validated_data.get("shipment_unit_no") is not None
        ):
            try:
                case = create_whole_batch_return_case(validated_data)
                if inspectors:
                    case.inspectors.set(inspectors)
                if secondary_reasons:
                    case.secondary_reasons.set(secondary_reasons)
                self._sync_case_orders(case, "登记客户退货")
                return case
            except ValueError as exc:
                raise serializers.ValidationError({"detail": str(exc)}) from exc
        case = super().create(validated_data)
        if inspectors:
            case.inspectors.set(inspectors)
        if secondary_reasons:
            case.secondary_reasons.set(secondary_reasons)
        if case.origin == QualityReworkCase.Origin.CUSTOMER_RETURN:
            self._sync_case_orders(case, "登记客户退货")
        return case

    def update(self, instance, validated_data):
        # Serialize corrections to the same registered return while the
        # immutable shipment/batch/quantity facts remain protected above.
        inspectors = validated_data.pop("inspectors", None)
        secondary_reasons = validated_data.pop("secondary_reasons", None)
        with transaction.atomic():
            locked = QualityReworkCase.objects.select_for_update().get(pk=instance.pk)
            case = super().update(locked, validated_data)
            if (
                case.origin == QualityReworkCase.Origin.CUSTOMER_RETURN
                and case.status == QualityReworkCase.Status.CANCELLED
                and case.is_current_return
            ):
                case.is_current_return = False
                case.save(update_fields=["is_current_return", "updated_at"])
            if inspectors is not None:
                case.inspectors.set(inspectors)
                if inspectors and case.responsible_inspector_id is None:
                    case.responsible_inspector = inspectors[0]
                    case.save(update_fields=["responsible_inspector", "updated_at"])
            if secondary_reasons is not None:
                case.secondary_reasons.set(secondary_reasons)
            if case.origin == QualityReworkCase.Origin.CUSTOMER_RETURN:
                self._sync_case_orders(case, "更新或取消客户退货")
            return case

    @staticmethod
    def _sync_case_orders(case, reason):
        if case.process_card_id:
            case.process_card.refresh_shipping_status()
        order_ids = set(
            case.shipment_allocations.values_list(
                "shipment_line__order_id", flat=True
            )
        )
        if case.shipment_line_id:
            line = case.shipment_line
            order_ids.add(
                line.order_id
                or (line.process_card.order_id if line.process_card_id else None)
            )
        if case.process_card_id:
            order_ids.add(case.process_card.order_id)
        order_ids.discard(None)
        sync_order_status_from_delivery(
            order_ids,
            source="CUSTOMER_RETURN",
            operator=case.created_by,
            reason_prefix=f"{reason} {case.case_no}。",
        )

    def get_source(self, obj) -> dict | None:
        return serialize_rework_source(obj)

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

    def get_return_label(self, obj) -> str:
        return (
            f"第{obj.return_round}次退货返工"
            if obj.return_round
            else "退货返工记录"
        )

    def get_active_process_card_no(self, obj) -> str | None:
        return obj.process_card.active_replacement.card_no if obj.process_card_id else None

    def get_binding_pending(self, obj) -> bool:
        return obj.origin == QualityReworkCase.Origin.CUSTOMER_RETURN and not obj.process_card_id

    def get_responsible_inspectors(self, obj) -> list[dict]:
        values = getattr(obj, "_prefetched_objects_cache", {}).get("inspectors", [])
        return QualityEmployeeSerializer(values, many=True, context=self.context).data

    def get_secondary_reason_details(self, obj) -> list[dict]:
        values = getattr(obj, "_prefetched_objects_cache", {}).get("secondary_reasons", [])
        return DefectReasonSerializer(values, many=True, context=self.context).data


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
            raise serializers.ValidationError({"case_id": "返工轮次关联的退货返工记录创建后不能更换。"})
        case = attrs.get("case") or (
            self.instance.case if self.instance is not None else None
        )
        if (
            self.instance is not None
            and case is not None
            and case.origin == QualityReworkCase.Origin.CUSTOMER_RETURN
            and case.shipment_unit_no is not None
        ):
            expected_quantity = int(case.affected_quantity or 0)
            expected_weight = Decimal(case.affected_weight_kg or 0)
            errors = {}
            for field_name in ("input_quantity", "reworked_quantity"):
                if (
                    field_name in attrs
                    and int(attrs[field_name] or 0) != expected_quantity
                ):
                    errors[field_name] = "整批退货的每轮投入和返工件数必须等于原整批件数。"
            for field_name in ("input_weight_kg", "reworked_weight_kg"):
                if (
                    field_name in attrs
                    and Decimal(attrs[field_name] or 0) != expected_weight
                ):
                    errors[field_name] = "整批退货的每轮投入和返工重量必须等于原整批净重。"
            if errors:
                raise serializers.ValidationError(errors)
        return attrs

    @staticmethod
    def _freeze_whole_batch_inputs(case, validated_data):
        if (
            case.origin == QualityReworkCase.Origin.CUSTOMER_RETURN
            and case.shipment_unit_no is not None
        ):
            validated_data["input_quantity"] = int(case.affected_quantity or 0)
            validated_data["reworked_quantity"] = int(case.affected_quantity or 0)
            validated_data["input_weight_kg"] = Decimal(
                case.affected_weight_kg or 0
            )
            validated_data["reworked_weight_kg"] = Decimal(
                case.affected_weight_kg or 0
            )

    def create(self, validated_data):
        case = validated_data.get("case")
        if case is None:
            return super().create(validated_data)
        with transaction.atomic():
            locked_case = QualityReworkCase.objects.select_for_update().get(pk=case.pk)
            if locked_case.status in (
                QualityReworkCase.Status.CANCELLED,
                QualityReworkCase.Status.SCRAPPED,
            ):
                raise serializers.ValidationError(
                    {"case_id": "已取消或已报废的退货返工记录不能新增轮次。"}
                )
            validated_data["case"] = locked_case
            # Each R1/R2/R3 round processes the same physical batch.  Freeze
            # these four source facts even when clients omit them.
            self._freeze_whole_batch_inputs(locked_case, validated_data)
            return super().create(validated_data)

    def update(self, instance, validated_data):
        with transaction.atomic():
            locked_instance = QualityReworkAttempt.objects.select_for_update().select_related("case").get(pk=instance.pk)
            locked_case = QualityReworkCase.objects.select_for_update().get(pk=locked_instance.case_id)
            validated_data["case"] = locked_case
            self._freeze_whole_batch_inputs(locked_case, validated_data)
            return super().update(locked_instance, validated_data)

    def get_attempt_label(self, obj) -> str | None:
        return f"R{obj.attempt_no}" if obj.attempt_no else None
