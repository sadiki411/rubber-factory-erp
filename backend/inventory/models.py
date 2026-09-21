from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone

from molds.models import TimeStampedModel


class InventoryLocation(TimeStampedModel):
    class RackType(models.TextChoices):
        LARGE = "LARGE", "大货架"
        SMALL = "SMALL", "小货架"
        FRIDGE = "FRIDGE", "冰箱"

    code = models.CharField("库位编码", max_length=40, unique=True)
    rack_code = models.CharField("货架编号", max_length=20, db_index=True)
    rack_type = models.CharField("货架类型", max_length=12, choices=RackType.choices)
    level_no = models.PositiveSmallIntegerField("层号", null=True, blank=True)
    position_no = models.PositiveSmallIntegerField("位置号", null=True, blank=True)
    label = models.CharField("库位名称", max_length=100, blank=True, default="")
    is_active = models.BooleanField("启用", default=True, db_index=True)
    allows_basket = models.BooleanField("允许放筐", default=False)
    allows_bag = models.BooleanField("允许放袋", default=True)

    class Meta:
        ordering = ["rack_code", "level_no", "position_no", "code"]
        constraints = [
            models.UniqueConstraint(
                fields=["rack_code", "level_no", "position_no"],
                condition=Q(level_no__isnull=False, position_no__isnull=False),
                name="inventory_location_rack_level_position_uniq",
            ),
        ]

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.rack_type == self.RackType.FRIDGE:
            if self.level_no is not None or self.position_no is not None:
                raise ValidationError("冰箱逻辑位不能设置层号或位置号。")
            if self.allows_basket or self.allows_bag:
                raise ValidationError("冰箱逻辑位不能作为成品袋位或筐位。")
            return
        if self.rack_type == self.RackType.LARGE:
            if self.level_no not in range(1, 5) or self.position_no not in range(1, 6):
                raise ValidationError("大货架库位必须是 1-4 层、1-5 位。")
            if not self.allows_basket or not self.allows_bag:
                raise ValidationError("大货架库位必须同时允许筐和袋。")
        if self.rack_type == self.RackType.SMALL:
            if self.level_no not in range(1, 6) or self.position_no not in range(1, 3):
                raise ValidationError("小货架库位必须是 1-5 层、1-2 位。")
            if self.allows_basket or not self.allows_bag:
                raise ValidationError("小货架固定为袋位，不允许放筐。")

    @property
    def current_container(self):
        return getattr(self, "inventory_container", None)

    def __str__(self):
        return self.code


class InventoryProduct(TimeStampedModel):
    """A product identity independent from orders and process cards."""

    product_code = models.CharField("产品编号", max_length=120, blank=True, default="", db_index=True)
    product_name = models.CharField("产品名称", max_length=200, blank=True, default="")
    specification = models.CharField("规格", max_length=200, blank=True, default="", db_index=True)
    material = models.CharField("材质", max_length=100, blank=True, default="", db_index=True)
    unit_weight_g = models.DecimalField(
        "成品单重(g)", max_digits=14, decimal_places=5, null=True, blank=True,
        validators=[MinValueValidator(Decimal("0.00001"))],
    )
    product_specification = models.ForeignKey(
        "orders.ProductSpecification", on_delete=models.PROTECT,
        related_name="inventory_products", null=True, blank=True,
    )
    is_active = models.BooleanField("启用", default=True, db_index=True)
    notes = models.TextField("备注", blank=True, default="")

    class Meta:
        ordering = ["product_code", "specification", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["product_code", "specification", "material"],
                condition=Q(product_code__gt="") | Q(specification__gt=""),
                name="inventory_product_identity_uniq",
            )
        ]

    def clean(self):
        self.product_code = str(self.product_code or "").strip()
        self.product_name = str(self.product_name or "").strip()
        self.specification = str(self.specification or "").strip()
        self.material = str(self.material or "").strip()
        if not any((self.product_code, self.product_name, self.specification)):
            from django.core.exceptions import ValidationError

            raise ValidationError("产品编号、产品名称、规格至少填写一项。")

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.product_code or self.specification or self.product_name or f"产品 {self.pk}"


class InventoryBatch(TimeStampedModel):
    class QualityStatus(models.TextChoices):
        WAITING = "WAITING", "待检"
        PASSED = "PASSED", "已检合格"
        FAILED = "FAILED", "不合格"
        HOLD = "HOLD", "冻结"

    class SourceType(models.TextChoices):
        OPENING = "OPENING", "期初盘点"
        PRODUCTION_SURPLUS = "PRODUCTION_SURPLUS", "生产余量"
        MANUAL = "MANUAL", "手工入库"
        OTHER = "OTHER", "其他"

    batch_no = models.CharField("库存批次号", max_length=80, unique=True)
    product = models.ForeignKey(InventoryProduct, related_name="batches", on_delete=models.PROTECT)
    received_on = models.DateField("入库日期", default=timezone.localdate, db_index=True)
    source_type = models.CharField("入库来源", max_length=30, choices=SourceType.choices, default=SourceType.MANUAL)
    source_note = models.CharField("来源说明", max_length=500, blank=True, default="")
    quality_status = models.CharField("质量状态", max_length=12, choices=QualityStatus.choices, default=QualityStatus.WAITING, db_index=True)
    inspector = models.ForeignKey(
        "quality.QualityEmployee", related_name="inventory_batches_inspected",
        on_delete=models.PROTECT, null=True, blank=True,
    )
    inspected_on = models.DateField("检验日期", null=True, blank=True)
    notes = models.TextField("备注", blank=True, default="")

    class Meta:
        ordering = ["-received_on", "-id"]

    def __str__(self):
        return self.batch_no


