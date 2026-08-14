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

    def get_created_by_name(self, obj) -> str:
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
    process_card_id = serializers.PrimaryKeyRelatedField(source="process_card", queryset=ProcessCard.objects.all())
    batch_id = serializers.PrimaryKeyRelatedField(source="batch", queryset=QualityShipmentBatch.objects.all(), required=False, write_only=True)
    class Meta:
        model = QualityShipmentLine
        fields = ["id", "batch_id", "process_card", "process_card_id", "net_weight_kg", "piece_quantity", "unit_weight_g_snapshot", "theoretical_weight_kg_snapshot", "max_allowed_weight_kg_snapshot", "notes", "created_at", "updated_at"]
        read_only_fields = ["unit_weight_g_snapshot", "theoretical_weight_kg_snapshot", "max_allowed_weight_kg_snapshot", "created_at", "updated_at"]

    def to_internal_value(self, data):
        data = data.copy()
        if "process_card_id" not in data and "card_id" in data:
            data["process_card_id"] = data.pop("card_id")
        if "net_weight_kg" not in data and "weight_kg" in data:
            data["net_weight_kg"] = data.pop("weight_kg")
        if "net_weight_kg" not in data and "actual_weight_kg" in data:
            data["net_weight_kg"] = data.pop("actual_weight_kg")
        if "piece_quantity" not in data and "quantity" in data:
            data["piece_quantity"] = data.pop("quantity")
        return super().to_internal_value(data)


class QualityShipmentBatchSerializer(ValidatedModelSerializer):
    client_key = serializers.CharField(required=False, allow_blank=True, validators=[])
    inspector_id = serializers.PrimaryKeyRelatedField(source="inspector", queryset=QualityEmployee.objects.filter(is_active=True, role__in=[QualityEmployee.Role.INSPECTOR, QualityEmployee.Role.BOTH]), required=False, allow_null=True)
    inspector = QualityEmployeeSerializer(read_only=True)
    lines = QualityShipmentLineSerializer(many=True, required=False)
    net_weight_kg = serializers.DecimalField(max_digits=14, decimal_places=3, read_only=True)
    actual_weight_kg = serializers.DecimalField(max_digits=14, decimal_places=3, read_only=True)
    shipped_quantity = serializers.IntegerField(read_only=True)
    line_count = serializers.IntegerField(read_only=True)
    date_pending = serializers.BooleanField(read_only=True)
    warnings = serializers.SerializerMethodField()
    class Meta:
        model = QualityShipmentBatch
        fields = ["id", "shipment_no", "client_key", "shipment_date", "inspector", "inspector_id", "status", "customer", "delivery_info", "backfill_reason", "notes", "lines", "net_weight_kg", "actual_weight_kg", "shipped_quantity", "line_count", "date_pending", "warnings", "created_by", "created_at", "updated_at"]
        read_only_fields = ["created_by", "created_at", "updated_at", "net_weight_kg", "line_count", "date_pending"]

    def to_internal_value(self, data):
        data = data.copy()
        if "lines" not in data and "items" in data:
            data["lines"] = data.pop("items")
        if "shipment_no" not in data and "batch_no" in data:
            data["shipment_no"] = data.pop("batch_no")
        return super().to_internal_value(data)

    def get_warnings(self, obj) -> list[str]:
        warnings: list[str] = []
        for line in obj.lines.select_related("process_card"):
            theoretical = line.theoretical_weight_kg_snapshot or line.process_card.theoretical_weight_kg
            if theoretical and Decimal(line.net_weight_kg) < Decimal(theoretical):
                warnings.append(f"{line.process_card.card_no} 实际净重低于理论重量，请复核称重。")
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
        return attrs

    def create(self, validated_data):
        lines = validated_data.pop("lines", [])
        key = validated_data.get("client_key")
        try:
            with transaction.atomic():
                if key:
                    existing = QualityShipmentBatch.objects.filter(client_key=key).first()
                    if existing:
                        return existing
                batch = QualityShipmentBatch.objects.create(**validated_data)
                for item in lines:
                    item.pop("batch", None)
                    QualityShipmentLine.objects.create(batch=batch, **item)
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
        lines = validated_data.pop("lines", serializers.empty)
        if instance.status in (QualityShipmentBatch.Status.VOID, QualityShipmentBatch.Status.CONFIRMED):
            raise serializers.ValidationError({"status": "Only draft shipment batches can be edited."})
        with transaction.atomic():
            instance = QualityShipmentBatch.objects.select_for_update().get(pk=instance.pk)
            if instance.status in (QualityShipmentBatch.Status.VOID, QualityShipmentBatch.Status.CONFIRMED):
                raise serializers.ValidationError({"status": "只有草稿出货批次可以编辑。"})
            instance = super().update(instance, validated_data)
            if lines is not serializers.empty:
                instance.lines.all().delete()
                for item in lines:
                    item.pop("batch", None)
                    QualityShipmentLine.objects.create(batch=instance, **item)
            return instance


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
