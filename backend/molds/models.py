import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower
from django.utils import timezone


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class MoldModel(TimeStampedModel):
    code = models.CharField("型号", max_length=100, unique=True)
    product_name = models.CharField("产品名称", max_length=200)
    description = models.TextField("说明", blank=True)
    is_active = models.BooleanField("启用", default=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} - {self.product_name}"


class Machine(TimeStampedModel):
    code = models.CharField("机台编号", max_length=50, unique=True)
    name = models.CharField("机台名称", max_length=100)
    is_active = models.BooleanField("启用", default=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} - {self.name}"


class Processor(TimeStampedModel):
    code = models.CharField("加工方编号", max_length=50, unique=True)
    name = models.CharField("加工方名称", max_length=150)
    contact = models.CharField("联系人", max_length=100, blank=True)
    phone = models.CharField("电话", max_length=50, blank=True)
    is_active = models.BooleanField("启用", default=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} - {self.name}"


class Rack(TimeStampedModel):
    code = models.CharField("货架编号", max_length=20, unique=True)
    name = models.CharField("货架名称", max_length=100)
    layout_version = models.PositiveSmallIntegerField("布局版本", default=1)
    is_configured = models.BooleanField("已配置", default=False)
    structure_locked = models.BooleanField("结构已锁定", default=False)
    is_active = models.BooleanField("启用", default=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return self.code


class RackLevel(models.Model):
    rack = models.ForeignKey(Rack, related_name="levels", on_delete=models.CASCADE)
    level_no = models.PositiveSmallIntegerField("层号")

    class Meta:
        ordering = ["rack__code", "-level_no"]
        constraints = [
            models.UniqueConstraint(fields=["rack", "level_no"], name="uniq_rack_level"),
        ]

    def __str__(self):
        return f"{self.rack.code}-L{self.level_no:02d}"


class RackZone(models.Model):
    level = models.ForeignKey(RackLevel, related_name="zones", on_delete=models.CASCADE)
    code = models.CharField("区域编码", max_length=10)
    label = models.CharField("区域名称", max_length=50)
    allowed_capacities = models.JSONField("允许容量", default=list)
    capacity_mode = models.PositiveSmallIntegerField("当前容量")
    supports_stacking = models.BooleanField("支持叠放", default=False)
    stacking_enabled = models.BooleanField("启用叠放", default=False)
    is_active = models.BooleanField("启用", default=True)

    class Meta:
        ordering = ["level__rack__code", "-level__level_no", "code"]
        constraints = [
            models.UniqueConstraint(fields=["level", "code"], name="uniq_level_zone"),
        ]

    def clean(self):
        capacities = sorted({int(value) for value in self.allowed_capacities if int(value) > 0})
        if not capacities:
            raise ValidationError({"allowed_capacities": "至少需要一个有效容量。"})
        if self.capacity_mode not in capacities:
            raise ValidationError({"capacity_mode": "当前容量必须属于允许容量。"})
        if self.stacking_enabled and not self.supports_stacking:
            raise ValidationError({"stacking_enabled": "该区域物理结构不支持叠放。"})
        self.allowed_capacities = capacities

    def __str__(self):
        return f"{self.level}-{self.code}"


class RackSlot(models.Model):
    zone = models.ForeignKey(RackZone, related_name="slots", on_delete=models.CASCADE)
    capacity_mode = models.PositiveSmallIntegerField("容量模式")
    position_no = models.PositiveSmallIntegerField("位置号")
    stack_level = models.PositiveSmallIntegerField("叠放层", default=1)
    display_code = models.CharField("显示库位编码", max_length=80, db_index=True)
    technical_code = models.CharField("内部库位编码", max_length=100, unique=True)
    is_blocked = models.BooleanField("禁放", default=False)
    blocking_reason = models.CharField("禁放原因", max_length=200, blank=True)

    class Meta:
        ordering = [
            "zone__level__rack__code",
            "-zone__level__level_no",
            "zone__code",
            "position_no",
            "stack_level",
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["zone", "capacity_mode", "position_no", "stack_level"],
                name="uniq_zone_capacity_position_stack",
            ),
            models.CheckConstraint(condition=Q(stack_level__in=[1, 2]), name="slot_stack_level_1_or_2"),
        ]

    @property
    def is_enabled(self):
        rack = self.zone.level.rack
        return (
            rack.is_active
            and rack.is_configured
            and self.zone.is_active
            and self.capacity_mode == self.zone.capacity_mode
            and (self.stack_level == 1 or self.zone.stacking_enabled)
            and not self.is_blocked
        )

    def __str__(self):
        return self.display_code


def mold_image_path(instance, filename):
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
    return f"molds/{instance.asset_code}/{uuid.uuid4().hex}.{extension}"


