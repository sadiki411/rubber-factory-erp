import re

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
    asset_code = serializers.CharField(required=False, allow_blank=True, max_length=100)
    mold_model = MoldModelSerializer(read_only=True)
    mold_model_id = serializers.PrimaryKeyRelatedField(
        source="mold_model",
        queryset=MoldModel.objects.filter(is_active=True),
        write_only=True,
        required=False,
    )
    model_code = serializers.CharField(write_only=True, required=False, max_length=100)
    product_name = serializers.CharField(write_only=True, required=False, allow_blank=True, max_length=200)
    slot = SlotSerializer(source="current_slot", read_only=True)
    machine = MachineSerializer(source="current_machine", read_only=True)
    processor = ProcessorSerializer(source="current_processor", read_only=True)
    slot_id = serializers.PrimaryKeyRelatedField(
        source="initial_slot", queryset=RackSlot.objects.select_related("zone__level__rack"), write_only=True, required=False
    )
    machine_id = serializers.PrimaryKeyRelatedField(
        source="initial_machine", queryset=Machine.objects.all(), write_only=True, required=False
    )
    initial_status = serializers.ChoiceField(
        choices=MoldAsset.Status.choices,
        write_only=True,
        required=False,
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
            "model_code",
            "product_name",
            "image",
            "status",
            "status_label",
            "slot",
            "slot_id",
            "machine",
            "machine_id",
            "initial_status",
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
        if obj.status == MoldAsset.Status.OUTSOURCED:
            return "客户收回"
        return ""

    def validate(self, attrs):
        model = attrs.get("mold_model")
        model_code = attrs.get("model_code", "").strip()
        product_name = attrs.get("product_name", "").strip()
        asset_code = attrs.get("asset_code", "").strip()
        if model_code:
            attrs["model_code"] = model_code
        if "product_name" in attrs:
            attrs["product_name"] = product_name
        if "asset_code" in attrs:
            attrs["asset_code"] = asset_code
        if self.instance and "asset_code" in attrs and not asset_code:
            raise serializers.ValidationError({"asset_code": "模具编号不能为空。"})
        if self.instance and "asset_code" in attrs and asset_code != self.instance.asset_code:
            raise serializers.ValidationError({"asset_code": "模具编号创建后不可直接修改。"})
        if asset_code and MoldAsset.objects.filter(asset_code=asset_code).exclude(
            pk=self.instance.pk if self.instance else None
        ).exists():
            raise serializers.ValidationError({"asset_code": "该模具编号已存在。"})
        if not self.instance and not model and not model_code:
            raise serializers.ValidationError({"model_code": "请输入模具型号。"})
        if model and model_code and model.code.casefold() != model_code.casefold():
            raise serializers.ValidationError({"model_code": "模具型号与所选型号不一致。"})
        if self.instance and any(key in attrs for key in ("initial_slot", "initial_machine", "initial_status")):
            raise serializers.ValidationError({"status": "修改状态或位置请使用对应的状态操作。"})
        if not self.instance:
            initial_status = attrs.get("initial_status", MoldAsset.Status.IN_STOCK)
            if initial_status == MoldAsset.Status.IN_STOCK:
                if "initial_slot" not in attrs:
                    raise serializers.ValidationError({"slot_id": "在库模具必须选择初始库位。"})
                if "initial_machine" in attrs:
                    raise serializers.ValidationError({"machine_id": "在库模具不能选择机台。"})
            elif initial_status == MoldAsset.Status.ON_MACHINE:
                if "initial_machine" not in attrs:
                    raise serializers.ValidationError({"machine_id": "上机模具必须选择机台。"})
                if "initial_slot" in attrs:
                    raise serializers.ValidationError({"slot_id": "上机模具不能选择库位。"})
            elif "initial_slot" in attrs or "initial_machine" in attrs:
                raise serializers.ValidationError({"initial_status": "客户收回状态不能选择库位或机台。"})
        return attrs

    @staticmethod
    def _resolve_model(mold_model, model_code, product_name):
        if mold_model:
            if product_name and mold_model.product_name != product_name:
                mold_model.product_name = product_name
                mold_model.save(update_fields=["product_name", "updated_at"])
            return mold_model
        model = MoldModel.objects.filter(code__iexact=model_code).order_by("id").first()
        if model:
            if product_name and model.product_name != product_name:
                model.product_name = product_name
                model.save(update_fields=["product_name", "updated_at"])
            return model
        try:
            with transaction.atomic():
                return MoldModel.objects.create(
                    code=model_code,
                    product_name=product_name or model_code,
                )
        except IntegrityError:
            model = MoldModel.objects.filter(code__iexact=model_code).order_by("id").first()
            if not model:
                raise
            if product_name and model.product_name != product_name:
                model.product_name = product_name
                model.save(update_fields=["product_name", "updated_at"])
            return model

    @staticmethod
    def _next_asset_code(model_code):
        prefix = re.sub(r"[^\w-]+", "-", model_code, flags=re.UNICODE).strip("-_").upper() or "MOLD"
        index = 1
        while True:
            suffix = f"-{index:02d}"
            candidate = f"{prefix[: 100 - len(suffix)]}{suffix}"
            if not MoldAsset.objects.filter(asset_code=candidate).exists():
                return candidate
            index += 1

    @transaction.atomic
    def create(self, validated_data):
        confirm_warnings = validated_data.pop("confirm_warnings", False)
        initial_status = validated_data.pop("initial_status", MoldAsset.Status.IN_STOCK)
        slot = validated_data.pop("initial_slot", None)
        machine = validated_data.pop("initial_machine", None)
        model_code = validated_data.pop("model_code", "")
        product_name = validated_data.pop("product_name", "")
        mold_model = self._resolve_model(validated_data.pop("mold_model", None), model_code, product_name)
        asset_code = validated_data.pop("asset_code", "")
        generated_asset_code = not asset_code
        asset_code = asset_code or self._next_asset_code(mold_model.code)
        if slot:
            slot = RackSlot.objects.select_for_update().select_related("zone__level__rack").get(pk=slot.pk)
            try:
                validate_slot(slot)
            except DjangoValidationError as exc:
                raise serializers.ValidationError({"slot_id": exc.messages})
        if machine:
            machine = Machine.objects.select_for_update().get(pk=machine.pk)
            if not machine.is_active:
                raise serializers.ValidationError({"machine_id": "所选机台已停用。"})
        mold = MoldAsset(
            asset_code=asset_code,
            mold_model=mold_model,
            status=initial_status,
            current_slot=slot,
            current_machine=machine,
            status_changed_at=timezone.now(),
            **validated_data,
        )
        warnings = []
        if slot:
            try:
                warnings = stacking_warnings(mold, target_slot=slot)
            except DjangoValidationError as exc:
                raise serializers.ValidationError({"slot_id": exc.messages}) from exc
        if warnings and not confirm_warnings:
            raise ConfirmationRequired(warnings)
        while True:
            try:
                mold.full_clean()
                with transaction.atomic():
                    mold.save()
                break
            except DjangoValidationError as exc:
                errors = exc.message_dict if hasattr(exc, "message_dict") else {}
                if generated_asset_code and errors and set(errors) == {"asset_code"}:
                    mold.asset_code = self._next_asset_code(mold_model.code)
                    continue
                raise serializers.ValidationError(errors or exc.messages)
            except IntegrityError as exc:
                if slot and MoldAsset.objects.filter(current_slot=slot).exists():
                    raise serializers.ValidationError({"slot_id": "目标库位已被占用。"}) from exc
                if MoldAsset.objects.filter(asset_code=mold.asset_code).exists():
                    if generated_asset_code:
                        mold.asset_code = self._next_asset_code(mold_model.code)
                        continue
                    raise serializers.ValidationError({"asset_code": "该模具编号已存在。"}) from exc
                raise serializers.ValidationError({"non_field_errors": "模具保存失败，请重试。"}) from exc
        if slot:
            Rack.objects.filter(pk=slot.zone.level.rack_id).update(structure_locked=True)
        MoldMovement.objects.create(
            mold=mold,
            action=MoldMovement.Action.CREATE,
            to_status=mold.status,
            to_slot=slot,
            to_machine=machine,
            note="新建模具",
            operator=self.context["request"].user,
        )
        return mold

    def update(self, instance, validated_data):
        validated_data.pop("confirm_warnings", None)
        validated_data.pop("initial_status", None)
        validated_data.pop("initial_slot", None)
        validated_data.pop("initial_machine", None)
        model_code = validated_data.pop("model_code", "")
        product_name = validated_data.pop("product_name", "")
        if model_code:
            validated_data["mold_model"] = self._resolve_model(
                validated_data.get("mold_model"), model_code, product_name
            )
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