class InventoryContainer(TimeStampedModel):
    class ContainerType(models.TextChoices):
        BAG = "BAG", "袋"
        BASKET = "BASKET", "筐"

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "在用"
        EMPTY = "EMPTY", "已空"
        CLOSED = "CLOSED", "已关闭"

    container_code = models.CharField("容器编号", max_length=80, unique=True)
    batch = models.ForeignKey(InventoryBatch, related_name="containers", on_delete=models.PROTECT)
    container_type = models.CharField("容器类型", max_length=10, choices=ContainerType.choices)
    location = models.OneToOneField(
        InventoryLocation, related_name="inventory_container", on_delete=models.PROTECT,
        null=True, blank=True,
    )
    bag_count = models.PositiveIntegerField("袋数", default=1)
    pieces_per_bag = models.PositiveIntegerField("每袋数量", null=True, blank=True)
    quantity = models.PositiveIntegerField("库存数量", validators=[MinValueValidator(0)])
    status = models.CharField("容器状态", max_length=10, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    notes = models.TextField("备注", blank=True, default="")

    class Meta:
        ordering = ["batch_id", "container_code"]

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.container_type == self.ContainerType.BASKET and self.location_id:
            if not self.location.allows_basket:
                raise ValidationError({"location": "该库位不允许放筐。"})
        if self.container_type == self.ContainerType.BAG and self.location_id:
            if not self.location.allows_bag:
                raise ValidationError({"location": "该库位不允许放袋。"})
        if self.bag_count < 1:
            raise ValidationError({"bag_count": "袋数必须大于0。"})
        if self.quantity < 0:
            raise ValidationError({"quantity": "库存数量不能小于0。"})
        if self.status == self.Status.ACTIVE and self.quantity < 1:
            raise ValidationError({"quantity": "在用容器数量必须大于0。"})
        if self.status == self.Status.EMPTY and self.quantity != 0:
            raise ValidationError({"quantity": "已空容器数量必须为0。"})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.container_code


class InventoryTransaction(TimeStampedModel):
    class TransactionType(models.TextChoices):
        RECEIPT = "RECEIPT", "入库"
        OUTBOUND = "OUTBOUND", "出库"
        MOVE = "MOVE", "移库"
        ADJUST = "ADJUST", "盘点调整"
        QUALITY = "QUALITY", "质量状态变更"

    transaction_type = models.CharField("流水类型", max_length=12, choices=TransactionType.choices)
    batch = models.ForeignKey(InventoryBatch, related_name="transactions", on_delete=models.PROTECT)
    container = models.ForeignKey(InventoryContainer, related_name="transactions", on_delete=models.PROTECT)
    quantity = models.IntegerField("变动数量")
    from_location = models.ForeignKey(InventoryLocation, related_name="outgoing_inventory_transactions", on_delete=models.PROTECT, null=True, blank=True)
    to_location = models.ForeignKey(InventoryLocation, related_name="incoming_inventory_transactions", on_delete=models.PROTECT, null=True, blank=True)
    outbound = models.ForeignKey("InventoryOutbound", related_name="transactions", on_delete=models.PROTECT, null=True, blank=True)
    reason = models.CharField("原因", max_length=500, blank=True, default="")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="inventory_transactions", on_delete=models.PROTECT)

    class Meta:
        ordering = ["-created_at", "-id"]


class InventoryOutbound(TimeStampedModel):
    class Status(models.TextChoices):
        CONFIRMED = "CONFIRMED", "已确认"
        VOID = "VOID", "已作废"

    outbound_no = models.CharField("出库单号", max_length=80, unique=True)
    order = models.ForeignKey("quality.QualityOrder", related_name="inventory_outbounds", on_delete=models.PROTECT, null=True, blank=True)
    shipment_ref = models.CharField("装车/出货参考号", max_length=100, blank=True, default="")
    status = models.CharField("状态", max_length=12, choices=Status.choices, default=Status.CONFIRMED, db_index=True)
    note = models.TextField("备注", blank=True, default="")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="inventory_outbounds", on_delete=models.PROTECT)

    def __str__(self):
        return self.outbound_no


class InventoryOutboundLine(TimeStampedModel):
    outbound = models.ForeignKey(InventoryOutbound, related_name="lines", on_delete=models.PROTECT)
    container = models.ForeignKey(InventoryContainer, related_name="outbound_lines", on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField("出库数量", validators=[MinValueValidator(1)])


class MaterialRemainder(TimeStampedModel):
    material = models.CharField("胶料材质", max_length=120)
    weight_kg = models.DecimalField("剩余重量(kg)", max_digits=14, decimal_places=3, validators=[MinValueValidator(Decimal("0.001"))])
    stored_on = models.DateTimeField("入冰箱时间", default=timezone.now, db_index=True)
    fridge_code = models.CharField("存放位置", max_length=50, default="F01-冰箱")
    note = models.TextField("备注", blank=True, default="")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="material_remainders", on_delete=models.PROTECT)

    class Meta:
        ordering = ["-stored_on", "-id"]


class MaterialRemainderUse(TimeStampedModel):
    remainder = models.ForeignKey(MaterialRemainder, related_name="uses", on_delete=models.PROTECT)
    weight_kg = models.DecimalField("使用重量(kg)", max_digits=14, decimal_places=3, validators=[MinValueValidator(Decimal("0.001"))])
    note = models.CharField("使用说明", max_length=500, blank=True, default="")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="material_remainder_uses", on_delete=models.PROTECT)