class MoldAsset(TimeStampedModel):
    class Status(models.TextChoices):
        IN_STOCK = "IN_STOCK", "在库"
        ON_MACHINE = "ON_MACHINE", "上机"
        OUTSOURCED = "OUTSOURCED", "客户收回"

    asset_code = models.CharField("模具编号", max_length=100)
    mold_model = models.ForeignKey(MoldModel, related_name="assets", on_delete=models.PROTECT)
    main_image = models.ImageField("主图", upload_to=mold_image_path, blank=True)
    status = models.CharField("当前状态", max_length=20, choices=Status.choices)
    current_slot = models.OneToOneField(
        RackSlot,
        related_name="occupant",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    current_machine = models.ForeignKey(
        Machine,
        related_name="current_molds",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    current_processor = models.ForeignKey(
        Processor,
        related_name="current_molds",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    allows_stacking = models.BooleanField("允许在其上叠放", default=False)
    notes = models.TextField("备注", blank=True)
    is_active = models.BooleanField("启用", default=True)
    status_changed_at = models.DateTimeField("状态更新时间", default=timezone.now)

    class Meta:
        ordering = ["asset_code"]
        constraints = [
            models.UniqueConstraint(
                Lower("asset_code"),
                condition=Q(is_active=True),
                name="uniq_active_mold_asset_code_ci",
            ),
            models.CheckConstraint(
                condition=(
                    (
                        Q(is_active=False)
                        & Q(current_slot__isnull=True)
                        & Q(current_machine__isnull=True)
                        & Q(current_processor__isnull=True)
                    )
                    | (
                        Q(is_active=True)
                        & (
                            (Q(status="IN_STOCK") & Q(current_slot__isnull=False) & Q(current_machine__isnull=True) & Q(current_processor__isnull=True))
                            | (Q(status="ON_MACHINE") & Q(current_slot__isnull=True) & Q(current_machine__isnull=False) & Q(current_processor__isnull=True))
                            | (Q(status="OUTSOURCED") & Q(current_slot__isnull=True) & Q(current_machine__isnull=True) & Q(current_processor__isnull=True))
                        )
                    )
                ),
                name="mold_status_location_consistent",
            )
        ]

    def clean(self):
        errors = {}
        if not self.is_active:
            if self.current_slot_id or self.current_machine_id or self.current_processor_id:
                errors["is_active"] = "已删除模具不能保留当前库位、机台或加工方。"
            if errors:
                raise ValidationError(errors)
            return
        if self.status == self.Status.IN_STOCK:
            if not self.current_slot_id:
                errors["current_slot"] = "在库模具必须选择库位。"
            elif not self.current_slot.is_enabled:
                errors["current_slot"] = "所选库位未启用或已禁放。"
            if self.current_machine_id or self.current_processor_id:
                errors["status"] = "在库状态不能同时设置机台或加工方。"
        elif self.status == self.Status.ON_MACHINE:
            if not self.current_machine_id:
                errors["current_machine"] = "上机模具必须选择机台。"
            if self.current_slot_id or self.current_processor_id:
                errors["status"] = "上机状态不能同时设置库位或加工方。"
        elif self.status == self.Status.OUTSOURCED:
            if self.current_slot_id or self.current_machine_id or self.current_processor_id:
                errors["status"] = "客户收回状态不能设置库位、机台或加工方。"
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return self.asset_code


class MoldMovement(models.Model):
    class Action(models.TextChoices):
        CREATE = "CREATE", "新建"
        PUTAWAY = "PUTAWAY", "归位"
        MOVE = "MOVE", "移库"
        LOAD_MACHINE = "LOAD_MACHINE", "上机"
        SEND_OUT = "SEND_OUT", "客户收回"
        EDIT = "EDIT", "编辑"
        DELETE = "DELETE", "删除"

    mold = models.ForeignKey(MoldAsset, related_name="movements", on_delete=models.PROTECT)
    action = models.CharField("操作", max_length=20, choices=Action.choices)
    from_status = models.CharField("原状态", max_length=20, blank=True)
    to_status = models.CharField("新状态", max_length=20, choices=MoldAsset.Status.choices)
    from_slot = models.ForeignKey(RackSlot, related_name="movements_from", on_delete=models.PROTECT, null=True, blank=True)
    to_slot = models.ForeignKey(RackSlot, related_name="movements_to", on_delete=models.PROTECT, null=True, blank=True)
    from_machine = models.ForeignKey(Machine, related_name="movements_from", on_delete=models.PROTECT, null=True, blank=True)
    to_machine = models.ForeignKey(Machine, related_name="movements_to", on_delete=models.PROTECT, null=True, blank=True)
    from_processor = models.ForeignKey(Processor, related_name="movements_from", on_delete=models.PROTECT, null=True, blank=True)
    to_processor = models.ForeignKey(Processor, related_name="movements_to", on_delete=models.PROTECT, null=True, blank=True)
    note = models.TextField("备注", blank=True)
    operator = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="mold_movements", on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("模具操作历史不可修改。")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("模具操作历史不可删除。")


class ImportBatch(models.Model):
    class Kind(models.TextChoices):
        STANDARD = "STANDARD", "标准模板"
        LEGACY = "LEGACY", "旧台账"

    class Status(models.TextChoices):
        PREVIEWED = "PREVIEWED", "已预检"
        COMMITTED = "COMMITTED", "已导入"
        FAILED = "FAILED", "失败"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=20, choices=Kind.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PREVIEWED)
    original_name = models.CharField(max_length=255)
    payload = models.JSONField(default=dict)
    errors = models.JSONField(default=list)
    warnings = models.JSONField(default=list)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="import_batches", on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    committed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
