from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import serializers

from .models import (
    Machine,
    MoldAsset,
    MoldModel,
    MoldMovement,
    Processor,
    Rack,
    RackSlot,
    RackZone,
)
from .services import ConfirmationRequired, stacking_warnings, validate_slot


class MoldModelSerializer(serializers.ModelSerializer):
    asset_count = serializers.IntegerField(read_only=True, required=False)

    class Meta:
        model = MoldModel
        fields = ["id", "code", "product_name", "description", "is_active", "asset_count", "created_at", "updated_at"]
        read_only_fields = ["created_at", "updated_at"]


class MachineSerializer(serializers.ModelSerializer):
    current_mold_count = serializers.IntegerField(read_only=True, required=False)

    class Meta:
        model = Machine
        fields = ["id", "code", "name", "is_active", "current_mold_count", "created_at", "updated_at"]
        read_only_fields = ["created_at", "updated_at"]


class ProcessorSerializer(serializers.ModelSerializer):
    current_mold_count = serializers.IntegerField(read_only=True, required=False)

    class Meta:
        model = Processor
        fields = ["id", "code", "name", "contact", "phone", "is_active", "current_mold_count", "created_at", "updated_at"]
        read_only_fields = ["created_at", "updated_at"]


class RackSummarySerializer(serializers.ModelSerializer):
    level_count = serializers.IntegerField(read_only=True, required=False)

    class Meta:
        model = Rack
        fields = [
            "id",
            "code",
            "name",
            "is_configured",
            "structure_locked",
            "layout_version",
            "is_active",
            "level_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["is_configured", "structure_locked", "layout_version", "created_at", "updated_at"]


class SlotSerializer(serializers.ModelSerializer):
    rack_id = serializers.IntegerField(source="zone.level.rack_id", read_only=True)
    rack_code = serializers.CharField(source="zone.level.rack.code", read_only=True)
    level_no = serializers.IntegerField(source="zone.level.level_no", read_only=True)
    zone_id = serializers.IntegerField(read_only=True)
    zone_code = serializers.CharField(source="zone.code", read_only=True)
    zone_label = serializers.CharField(source="zone.label", read_only=True)
    supports_stacking = serializers.BooleanField(source="zone.supports_stacking", read_only=True)
    stacking_enabled = serializers.BooleanField(source="zone.stacking_enabled", read_only=True)
    is_enabled = serializers.BooleanField(read_only=True)
    occupied = serializers.SerializerMethodField()
    occupant = serializers.SerializerMethodField()

    class Meta:
        model = RackSlot
        fields = [
            "id",
            "rack_id",
            "rack_code",
            "level_no",
            "zone_id",
            "zone_code",
            "zone_label",
            "capacity_mode",
            "position_no",
            "stack_level",
            "display_code",
            "technical_code",
            "supports_stacking",
            "stacking_enabled",
            "is_enabled",
            "is_blocked",
            "blocking_reason",
            "occupied",
            "occupant",
        ]

    def get_occupied(self, obj) -> bool:
        return hasattr(obj, "occupant")

    def get_occupant(self, obj) -> dict | None:
        occupant = getattr(obj, "occupant", None)
        if not occupant:
            return None
        return {
            "id": occupant.id,
            "asset_code": occupant.asset_code,
            "model_code": occupant.mold_model.code,
            "product_name": occupant.mold_model.product_name,
        }


class MoldAssetSerializer(serializers.ModelSerializer):
    mold_model = MoldModelSerializer(read_only=True)
    mold_model_id = serializers.PrimaryKeyRelatedField(
        source="mold_model", queryset=MoldModel.objects.filter(is_active=True), write_only=True
    )
    slot = SlotSerializer(source="current_slot", read_only=True)
    machine = MachineSerializer(source="current_machine", read_only=True)
    processor = ProcessorSerializer(source="current_processor", read_only=True)
    slot_id = serializers.PrimaryKeyRelatedField(
        source="initial_slot", queryset=RackSlot.objects.select_related("zone__level__rack"), write_only=True, required=False
    )
    image = serializers.ImageField(source="main_image", required=False, allow_null=True)
    can_stack = serializers.BooleanField(source="allows_stacking", required=False)
    note = serializers.CharField(source="notes", required=False, allow_blank=True)
    confirm_warnings = serializers.BooleanField(write_only=True, required=False, default=False)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    location_text = serializers.SerializerMethodField()

    class Meta:
        model = MoldAsset
        fields = [
            "id",
            "asset_code",
            "mold_model",
            "mold_model_id",
            "image",
            "status",
            "status_label",
            "slot",
            "slot_id",
            "machine",
            "processor",
            "location_text",
            "can_stack",
            "note",
            "confirm_warnings",
            "is_active",
            "status_changed_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["status", "status_changed_at", "created_at", "updated_at"]

    def get_location_text(self, obj) -> str:
        if obj.status == MoldAsset.Status.IN_STOCK and obj.current_slot:
            return obj.current_slot.display_code
        if obj.status == MoldAsset.Status.ON_MACHINE and obj.current_machine:
            return f"{obj.current_machine.code} - {obj.current_machine.name}"
        if obj.status == MoldAsset.Status.OUTSOURCED and obj.current_processor:
            return f"{obj.current_processor.code} - {obj.current_processor.name}"
        return ""

    def validate(self, attrs):
        if self.instance and "initial_slot" in attrs:
            raise serializers.ValidationError({"slot_id": "修改位置请使用归位或移库操作。"})
        if not self.instance and "initial_slot" not in attrs:
            raise serializers.ValidationError({"slot_id": "新建模具必须选择初始库位。"})
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        confirm_warnings = validated_data.pop("confirm_warnings", False)
        slot = validated_data.pop("initial_slot")
        slot = RackSlot.objects.select_for_update().select_related("zone__level__rack").get(pk=slot.pk)
        try:
            validate_slot(slot)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"slot_id": exc.messages})
        mold = MoldAsset(
            status=MoldAsset.Status.IN_STOCK,
            current_slot=slot,
            status_changed_at=timezone.now(),
            **validated_data,
        )
        try:
            warnings = stacking_warnings(mold, target_slot=slot)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"slot_id": exc.messages}) from exc
        if warnings and not confirm_warnings:
            raise ConfirmationRequired(warnings)
        try:
            mold.full_clean()
            mold.save()
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.message_dict if hasattr(exc, "message_dict") else exc.messages)
        except IntegrityError as exc:
            raise serializers.ValidationError({"slot_id": "目标库位已被占用。"}) from exc
        Rack.objects.filter(pk=slot.zone.level.rack_id).update(structure_locked=True)
        MoldMovement.objects.create(
            mold=mold,
            action=MoldMovement.Action.CREATE,
            to_status=mold.status,
            to_slot=slot,
            note="新建模具",
            operator=self.context["request"].user,
        )
        return mold

    def update(self, instance, validated_data):
        validated_data.pop("confirm_warnings", None)
        return super().update(instance, validated_data)


class MoldMovementSerializer(serializers.ModelSerializer):
    action_label = serializers.CharField(source="get_action_display", read_only=True)
    from_status_label = serializers.SerializerMethodField()
    to_status_label = serializers.CharField(source="get_to_status_display", read_only=True)
    from_slot = SlotSerializer(read_only=True)
    to_slot = SlotSerializer(read_only=True)
    from_machine = MachineSerializer(read_only=True)
    to_machine = MachineSerializer(read_only=True)
    from_processor = ProcessorSerializer(read_only=True)
    to_processor = ProcessorSerializer(read_only=True)
    operator_name = serializers.CharField(source="operator.username", read_only=True)
    action_display = serializers.CharField(source="get_action_display", read_only=True)
    from_location = serializers.SerializerMethodField()
    to_location = serializers.SerializerMethodField()

    class Meta:
        model = MoldMovement
        fields = [
            "id",
            "action",
            "action_label",
            "action_display",
            "from_status",
            "from_status_label",
            "to_status",
            "to_status_label",
            "from_slot",
            "to_slot",
            "from_machine",
            "to_machine",
            "from_processor",
            "to_processor",
            "note",
            "operator_name",
            "from_location",
            "to_location",
            "created_at",
        ]

    def get_from_status_label(self, obj) -> str:
        return dict(MoldAsset.Status.choices).get(obj.from_status, "")

    @staticmethod
    def _location(slot, machine, processor):
        if slot:
            return slot.display_code
        if machine:
            return f"{machine.code} - {machine.name}"
        if processor:
            return f"{processor.code} - {processor.name}"
        return None

    def get_from_location(self, obj) -> str | None:
        return self._location(obj.from_slot, obj.from_machine, obj.from_processor)

    def get_to_location(self, obj) -> str | None:
        return self._location(obj.to_slot, obj.to_machine, obj.to_processor)


class ZoneSerializer(serializers.ModelSerializer):
    rack_code = serializers.CharField(source="level.rack.code", read_only=True)
    level_no = serializers.IntegerField(source="level.level_no", read_only=True)

    class Meta:
        model = RackZone
        fields = [
            "id",
            "rack_code",
            "level_no",
            "code",
            "label",
            "allowed_capacities",
            "capacity_mode",
            "supports_stacking",
            "stacking_enabled",
            "is_active",
        ]


class MoldActionSerializer(serializers.Serializer):
    slot_id = serializers.PrimaryKeyRelatedField(
        source="slot",
        queryset=RackSlot.objects.select_related("zone__level__rack"),
        required=False,
    )
    machine_id = serializers.PrimaryKeyRelatedField(
        source="machine", queryset=Machine.objects.all(), required=False
    )
    processor_id = serializers.PrimaryKeyRelatedField(
        source="processor", queryset=Processor.objects.all(), required=False
    )
    note = serializers.CharField(required=False, allow_blank=True, max_length=1000)
    confirm_warnings = serializers.BooleanField(required=False, default=False)


class RackConfigSerializer(serializers.Serializer):
    level_count = serializers.IntegerField(min_value=1, max_value=30)
    zones = serializers.ListField(child=serializers.DictField(), min_length=1, max_length=4)


class CapacitySerializer(serializers.Serializer):
    capacity = serializers.IntegerField(min_value=1, max_value=20)


class StackingSerializer(serializers.Serializer):
    enabled = serializers.BooleanField()
