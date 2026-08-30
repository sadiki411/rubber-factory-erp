import uuid
from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import F, Q, Sum
from django.utils import timezone

from molds.models import TimeStampedModel


class QualityEmployee(TimeStampedModel):
    class Role(models.TextChoices):
        INSPECTOR = "INSPECTOR", "品检员"
        REWORKER = "REWORKER", "返工员"
        BOTH = "BOTH", "品检兼返工"

    employee_no = models.CharField("员工编号", max_length=50, unique=True)
    name = models.CharField("姓名", max_length=100)
    team = models.CharField("班组", max_length=100, blank=True)
    role = models.CharField("岗位", max_length=20, choices=Role.choices)
    is_active = models.BooleanField("在职/启用", default=True)
    notes = models.TextField("备注", blank=True)

    class Meta:
        ordering = ["employee_no"]

    def clean(self):
        self.employee_no = str(self.employee_no or "").strip().upper()
        self.name = str(self.name or "").strip()
        self.team = str(self.team or "").strip()
        if not self.employee_no:
            raise ValidationError({"employee_no": "员工编号不能为空。"})
        if not self.name:
            raise ValidationError({"name": "员工姓名不能为空。"})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.employee_no} - {self.name}"


class QualityOrder(TimeStampedModel):
    class Status(models.TextChoices):
        OPEN = "OPEN", "进行中"
        COMPLETED = "COMPLETED", "已完成"
        CANCELLED = "CANCELLED", "已取消"

    order_no = models.CharField("订单号", max_length=100, db_index=True)
    item_no = models.CharField("项次", max_length=100, blank=True, default="", db_index=True)
    # Empty string keeps imported and manually entered rows consistent when no
    # customer batch number is supplied.
    batch_no = models.CharField("批次号", max_length=100, blank=True, default="")
    product_code = models.CharField("产品编号", max_length=100, blank=True)
    product_name = models.CharField("产品名称", max_length=200, blank=True, default="")
    specification = models.CharField("规格", max_length=200)
    material = models.CharField("材质/胶料", max_length=100, blank=True, default="")
    product_specification = models.ForeignKey(
        "orders.ProductSpecification",
        verbose_name="产品规格资料",
        related_name="orders",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    order_quantity = models.PositiveIntegerField(
        "订单数量", validators=[MinValueValidator(1)]
    )
    order_date = models.DateField("下单日期", null=True, blank=True, db_index=True)
    due_date = models.DateField("交期", null=True, blank=True, db_index=True)
    mold_size = models.CharField("模具尺寸", max_length=100, blank=True)
    forming_hours = models.DecimalField(
        "成型工时",
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
    )
    production_required = models.BooleanField("是否生产", null=True, blank=True)
    legacy_shipment_text = models.TextField("原出货信息", blank=True, default="")
    required_material_kg = models.DecimalField(
        "所需胶料(kg)",
        max_digits=14,
        decimal_places=3,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
    )
    manual_received_material_kg = models.DecimalField(
        "手工已发胶料(kg)",
        max_digits=14,
        decimal_places=3,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
    )
    process_card_count = models.PositiveIntegerField("流程卡张数", null=True, blank=True)
    process_card_covered_quantity = models.PositiveIntegerField(
        "流程卡覆盖订单数量", null=True, blank=True
    )
    process_card_text = models.CharField(
        "流程卡原始记录", max_length=200, blank=True, default=""
    )
    production_quantity = models.CharField(
        "生产数量原始记录", max_length=200, blank=True, default=""
    )
    shipment_date = models.CharField(
        "出货日期原始记录", max_length=200, blank=True, default=""
    )
    shipped_quantity = models.CharField(
        "出货数量原始记录", max_length=200, blank=True, default=""
    )
    status = models.CharField(
        "状态", max_length=20, choices=Status.choices, default=Status.OPEN, db_index=True
    )
    notes = models.TextField("备注", blank=True)
    source_batch = models.ForeignKey(
        "orders.BusinessImportBatch",
        related_name="orders",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    last_source_batch = models.ForeignKey(
        "orders.BusinessImportBatch",
        related_name="latest_orders",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    source_sheet = models.CharField(max_length=100, blank=True, default="")
    source_row = models.PositiveIntegerField(null=True, blank=True)
    source_key = models.CharField(max_length=255, blank=True, default="", db_index=True)
    source_system = models.CharField(max_length=100, blank=True, default="", db_index=True)
    external_key = models.CharField(max_length=500, blank=True, default="", db_index=True)
    source_document_at = models.DateTimeField(null=True, blank=True)
    last_imported_at = models.DateTimeField(null=True, blank=True)
    raw_data = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="创建人",
        related_name="quality_orders",
        on_delete=models.PROTECT,
    )

    class Meta:
        ordering = ["-order_date", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(order_quantity__gt=0),
                name="quality_order_quantity_gt_zero_ck",
            ),
            models.CheckConstraint(
                condition=Q(forming_hours__isnull=True) | Q(forming_hours__gte=0),
                name="quality_order_forming_hours_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(required_material_kg__isnull=True)
                | Q(required_material_kg__gte=0),
                name="quality_order_required_material_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(manual_received_material_kg__isnull=True)
                | Q(manual_received_material_kg__gte=0),
                name="quality_order_received_material_nonnegative",
            ),
            models.UniqueConstraint(
                fields=["source_key"],
                condition=~Q(source_key=""),
                name="uniq_quality_order_source_key",
            ),
            models.UniqueConstraint(
                fields=["external_key"],
                condition=~Q(external_key=""),
                name="uniq_quality_order_external_key",
            ),
        ]

    def clean(self):
        errors = {}
        for field_name in (
            "order_no",
            "item_no",
            "batch_no",
            "product_code",
            "product_name",
            "specification",
            "material",
            "mold_size",
            "legacy_shipment_text",
            "process_card_text",
            "production_quantity",
            "shipment_date",
            "shipped_quantity",
            "source_sheet",
            "source_key",
            "source_system",
            "external_key",
        ):
            setattr(self, field_name, str(getattr(self, field_name, "") or "").strip())
        if not self.order_no:
            errors["order_no"] = "订单号不能为空。"
        if not self.specification:
            errors["specification"] = "规格不能为空。"
        if not self.order_quantity or self.order_quantity < 1:
            errors["order_quantity"] = "订单数量必须大于0。"
        if (
            not self.source_batch_id
            and self.order_date
            and self.due_date
            and self.due_date < self.order_date
        ):
            errors["due_date"] = "交期不能早于下单日期。"
        for field_name in ("forming_hours", "required_material_kg", "manual_received_material_kg"):
            value = getattr(self, field_name)
            if value is not None and value < 0:
                errors[field_name] = "数值不能小于0。"
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        suffix = f"/{self.batch_no}" if self.batch_no else ""
        return f"{self.order_no}{suffix} - {self.product_name}"


class QualityShipment(TimeStampedModel):
    shipment_no = models.CharField("出货单号", max_length=100, unique=True)
    shipment_date = models.DateField("出货日期", db_index=True)
    order = models.ForeignKey(
        QualityOrder,
        verbose_name="订单/批次",
        related_name="shipments",
        on_delete=models.PROTECT,
    )
    inspector = models.ForeignKey(
        QualityEmployee,
        verbose_name="品检员",
        related_name="inspected_shipments",
        on_delete=models.PROTECT,
    )
    inspectors = models.ManyToManyField(
        QualityEmployee,
        verbose_name="品检员（多人）",
        related_name="legacy_inspected_shipments_many",
        blank=True,
    )
    inspection_quantity = models.PositiveIntegerField("质检数量")
    qualified_quantity = models.PositiveIntegerField("合格数量", default=0)
    defective_quantity = models.PositiveIntegerField("不良数量", default=0)
    shipped_quantity = models.PositiveIntegerField("出货数量")
    notes = models.TextField("备注", blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="创建人",
        related_name="quality_shipments",
        on_delete=models.PROTECT,
    )

    class Meta:
        ordering = ["-shipment_date", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(inspection_quantity__gt=0),
                name="quality_shipment_inspection_gt_zero_ck",
            ),
            models.CheckConstraint(
                condition=Q(shipped_quantity__gt=0),
                name="quality_shipment_shipped_gt_zero_ck",
            ),
            models.CheckConstraint(
                condition=Q(
                    inspection_quantity=F("qualified_quantity")
                    + F("defective_quantity")
                ),
                name="quality_shipment_inspection_balance_ck",
            ),
            models.CheckConstraint(
                condition=Q(shipped_quantity__lte=F("qualified_quantity")),
                name="quality_shipment_shipped_lte_qualified_ck",
            ),
        ]

    def clean(self):
        errors = {}
        self.shipment_no = str(self.shipment_no or "").strip().upper()
        if not self.shipment_no:
            errors["shipment_no"] = "出货单号不能为空。"
        if not self.inspection_quantity or self.inspection_quantity < 1:
            errors["inspection_quantity"] = "质检数量必须大于0。"
        if not self.shipped_quantity or self.shipped_quantity < 1:
            errors["shipped_quantity"] = "出货数量必须大于0。"
        if self.inspection_quantity != (
            int(self.qualified_quantity or 0) + int(self.defective_quantity or 0)
        ):
            errors["qualified_quantity"] = "合格数量与不良数量之和必须等于质检数量。"
        if int(self.shipped_quantity or 0) > int(self.qualified_quantity or 0):
            errors["shipped_quantity"] = "出货数量不能超过合格数量。"
        if self.pk and self.shipped_quantity:
            returned_total = self.reworks.aggregate(total=Sum("returned_quantity"))[
                "total"
            ] or 0
            if returned_total > self.shipped_quantity:
                errors["shipped_quantity"] = (
                    f"出货数量不能小于该记录累计退货数量{returned_total}。"
                )
        if self.inspector_id:
            role = QualityEmployee.objects.filter(pk=self.inspector_id).values_list(
                "role", flat=True
            ).first()
            if role not in (QualityEmployee.Role.INSPECTOR, QualityEmployee.Role.BOTH):
                errors["inspector"] = "所选员工不具备品检岗位。"
        if errors:
            raise ValidationError(errors)

    @property
    def rework_count(self):
        annotated = getattr(self, "rework_count_value", None)
        if annotated is not None:
            return annotated
        return self.reworks.count()

    @property
    def returned_quantity(self):
        annotated = getattr(self, "returned_quantity_value", None)
        if annotated is not None:
            return annotated or 0
        return self.reworks.aggregate(total=Sum("returned_quantity"))["total"] or 0

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.shipment_no} - {self.order.order_no}"


class ReturnRework(TimeStampedModel):
    class ReasonCategory(models.TextChoices):
        APPEARANCE = "APPEARANCE", "外观"
        STICKING = "STICKING", "粘皮"
        DIMENSION = "DIMENSION", "尺寸"
        MATERIAL = "MATERIAL", "材质"
        MIXED = "MIXED", "混料/混装"
        PACKAGING = "PACKAGING", "包装"
        OTHER = "OTHER", "其他"

    class Status(models.TextChoices):
        PENDING = "PENDING", "待处理"
        PROCESSING = "PROCESSING", "处理中"
        COMPLETED = "COMPLETED", "已完成"

    shipment = models.ForeignKey(
        QualityShipment,
        verbose_name="原出货记录",
        related_name="reworks",
        on_delete=models.PROTECT,
    )
    rework_date = models.DateField("退货/返工日期", db_index=True)
    reason_category = models.CharField(
        "原因分类", max_length=20, choices=ReasonCategory.choices, db_index=True
    )
    reason = models.TextField("具体原因", blank=True)
    responsible_inspector = models.ForeignKey(
        QualityEmployee,
        verbose_name="责任品检员",
        related_name="responsible_reworks",
        on_delete=models.PROTECT,
    )
    rework_employee = models.ForeignKey(
        QualityEmployee,
        verbose_name="返工处理员工",
        related_name="handled_reworks",
        on_delete=models.PROTECT,
    )
    returned_quantity = models.PositiveIntegerField("退货数量")
    reworked_quantity = models.PositiveIntegerField("返工数量", default=0)
    recovered_quantity = models.PositiveIntegerField("返工合格数量", default=0)
    scrap_quantity = models.PositiveIntegerField("报废数量", default=0)
    status = models.CharField(
        "状态", max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    work_hours = models.DecimalField(
        "返工工时",
        max_digits=10,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(Decimal("0"))],
    )
    notes = models.TextField("备注", blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="创建人",
        related_name="quality_return_reworks",
        on_delete=models.PROTECT,
    )

    class Meta:
        ordering = ["-rework_date", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(returned_quantity__gt=0),
                name="quality_rework_returned_gt_zero_ck",
            ),
            models.CheckConstraint(
                condition=Q(reworked_quantity__lte=F("returned_quantity")),
                name="quality_reworked_lte_returned_ck",
            ),
            models.CheckConstraint(
                condition=Q(
                    recovered_quantity__lte=F("reworked_quantity")
                    - F("scrap_quantity")
                ),
                name="quality_rework_result_lte_reworked_ck",
            ),
            models.CheckConstraint(
                condition=Q(work_hours__gte=0),
                name="quality_rework_hours_nonnegative_ck",
            ),
        ]

    def clean(self):
        errors = {}
        if self.shipment_id and not self.responsible_inspector_id:
            self.responsible_inspector_id = QualityShipment.objects.filter(
                pk=self.shipment_id
            ).values_list("inspector_id", flat=True).first()
        if not self.returned_quantity or self.returned_quantity < 1:
            errors["returned_quantity"] = "退货数量必须大于0。"
        if int(self.reworked_quantity or 0) > int(self.returned_quantity or 0):
            errors["reworked_quantity"] = "返工数量不能超过退货数量。"
        if int(self.recovered_quantity or 0) + int(self.scrap_quantity or 0) > int(
            self.reworked_quantity or 0
        ):
            errors["recovered_quantity"] = "返工合格数量与报废数量之和不能超过返工数量。"
        if self.work_hours is not None and self.work_hours < 0:
            errors["work_hours"] = "返工工时不能小于0。"

        if self.responsible_inspector_id:
            role = QualityEmployee.objects.filter(
                pk=self.responsible_inspector_id
            ).values_list("role", flat=True).first()
            if role not in (QualityEmployee.Role.INSPECTOR, QualityEmployee.Role.BOTH):
                errors["responsible_inspector"] = "责任员工必须具备品检岗位。"
        if self.rework_employee_id:
            role = QualityEmployee.objects.filter(pk=self.rework_employee_id).values_list(
                "role", flat=True
            ).first()
            if role not in (QualityEmployee.Role.REWORKER, QualityEmployee.Role.BOTH):
                errors["rework_employee"] = "返工员工必须具备返工岗位。"

        if self.shipment_id and self.returned_quantity:
            shipment_quantity = QualityShipment.objects.filter(
                pk=self.shipment_id
            ).values_list("shipped_quantity", flat=True).first()
            previous = ReturnRework.objects.filter(shipment_id=self.shipment_id)
            if self.pk:
                previous = previous.exclude(pk=self.pk)
            previous_total = previous.aggregate(total=Sum("returned_quantity"))["total"] or 0
            if shipment_quantity is not None and previous_total + self.returned_quantity > shipment_quantity:
                errors["returned_quantity"] = (
                    f"该出货单累计退货数量不能超过出货数量{shipment_quantity}。"
                )
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.shipment_id and not self.responsible_inspector_id:
            self.responsible_inspector_id = QualityShipment.objects.filter(
                pk=self.shipment_id
            ).values_list("inspector_id", flat=True).first()
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.shipment.shipment_no} - {self.rework_date}"


# ---------------------------------------------------------------------------
# Weighted shipment and multi-round rework records
# ---------------------------------------------------------------------------
#
# The four models below intentionally live beside the legacy quality models.
# The legacy QualityShipment/ReturnRework tables are already used by the
# production and analytics modules and their meaning is quantity based.  The
# new tables are additive: they provide a weight-based workflow without
# changing or rewriting historical rows.


class ProductUnitWeight(TimeStampedModel):
    """A measured finished-product unit weight configuration.

    A product specification may have different weights for different mold
    models.  Keeping the measurement as its own record lets operators retain
    history when a product or mold is changed.  ``unit_weight_g`` can be typed
    directly, or calculated from ``sample_total_weight_g / sample_count``.
    """

    product_specification = models.ForeignKey(
        "orders.ProductSpecification",
        verbose_name="product specification",
        related_name="quality_unit_weights",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    mold_model = models.ForeignKey(
        "molds.MoldModel",
        verbose_name="mold model",
        related_name="quality_unit_weights",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    sample_count = models.PositiveIntegerField(
        "sample count", null=True, blank=True
    )
    sample_total_weight_g = models.DecimalField(
        "sample total weight (g)",
        max_digits=14,
        decimal_places=5,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
    )
    unit_weight_g = models.DecimalField(
        "finished unit weight (g)",
        max_digits=14,
        decimal_places=5,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
    )
    measured_on = models.DateField(
        "measurement date", default=timezone.localdate, db_index=True
    )
    backfill_reason = models.TextField("historical-entry reason", blank=True, default="")
    is_active = models.BooleanField("active", default=True, db_index=True)
    notes = models.TextField("notes", blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="created by",
        related_name="quality_product_unit_weights",
        on_delete=models.PROTECT,
    )

    class Meta:
        ordering = ["-measured_on", "-id"]

    def clean(self):
        errors = {}
        if not self.product_specification_id and not self.mold_model_id:
            errors["product_specification"] = (
                "A product specification or mold model is required."
            )
        if self.sample_count is not None and self.sample_count < 1:
            errors["sample_count"] = "Sample count must be greater than zero."
        if self.sample_total_weight_g is not None and self.sample_total_weight_g <= 0:
            errors["sample_total_weight_g"] = "Sample total weight must be greater than zero."
        if self.unit_weight_g is not None and self.unit_weight_g <= 0:
            errors["unit_weight_g"] = "Unit weight must be greater than zero."

        # Prefer an explicit value, but make the common measured-samples path
        # convenient by calculating it when the operator leaves it blank.
        if (
            self.unit_weight_g is None
            and self.sample_count
            and self.sample_total_weight_g
            and self.sample_count > 0
        ):
            self.unit_weight_g = (
                self.sample_total_weight_g / Decimal(self.sample_count)
            ).quantize(Decimal("0.00001"), rounding=ROUND_HALF_UP)
        if self.unit_weight_g is None:
            errors["unit_weight_g"] = (
                "Enter unit weight, or enter sample count and sample total weight."
            )
        if self.measured_on and self.measured_on < timezone.localdate() and not str(self.backfill_reason or "").strip():
            errors["backfill_reason"] = "A reason is required when entering a historical measurement."
        self.backfill_reason = str(self.backfill_reason or "").strip()
        self.notes = str(self.notes or "").strip()
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        target = self.product_specification_id or self.mold_model_id or "generic"
        return f"{target} - {self.unit_weight_g} g"


class ProcessCard(TimeStampedModel):
    """One physical manufacturing process card and its immutable weight snapshot."""

    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        PARTIAL_SHIPPED = "PARTIAL_SHIPPED", "Partially shipped"
        SHIPPED = "SHIPPED", "Shipped"
        REPLACED = "REPLACED", "已补卡替换"
        CANCELLED = "CANCELLED", "Cancelled"

    card_no = models.CharField("process card number", max_length=150, unique=True)
    # A replacement card keeps the same tracking id.  Return rounds are
    # counted against this stable physical-batch identity instead of a
    # particular printed card number.
    tracking_id = models.UUIDField(default=uuid.uuid4, db_index=True, editable=False)
    replaces = models.OneToOneField(
        "self",
        verbose_name="replaces process card",
        related_name="replaced_by",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    order = models.ForeignKey(
        QualityOrder,
        verbose_name="order",
        related_name="quality_process_cards",
        on_delete=models.PROTECT,
    )
    source_item_no = models.CharField(
        "source item number", max_length=100, blank=True, default=""
    )
    source_order_no = models.CharField("source order number", max_length=100, blank=True, default="")
    product_specification = models.ForeignKey(
        "orders.ProductSpecification",
        verbose_name="product specification",
        related_name="quality_process_cards",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    product_name_snapshot = models.CharField(
        "product name snapshot", max_length=200, blank=True, default=""
    )
    product_code_snapshot = models.CharField("product code snapshot", max_length=100, blank=True, default="")
    formula_code_snapshot = models.CharField("formula code snapshot", max_length=100, blank=True, default="")
    specification_snapshot = models.CharField(
        "specification snapshot", max_length=200, blank=True, default=""
    )
    material_snapshot = models.CharField(
        "material snapshot", max_length=100, blank=True, default=""
    )
    customer_snapshot = models.CharField(
        "customer snapshot", max_length=150, blank=True, default=""
    )
    department_snapshot = models.CharField(
        "department snapshot", max_length=150, blank=True, default=""
    )
    special_requirements = models.TextField("special requirements", blank=True, default="")
    qr_text = models.CharField("QR text", max_length=500, blank=True, default="")
    original_image = models.ImageField("original process-card image", upload_to="quality/process_cards/%Y/%m/", null=True, blank=True)
    material_issue_weight_kg = models.DecimalField(
        "material issued (kg)", max_digits=14, decimal_places=3, null=True, blank=True,
        validators=[MinValueValidator(Decimal("0"))],
    )
    reprint_count = models.PositiveIntegerField("reprint count", default=0)
    demand_date = models.DateField("demand date", null=True, blank=True, db_index=True)
    quantity = models.PositiveIntegerField(
        "card quantity", validators=[MinValueValidator(1)]
    )
    unit_weight_config = models.ForeignKey(
        ProductUnitWeight,
        verbose_name="unit weight configuration",
        related_name="process_cards",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    # Snapshots are deliberately duplicated here.  Editing a master weight
    # record must never change historical shipment calculations.
    unit_weight_g = models.DecimalField(
        "unit weight snapshot (g)",
        max_digits=14,
        decimal_places=5,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
    )
    sample_count_snapshot = models.PositiveIntegerField(null=True, blank=True)
    sample_total_weight_g_snapshot = models.DecimalField(
        max_digits=14,
        decimal_places=5,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
    )
    measured_on_snapshot = models.DateField(null=True, blank=True)
    mold_model_code_snapshot = models.CharField(
        max_length=100, blank=True, default=""
    )
    status = models.CharField(
        "status", max_length=20, choices=Status.choices, default=Status.OPEN, db_index=True
    )
    received_on = models.DateField(
        "received date", default=timezone.localdate, db_index=True
    )
    backfill_reason = models.TextField("historical-entry reason", blank=True, default="")
    notes = models.TextField("notes", blank=True, default="")
    raw_data = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="created by",
        related_name="quality_process_cards",
        on_delete=models.PROTECT,
    )

    class Meta:
        ordering = ["-received_on", "-id"]
        indexes = [
            models.Index(fields=["order", "source_item_no"]),
            models.Index(fields=["status", "demand_date"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(quantity__gt=0), name="quality_process_card_quantity_gt_zero"
            ),
        ]

    def clean(self):
        errors = {}
        self.card_no = str(self.card_no or "").strip().upper()
        if not self.card_no:
            errors["card_no"] = "Process card number is required."
        if self.replaces_id:
            if self.pk and self.replaces_id == self.pk:
                errors["replaces"] = "流程卡不能替换自身。"
            if self.replaces.tracking_id != self.tracking_id:
                errors["tracking_id"] = "补卡必须继承原流程卡追踪编号。"
        if not self.quantity or self.quantity < 1:
            errors["quantity"] = "Card quantity must be greater than zero."
        if self.unit_weight_g is not None and self.unit_weight_g <= 0:
            errors["unit_weight_g"] = "成品单重必须大于 0。"
        if self.order_id:
            order = self.order
            self.source_item_no = str(self.source_item_no or order.item_no or "").strip()
            if not self.product_name_snapshot:
                self.product_name_snapshot = order.product_name
            if not self.specification_snapshot:
                self.specification_snapshot = order.specification
            if not self.material_snapshot:
                self.material_snapshot = order.material
            if not self.source_order_no:
                self.source_order_no = order.order_no
            if not self.product_code_snapshot:
                self.product_code_snapshot = order.product_code
            if not self.product_specification_id and order.product_specification_id:
                self.product_specification_id = order.product_specification_id
        if self.unit_weight_config_id and self.unit_weight_g is None:
            config = self.unit_weight_config
            self.unit_weight_g = config.unit_weight_g
            self.sample_count_snapshot = config.sample_count
            self.sample_total_weight_g_snapshot = config.sample_total_weight_g
            self.measured_on_snapshot = config.measured_on
        if self.material_issue_weight_kg is not None and self.material_issue_weight_kg < 0:
            errors["material_issue_weight_kg"] = "Material issue weight cannot be negative."
        if self.reprint_count < 0:
            errors["reprint_count"] = "Reprint count cannot be negative."
        if self.received_on and self.received_on < timezone.localdate() and not str(self.backfill_reason or "").strip():
            errors["backfill_reason"] = "A reason is required when entering a historical process card."
        for field_name in ("source_order_no", "product_code_snapshot", "formula_code_snapshot", "special_requirements", "qr_text", "backfill_reason"):
            setattr(self, field_name, str(getattr(self, field_name, "") or "").strip())
        self.notes = str(self.notes or "").strip()
        if errors:
            raise ValidationError(errors)

    @property
    def theoretical_weight_kg(self):
        return (
            Decimal(self.quantity or 0) * Decimal(self.unit_weight_g or 0) / Decimal("1000")
        ).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)

    @property
    def max_allowed_weight_kg(self):
        return (self.theoretical_weight_kg * Decimal("1.10")).quantize(
            Decimal("0.001"), rounding=ROUND_HALF_UP
        )

    @property
    def shipped_net_weight_kg(self):
        return self._tracking_outbound_totals()["weight"]

    @property
    def returned_net_weight_kg(self):
        cases = QualityReworkCase.objects.filter(
            process_card__tracking_id=self.tracking_id,
            origin=QualityReworkCase.Origin.CUSTOMER_RETURN,
        ).exclude(status=QualityReworkCase.Status.CANCELLED)
        total = Decimal("0")
        for case in cases.only("affected_weight_kg", "affected_quantity"):
            if case.affected_weight_kg is not None:
                total += Decimal(case.affected_weight_kg)
            elif case.affected_quantity and self.unit_weight_g:
                total += Decimal(case.affected_quantity) * Decimal(self.unit_weight_g) / Decimal("1000")
        return total

    @property
    def returned_piece_quantity(self):
        total = QualityReworkCase.objects.filter(
            process_card__tracking_id=self.tracking_id,
            origin=QualityReworkCase.Origin.CUSTOMER_RETURN,
        ).exclude(status=QualityReworkCase.Status.CANCELLED).aggregate(
            total=Sum("affected_quantity")
        )["total"]
        return total or 0

    @property
    def shipped_piece_quantity(self):
        """Confirmed piece count when operators entered piece quantities."""
        return self._tracking_outbound_totals()["pieces"]

    def _tracking_outbound_totals(self):
        """Gross shipments for a card chain, including aggregate-line bindings.

        Quick-entry shipments intentionally leave the aggregate line unbound;
        the per-unit binding (or a later return source) contributes that one
        physical unit without pretending every repeat used the same card.
        """

        lines = QualityShipmentLine.objects.filter(
            process_card__tracking_id=self.tracking_id,
            batch__status=QualityShipmentBatch.Status.CONFIRMED,
        )
        line_totals = lines.aggregate(
            pieces=Sum("piece_quantity"), weight=Sum("net_weight_kg")
        )
        pieces = int(line_totals["pieces"] or 0)
        weight = Decimal(line_totals["weight"] or 0)
        linked_batches = set(lines.values_list("batch_id", flat=True))
        occurrences = {}
        for binding in ProcessCardUnitBinding.objects.filter(
            process_card__tracking_id=self.tracking_id,
            shipment_batch__status=QualityShipmentBatch.Status.CONFIRMED,
        ).only(
            "shipment_batch_id", "shipment_unit_no", "piece_quantity", "net_weight_kg"
        ):
            if binding.shipment_batch_id not in linked_batches:
                occurrences[(binding.shipment_batch_id, binding.shipment_unit_no)] = (
                    int(binding.piece_quantity), Decimal(binding.net_weight_kg)
                )
        cases = QualityReworkCase.objects.filter(
            process_card__tracking_id=self.tracking_id,
            origin=QualityReworkCase.Origin.CUSTOMER_RETURN,
        ).exclude(status=QualityReworkCase.Status.CANCELLED).only(
            "id", "shipment_batch_id", "shipment_unit_no",
            "affected_quantity", "affected_weight_kg",
        )
        for case in cases:
            if case.shipment_batch_id in linked_batches:
                continue
            key = (
                case.shipment_batch_id,
                case.shipment_unit_no
                if case.shipment_unit_no is not None
                else f"legacy-{case.pk}",
            )
            occurrences.setdefault(
                key,
                (
                    int(case.affected_quantity or 0),
                    Decimal(case.affected_weight_kg or 0),
                ),
            )
        pieces += sum(value[0] for value in occurrences.values())
        weight += sum((value[1] for value in occurrences.values()), Decimal("0"))
        return {"pieces": pieces, "weight": weight}

    @property
    def delivered_piece_quantity(self):
        """Confirmed pieces still delivered after customer returns."""
        return max(0, self.shipped_piece_quantity - self.returned_piece_quantity)

    @property
    def delivered_net_weight_kg(self):
        """Confirmed sent weight less valid customer-return weight."""
        return max(Decimal("0"), self.shipped_net_weight_kg - self.returned_net_weight_kg)

    @property
    def remaining_weight_kg(self):
        remaining = self.max_allowed_weight_kg - self.delivered_net_weight_kg
        return max(Decimal("0"), remaining)

    @property
    def weight_variance_percent(self):
        theoretical = self.theoretical_weight_kg
        if not theoretical:
            return Decimal("0")
        return (
            (self.delivered_net_weight_kg - theoretical) / theoretical * Decimal("100")
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def refresh_shipping_status(self):
        shipped = self.delivered_net_weight_kg
        if shipped <= 0:
            next_status = self.Status.OPEN
        elif shipped >= self.theoretical_weight_kg:
            next_status = self.Status.SHIPPED
        else:
            next_status = self.Status.PARTIAL_SHIPPED
        if self.status not in (self.Status.CANCELLED, self.Status.REPLACED) and self.status != next_status:
            type(self).objects.filter(pk=self.pk).update(status=next_status)
            self.status = next_status

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.card_no} - {self.order.order_no}"

    @property
    def is_replaced(self):
        return self.status == self.Status.REPLACED or hasattr(self, "replaced_by")

    @property
    def active_replacement(self):
        """Return the newest printable card in this replacement chain."""

        card = self
        seen = set()
        while card.pk not in seen:
            seen.add(card.pk)
            try:
                card = card.replaced_by
            except ProcessCard.DoesNotExist:
                break
        return card


class QualityShipmentBatch(TimeStampedModel):
    """A single delivery operation containing one or more process cards."""

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        CONFIRMED = "CONFIRMED", "Confirmed"
        VOID = "VOID", "Void"

    shipment_no = models.CharField("shipment batch number", max_length=100, unique=True, blank=True, default="")
    client_key = models.CharField("idempotency client key", max_length=128, blank=True, default="")
    # Optional order/product context for the lightweight (non-process-card)
    # entry form.  Existing process-card batches continue to use the card as
    # their authoritative order association.
    order = models.ForeignKey(
        QualityOrder,
        verbose_name="order",
        related_name="quality_shipment_batches",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    product_specification = models.ForeignKey(
        "orders.ProductSpecification",
        verbose_name="product specification",
        related_name="quality_shipment_batches",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    product_name_snapshot = models.CharField(
        "product name snapshot", max_length=200, blank=True, default=""
    )
    specification_snapshot = models.CharField(
        "specification snapshot", max_length=200, blank=True, default=""
    )
    material_snapshot = models.CharField(
        "material snapshot", max_length=100, blank=True, default=""
    )
    unit_weight_g = models.DecimalField(
        "unit weight snapshot (g)",
        max_digits=14,
        decimal_places=5,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
    )
    single_batch_net_weight_kg = models.DecimalField(
        "single batch net weight (kg)",
        max_digits=14,
        decimal_places=3,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.001"))],
    )
    process_card_shipment_quantity = models.PositiveIntegerField(
        "process-card shipment quantity per batch", null=True, blank=True
    )
    product_batch_count = models.PositiveIntegerField(
        "product batch count", null=True, blank=True
    )
    pieces_per_batch = models.PositiveIntegerField(
        "pieces per product batch", null=True, blank=True
    )
    # A null date is an explicit "date to be filled" state.  Omitting the
    # field when creating through the API uses today's local date via default;
    # sending JSON null keeps it pending for later correction.
    shipment_date = models.DateField(
        "actual shipment date",
        null=True,
        blank=True,
        default=timezone.localdate,
        db_index=True,
    )
    inspector = models.ForeignKey(
        QualityEmployee,
        verbose_name="inspector",
        related_name="weighted_shipment_batches",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    # ``inspector`` is retained as the legacy single-responsible-person field.
    # New clients may assign any number of active inspectors through this M2M;
    # serializers keep the first selected person mirrored into ``inspector``
    # so old reports and APIs continue to work unchanged.
    inspectors = models.ManyToManyField(
        QualityEmployee,
        verbose_name="inspectors",
        related_name="weighted_shipment_batches_many",
        blank=True,
    )
    status = models.CharField(
        "status", max_length=20, choices=Status.choices, default=Status.DRAFT, db_index=True
    )
    customer = models.CharField("customer", max_length=150, blank=True, default="")
    delivery_info = models.TextField("delivery information", blank=True, default="")
    backfill_reason = models.TextField("historical-entry reason", blank=True, default="")
    notes = models.TextField("notes", blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="created by",
        related_name="quality_shipment_batches",
        on_delete=models.PROTECT,
    )

    class Meta:
        ordering = ["-shipment_date", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["client_key"], condition=~Q(client_key=""), name="quality_shipment_batch_client_key_uniq")
        ]

    @property
    def date_pending(self):
        return self.shipment_date is None

    @property
    def net_weight_kg(self):
        total = self.lines.aggregate(total=Sum("net_weight_kg"))["total"]
        return total or Decimal("0")

    @property
    def actual_weight_kg(self):
        return self.net_weight_kg

    @property
    def total_net_weight_kg(self):
        """Canonical total-weight alias used by the current entry form."""
        return self.net_weight_kg

    @property
    def shipped_quantity(self):
        total = self.lines.aggregate(total=Sum("piece_quantity"))["total"]
        return total or 0

    @property
    def piece_quantity(self):
        """Alias used by the weighted entry response for one-line forms."""
        return self.shipped_quantity

    @property
    def line_count(self):
        return self.lines.count()

    def clean(self):
        errors = {}
        self.shipment_no = str(self.shipment_no or "").strip().upper()
        if not self.shipment_no:
            errors["shipment_no"] = "Shipment batch number is required."
        if self.status == self.Status.CONFIRMED and self.shipment_date is None:
            errors["shipment_date"] = "A confirmed shipment must have an actual date."
        if self.shipment_date and self.shipment_date < timezone.localdate() and not str(self.backfill_reason or "").strip():
            errors["backfill_reason"] = "A reason is required when entering a historical shipment."
        if self.inspector_id:
            role = QualityEmployee.objects.filter(pk=self.inspector_id).values_list(
                "role", flat=True
            ).first()
            if role not in (QualityEmployee.Role.INSPECTOR, QualityEmployee.Role.BOTH):
                errors["inspector"] = "The selected employee is not an inspector."
        for field_name in (
            "product_name_snapshot",
            "specification_snapshot",
            "material_snapshot",
            "backfill_reason",
            "notes",
            "client_key",
            "customer",
            "delivery_info",
        ):
            setattr(self, field_name, str(getattr(self, field_name, "") or "").strip())
        if self.unit_weight_g is not None and self.unit_weight_g <= 0:
            errors["unit_weight_g"] = "成品单重必须大于 0。"
        if (
            self.single_batch_net_weight_kg is not None
            and self.single_batch_net_weight_kg <= 0
        ):
            errors["single_batch_net_weight_kg"] = "单批净重必须大于 0。"
        if (
            self.process_card_shipment_quantity is not None
            and self.process_card_shipment_quantity < 1
        ):
            errors["process_card_shipment_quantity"] = "流程卡出货数量必须大于 0。"
        for field_name in ("product_batch_count", "pieces_per_batch"):
            value = getattr(self, field_name, None)
            if value is not None and value < 1:
                errors[field_name] = "批数必须大于 0。"
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if not self.shipment_no:
            self.shipment_no = f"QS-{timezone.localdate():%Y%m%d}-{uuid.uuid4().hex[:8].upper()}"
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.shipment_no


class QualityShipmentLine(TimeStampedModel):
    """Net-weight delivery line for one process card."""

    batch = models.ForeignKey(
        QualityShipmentBatch,
        verbose_name="shipment batch",
        related_name="lines",
        on_delete=models.PROTECT,
    )
    process_card = models.ForeignKey(
        ProcessCard,
        verbose_name="process card",
        related_name="shipment_lines",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    # Direct order lines are accepted for the lightweight shipment form.  A
    # process-card line still takes precedence when both associations exist.
    order = models.ForeignKey(
        QualityOrder,
        verbose_name="order",
        related_name="quality_shipment_lines",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    product_specification = models.ForeignKey(
        "orders.ProductSpecification",
        verbose_name="product specification",
        related_name="quality_shipment_lines",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    specification_snapshot = models.CharField(
        "specification snapshot", max_length=200, blank=True, default=""
    )
    material_snapshot = models.CharField(
        "material snapshot", max_length=100, blank=True, default=""
    )
    net_weight_kg = models.DecimalField(
        "net shipment weight (kg)",
        max_digits=14,
        decimal_places=3,
        validators=[MinValueValidator(Decimal("0.001"))],
    )
    piece_quantity = models.PositiveIntegerField(
        "optional piece quantity", null=True, blank=True
    )
    unit_weight_g_snapshot = models.DecimalField(
        max_digits=14, decimal_places=5, null=True, blank=True
    )
    single_batch_net_weight_kg = models.DecimalField(
        "single batch net weight (kg)",
        max_digits=14,
        decimal_places=3,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.001"))],
    )
    process_card_shipment_quantity = models.PositiveIntegerField(
        "process-card shipment quantity per batch", null=True, blank=True
    )
    product_batch_count = models.PositiveIntegerField(
        "product batch count", null=True, blank=True
    )
    pieces_per_batch = models.PositiveIntegerField(
        "pieces per product batch", null=True, blank=True
    )
    theoretical_weight_kg_snapshot = models.DecimalField(
        max_digits=14, decimal_places=3, null=True, blank=True
    )
    max_allowed_weight_kg_snapshot = models.DecimalField(
        max_digits=14, decimal_places=3, null=True, blank=True
    )
    notes = models.TextField("notes", blank=True, default="")

    class Meta:
        ordering = ["id"]

    @property
    def returned_piece_quantity(self):
        """Customer-return pieces, including weight-only return records."""
        total = 0
        returns = self.rework_cases.filter(
            origin=QualityReworkCase.Origin.CUSTOMER_RETURN,
        ).exclude(status=QualityReworkCase.Status.CANCELLED).filter(
            shipment_allocations__isnull=True
        )
        unit = self.unit_weight_g_snapshot or (
            self.process_card.unit_weight_g if self.process_card_id else None
        )
        for case in returns.only("affected_quantity", "affected_weight_kg"):
            if case.affected_quantity is not None:
                total += int(case.affected_quantity)
            elif case.affected_weight_kg is not None and unit:
                total += int(
                    (Decimal(case.affected_weight_kg) * Decimal("1000") / Decimal(unit))
                    .quantize(Decimal("1"), rounding=ROUND_HALF_UP)
                )
        total += self.rework_allocations.filter(
            case__origin=QualityReworkCase.Origin.CUSTOMER_RETURN,
        ).exclude(case__status=QualityReworkCase.Status.CANCELLED).aggregate(
            total=Sum("piece_quantity")
        )["total"] or 0
        return total

    @property
    def returned_net_weight_kg(self):
        """Customer-return weight, deriving it from pieces when necessary."""
        total = Decimal("0")
        returns = self.rework_cases.filter(
            origin=QualityReworkCase.Origin.CUSTOMER_RETURN,
        ).exclude(status=QualityReworkCase.Status.CANCELLED).filter(
            shipment_allocations__isnull=True
        )
        unit = self.unit_weight_g_snapshot or (
            self.process_card.unit_weight_g if self.process_card_id else None
        )
        for case in returns.only("affected_quantity", "affected_weight_kg"):
            if case.affected_weight_kg is not None:
                total += Decimal(case.affected_weight_kg)
            elif case.affected_quantity is not None and unit:
                total += Decimal(case.affected_quantity) * Decimal(unit) / Decimal("1000")
        total += Decimal(
            self.rework_allocations.filter(
                case__origin=QualityReworkCase.Origin.CUSTOMER_RETURN,
            ).exclude(case__status=QualityReworkCase.Status.CANCELLED).aggregate(
                total=Sum("net_weight_kg")
            )["total"]
            or 0
        )
        return total

    @property
    def delivered_piece_quantity(self):
        return max(0, int(self.piece_quantity or 0) - self.returned_piece_quantity)

    @property
    def delivered_net_weight_kg(self):
        return max(Decimal("0"), Decimal(self.net_weight_kg or 0) - self.returned_net_weight_kg)

    def clean(self):
        errors = {}
        if self.piece_quantity is not None and self.piece_quantity < 1:
            errors["piece_quantity"] = "Piece quantity must be greater than zero."
        if not self.process_card_id and not self.order_id:
            errors["process_card"] = "出货明细必须关联流程卡或订单。"
        if self.batch_id:
            batch = self.batch
            if batch.status == QualityShipmentBatch.Status.VOID:
                errors["batch"] = "A void shipment batch cannot receive lines."
        card = self.process_card if self.process_card_id else None
        order = self.order if self.order_id else (card.order if card else None)
        if card and self.process_card_shipment_quantity is None:
            self.process_card_shipment_quantity = card.quantity
        if card and self.order_id and self.order_id != card.order_id:
            errors["order"] = "出货明细订单必须与流程卡一致。"
        if order and self.product_specification_id is None and order.product_specification_id:
            self.product_specification_id = order.product_specification_id
        if card and self.product_specification_id is None and card.product_specification_id:
            self.product_specification_id = card.product_specification_id
        if order:
            expected_spec = str(
                (card.specification_snapshot if card else "") or order.specification or ""
            ).strip()
            expected_material = str(
                (card.material_snapshot if card else "") or order.material or ""
            ).strip()
            if not self.specification_snapshot:
                self.specification_snapshot = expected_spec
            if not self.material_snapshot:
                self.material_snapshot = expected_material
            if self.specification_snapshot and expected_spec and self.specification_snapshot != expected_spec:
                errors["specification_snapshot"] = "规格必须与所选流程卡/订单一致。"
            if self.material_snapshot and expected_material and self.material_snapshot != expected_material:
                errors["material_snapshot"] = "材质必须与所选流程卡/订单一致。"
        unit_weight = self.unit_weight_g_snapshot
        if unit_weight is None and card:
            unit_weight = card.unit_weight_g
            self.unit_weight_g_snapshot = unit_weight
        if unit_weight is None or unit_weight <= 0:
            errors["process_card" if card else "unit_weight_g_snapshot"] = (
                "出货明细必须填写大于 0 的成品单重。"
            )
        if self.product_batch_count is not None and self.product_batch_count < 1:
            errors["product_batch_count"] = "批数必须大于 0。"
        if self.pieces_per_batch is not None and self.pieces_per_batch < 1:
            errors["pieces_per_batch"] = "每批件数必须大于 0。"
        if (
            self.process_card_shipment_quantity is not None
            and self.process_card_shipment_quantity < 1
        ):
            errors["process_card_shipment_quantity"] = "流程卡出货数量必须大于 0。"
        if (
            self.single_batch_net_weight_kg is not None
            and self.single_batch_net_weight_kg <= 0
        ):
            errors["single_batch_net_weight_kg"] = "单批净重必须大于 0。"

        # Repeated shipment batches are a quick-entry feature for equal scale
        # readings.  Persist the expanded total weight/count while retaining
        # the single reading so reopening a draft cannot multiply it twice.
        repeat_count = int(self.product_batch_count or 1)
        single_batch_pieces = None
        if self.single_batch_net_weight_kg is not None:
            if self.process_card_shipment_quantity is None:
                errors["process_card_shipment_quantity"] = (
                    "重复称重出货必须填写单批流程卡出货数量。"
                )
            self.net_weight_kg = (
                Decimal(self.single_batch_net_weight_kg) * Decimal(repeat_count)
            ).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
            if unit_weight:
                single_batch_pieces = int(
                    (
                        Decimal(self.single_batch_net_weight_kg)
                        * Decimal("1000")
                        / Decimal(unit_weight)
                    ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
                )
                self.pieces_per_batch = single_batch_pieces
                self.piece_quantity = single_batch_pieces * repeat_count
        elif not card and unit_weight and self.net_weight_kg:
            # Compatibility for older clients that submit only a total net
            # weight and do not use the repeat shortcut.
            self.piece_quantity = int(
                (Decimal(self.net_weight_kg) * Decimal("1000") / Decimal(unit_weight))
                .quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            )
        elif self.piece_quantity is None and unit_weight and self.net_weight_kg:
            self.piece_quantity = int(
                (Decimal(self.net_weight_kg) * Decimal("1000") / Decimal(unit_weight))
                .quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            )
        if self.net_weight_kg is None or self.net_weight_kg <= 0:
            errors["net_weight_kg"] = "Net shipment weight must be greater than zero."

        if self.process_card_shipment_quantity is not None:
            if single_batch_pieces is None and self.piece_quantity:
                single_batch_pieces = int(
                    (Decimal(self.piece_quantity) / Decimal(repeat_count)).quantize(
                        Decimal("1"), rounding=ROUND_HALF_UP
                    )
                )
            if (
                single_batch_pieces is not None
                and Decimal(single_batch_pieces)
                > Decimal(self.process_card_shipment_quantity) * Decimal("1.10")
            ):
                errors["process_card_shipment_quantity"] = (
                    "单批实际出货数量不能超过流程卡出货数量的 110%。"
                )
        quantity_basis = int(
            (
                self.process_card_shipment_quantity * repeat_count
                if self.process_card_shipment_quantity is not None
                else None
            )
            or self.piece_quantity
            or (card.quantity if card else (order.order_quantity if order else 0))
            or 0
        )
        theoretical = (
            Decimal(quantity_basis) * Decimal(unit_weight or 0) / Decimal("1000")
        ).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
        if self.theoretical_weight_kg_snapshot is None and theoretical:
            self.theoretical_weight_kg_snapshot = theoretical
        if self.max_allowed_weight_kg_snapshot is None and theoretical:
            self.max_allowed_weight_kg_snapshot = (
                theoretical * Decimal("1.10")
            ).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
        if card and self.batch_id and self.batch.status == QualityShipmentBatch.Status.CONFIRMED:
            # Draft and void documents do not consume the card's delivered
            # allowance.  Confirmation performs the same check inside a
            # transaction, making the cap safe under concurrent requests.
            prior_delivery = card.delivered_net_weight_kg
            if self.batch_id and self.batch.status == QualityShipmentBatch.Status.CONFIRMED:
                prior_delivery -= QualityShipmentLine.objects.filter(batch_id=self.batch_id, process_card_id=self.process_card_id).aggregate(total=Sum("net_weight_kg"))["total"] or Decimal("0")
                if prior_delivery + self.net_weight_kg > card.max_allowed_weight_kg:
                    errors["net_weight_kg"] = (
                        "累计出货净重不能超过流程卡理论重量的 110%。"
                    )
            if self.batch_id and self.batch.status == QualityShipmentBatch.Status.CONFIRMED and self.piece_quantity is not None:
                previous_pieces = QualityShipmentLine.objects.filter(
                    process_card_id=self.process_card_id,
                    batch__status=QualityShipmentBatch.Status.CONFIRMED,
                ).exclude(batch_id=self.batch_id).aggregate(total=Sum("piece_quantity"))["total"] or 0
                if previous_pieces - card.returned_piece_quantity + self.piece_quantity > card.quantity:
                    errors["piece_quantity"] = "Cumulative shipped pieces cannot exceed the process-card quantity."
        self.specification_snapshot = str(self.specification_snapshot or "").strip()
        self.material_snapshot = str(self.material_snapshot or "").strip()
        self.notes = str(self.notes or "").strip()
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        result = super().save(*args, **kwargs)
        if self.process_card_id:
            self.process_card.refresh_shipping_status()
        return result

    def __str__(self):
        card_label = self.process_card.card_no if self.process_card_id else (self.order.order_no if self.order_id else self.pk)
        return f"{self.batch.shipment_no} - {card_label}"


class ProcessCardUnitBinding(TimeStampedModel):
    """Current outbound occurrence of one physical process-card batch.

    Equal-weight quick entry stores many physical batches in one shipment
    line.  This additive table lets zero, some, or all of those physical units
    be bound to individual cards without rewriting the historical weight row.
    On re-shipment the same binding moves to the new confirmed shipment while
    the return cases retain every earlier source.
    """

    process_card = models.OneToOneField(
        ProcessCard,
        related_name="unit_binding",
        on_delete=models.PROTECT,
    )
    shipment_batch = models.ForeignKey(
        QualityShipmentBatch,
        related_name="process_card_bindings",
        on_delete=models.PROTECT,
    )
    shipment_unit_no = models.PositiveIntegerField()
    piece_quantity = models.PositiveIntegerField()
    net_weight_kg = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        validators=[MinValueValidator(Decimal("0.001"))],
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="quality_process_card_unit_bindings",
        on_delete=models.PROTECT,
    )

    class Meta:
        ordering = ["shipment_batch_id", "shipment_unit_no"]
        constraints = [
            models.UniqueConstraint(
                fields=["shipment_batch", "shipment_unit_no"],
                name="quality_card_binding_batch_unit_uniq",
            ),
            models.CheckConstraint(
                condition=Q(shipment_unit_no__gt=0),
                name="quality_card_binding_unit_gt_zero",
            ),
            models.CheckConstraint(
                condition=Q(piece_quantity__gt=0),
                name="quality_card_binding_piece_gt_zero",
            ),
            models.CheckConstraint(
                condition=Q(net_weight_kg__gt=0),
                name="quality_card_binding_weight_gt_zero",
            ),
        ]

    def clean(self):
        errors = {}
        if self.shipment_batch_id and self.shipment_batch.status != QualityShipmentBatch.Status.CONFIRMED:
            errors["shipment_batch"] = "流程卡只能绑定已确认的出货记录。"
        if self.process_card_id and self.process_card.status in (
            ProcessCard.Status.CANCELLED,
            ProcessCard.Status.REPLACED,
        ):
            errors["process_card"] = "已取消或已替换的流程卡不能绑定出货批次。"
        if not self.shipment_unit_no or self.shipment_unit_no < 1:
            errors["shipment_unit_no"] = "物理批号必须大于0。"
        if not self.piece_quantity or self.piece_quantity < 1:
            errors["piece_quantity"] = "物理批件数必须大于0。"
        if self.net_weight_kg is None or self.net_weight_kg <= 0:
            errors["net_weight_kg"] = "物理批净重必须大于0。"
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.process_card.card_no} / {self.shipment_batch.shipment_no} #{self.shipment_unit_no}"


class DefectReason(TimeStampedModel):
    """Maintainable reason/tag dictionary used by customer returns."""

    code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True, db_index=True)
    is_system = models.BooleanField(default=False)
    sort_order = models.PositiveIntegerField(default=0)
    notes = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["sort_order", "id"]

    def clean(self):
        self.code = str(self.code or "").strip().upper()
        self.name = str(self.name or "").strip()
        self.notes = str(self.notes or "").strip()
        errors = {}
        if not self.code:
            errors["code"] = "原因代码不能为空。"
        if not self.name:
            errors["name"] = "原因名称不能为空。"
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class QualityReworkCase(TimeStampedModel):
    """A quality issue that can contain any number of rework attempts."""

    class Origin(models.TextChoices):
        INTERNAL = "INTERNAL", "Internal rework"
        CUSTOMER_RETURN = "CUSTOMER_RETURN", "Customer return"

    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        PROCESSING = "PROCESSING", "Processing"
        WAITING_REINSPECTION = "WAITING_REINSPECTION", "Waiting for reinspection"
        COMPLETED = "COMPLETED", "Completed"
        WAITING_REWORK = "WAITING_REWORK", "待返工"
        RESHIPPED = "RESHIPPED", "已重新出货"
        SCRAPPED = "SCRAPPED", "Scrapped"
        CANCELLED = "CANCELLED", "Cancelled"

    case_no = models.CharField(
        "rework case number", max_length=100, unique=True, blank=True, default=""
    )
    origin = models.CharField(
        "origin", max_length=20, choices=Origin.choices, default=Origin.INTERNAL, db_index=True
    )
    process_card = models.ForeignKey(
        ProcessCard,
        verbose_name="process card",
        related_name="rework_cases",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    shipment_line = models.ForeignKey(
        QualityShipmentLine,
        verbose_name="shipment line",
        related_name="rework_cases",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    shipment_batch = models.ForeignKey(
        QualityShipmentBatch,
        verbose_name="source shipment batch",
        related_name="rework_cases",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    shipment_unit_no = models.PositiveIntegerField(
        "physical shipment unit number", null=True, blank=True
    )
    return_round = models.PositiveIntegerField(
        "customer return round", null=True, blank=True, db_index=True
    )
    is_current_return = models.BooleanField(default=False, db_index=True)
    opened_on = models.DateField(
        "opened date", default=timezone.localdate, null=True, blank=True, db_index=True
    )
    date_is_approximate = models.BooleanField(default=False)
    backfill_reason = models.TextField("historical-entry reason", blank=True, default="")
    reason_category = models.CharField(
        "reason category",
        max_length=20,
        choices=ReturnRework.ReasonCategory.choices,
        default=ReturnRework.ReasonCategory.OTHER,
        db_index=True,
    )
    reason = models.TextField("reason", blank=True, default="")
    responsible_inspector = models.ForeignKey(
        QualityEmployee,
        verbose_name="responsible inspector",
        related_name="weighted_rework_cases",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    inspectors = models.ManyToManyField(
        QualityEmployee,
        related_name="weighted_rework_cases_many",
        blank=True,
    )
    primary_reason = models.ForeignKey(
        DefectReason,
        related_name="primary_rework_cases",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    secondary_reasons = models.ManyToManyField(
        DefectReason,
        related_name="secondary_rework_cases",
        blank=True,
    )
    affected_quantity = models.PositiveIntegerField(
        "affected quantity", null=True, blank=True
    )
    affected_weight_kg = models.DecimalField(
        "affected weight (kg)",
        max_digits=14,
        decimal_places=3,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
    )
    status = models.CharField(
        "status", max_length=30, choices=Status.choices, default=Status.OPEN, db_index=True
    )
    closed_on = models.DateField("closed date", null=True, blank=True)
    notes = models.TextField("notes", blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="created by",
        related_name="quality_rework_cases",
        on_delete=models.PROTECT,
    )

    class Meta:
        ordering = ["-opened_on", "-id"]
        indexes = [
            models.Index(fields=["status", "opened_on"]),
            models.Index(fields=["origin", "opened_on"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(shipment_unit_no__isnull=True)
                    | Q(shipment_batch__isnull=False)
                ),
                name="quality_rework_unit_requires_batch_ck",
            ),
            models.UniqueConstraint(
                fields=["shipment_batch", "shipment_unit_no"],
                condition=(
                    Q(origin="CUSTOMER_RETURN")
                    & Q(shipment_batch__isnull=False)
                    & Q(shipment_unit_no__isnull=False)
                    & Q(status__in=["OPEN", "PROCESSING", "WAITING_REINSPECTION", "WAITING_REWORK"])
                ),
                name="quality_active_return_batch_unit_uniq",
            ),
            models.CheckConstraint(
                condition=Q(return_round__isnull=True) | Q(return_round__gt=0),
                name="quality_rework_return_round_gt_zero",
            ),
        ]

    @property
    def attempt_count(self):
        annotated = getattr(self, "attempt_count_value", None)
        if annotated is not None:
            return annotated
        return self.attempts.count()

    @property
    def latest_attempt(self):
        return self.attempts.order_by("-attempt_no", "-id").first()

    def clean(self):
        errors = {}
        self.case_no = str(self.case_no or "").strip().upper()
        if self.shipment_line_id and not self.shipment_batch_id:
            self.shipment_batch_id = self.shipment_line.batch_id
        if self.origin == self.Origin.CUSTOMER_RETURN and not (
            self.shipment_line_id or self.shipment_batch_id
        ):
            errors["shipment_line"] = (
                "A customer-return case must link to a confirmed shipment."
            )
        if not self.process_card_id and not self.shipment_line_id and not self.shipment_batch_id:
            errors["process_card"] = "Link a process card or shipment line."
        if self.shipment_unit_no is not None and not self.shipment_batch_id:
            errors["shipment_unit_no"] = "整批序号必须关联原出货批次。"
        if self.shipment_unit_no is not None and self.shipment_unit_no < 1:
            errors["shipment_unit_no"] = "整批序号必须大于0。"
        if self.return_round is not None and self.return_round < 1:
            errors["return_round"] = "退货返工次数必须大于0。"
        if (
            self.shipment_line_id
            and self.shipment_batch_id
            and self.shipment_line.batch_id != self.shipment_batch_id
        ):
            errors["shipment_line"] = "原出货明细不属于所选出货批次。"
        if self.affected_quantity is not None and self.affected_quantity < 1:
            errors["affected_quantity"] = "Affected quantity must be greater than zero."
        if self.affected_weight_kg is not None and self.affected_weight_kg <= 0:
            errors["affected_weight_kg"] = "Affected weight must be greater than zero."
        if (
            self.shipment_line_id
            and self.process_card_id
            and self.shipment_line.process_card_id is not None
            and self.shipment_line.process_card_id != self.process_card_id
        ):
            errors["shipment_line"] = "返工关联的出货明细必须属于所选流程卡。"
        if self.shipment_line_id and not self.process_card_id:
            self.process_card_id = self.shipment_line.process_card_id
        if (
            self.shipment_line_id
            and self.origin == self.Origin.CUSTOMER_RETURN
            and self.shipment_unit_no is None
        ):
            line = self.shipment_line
            line_unit_weight = line.unit_weight_g_snapshot or (
                line.process_card.unit_weight_g if line.process_card_id else None
            )
            if self.affected_weight_kg is None and self.affected_quantity and line_unit_weight:
                self.affected_weight_kg = (
                    Decimal(self.affected_quantity) * Decimal(line_unit_weight) / Decimal("1000")
                ).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
            if line.batch.status != QualityShipmentBatch.Status.CONFIRMED:
                errors["shipment_line"] = "A customer return must link to a confirmed shipment line."
            if self.affected_quantity is not None and line.piece_quantity is not None and self.affected_quantity > line.piece_quantity:
                errors["affected_quantity"] = "Affected quantity cannot exceed the shipped line quantity."
            if self.affected_weight_kg is not None and self.affected_weight_kg > line.net_weight_kg:
                errors["affected_weight_kg"] = "Affected weight cannot exceed the shipped line weight."
            existing_returns = type(self).objects.filter(
                shipment_line_id=self.shipment_line_id,
                origin=self.Origin.CUSTOMER_RETURN,
            ).exclude(status=self.Status.CANCELLED)
            if self.pk:
                existing_returns = existing_returns.exclude(pk=self.pk)
            returned_weight = existing_returns.aggregate(total=Sum("affected_weight_kg"))["total"] or Decimal("0")
            returned_qty = existing_returns.aggregate(total=Sum("affected_quantity"))["total"] or 0
            if self.affected_weight_kg is not None and returned_weight + self.affected_weight_kg > line.net_weight_kg:
                errors["affected_weight_kg"] = "累计客户退回重量不能超过该出货行净重。"
            if self.affected_quantity is not None and line.piece_quantity is not None and returned_qty + self.affected_quantity > line.piece_quantity:
                errors["affected_quantity"] = "累计客户退回件数不能超过该出货行件数。"
        if (
            (self.opened_on is None or self.date_is_approximate or self.opened_on < timezone.localdate())
            and not str(self.backfill_reason or "").strip()
        ):
            errors["backfill_reason"] = "A reason is required when entering a historical rework case."
        if self.responsible_inspector_id:
            role = QualityEmployee.objects.filter(pk=self.responsible_inspector_id).values_list(
                "role", flat=True
            ).first()
            if role not in (QualityEmployee.Role.INSPECTOR, QualityEmployee.Role.BOTH):
                errors["responsible_inspector"] = "The selected employee is not an inspector."
        self.reason = str(self.reason or "").strip()
        self.backfill_reason = str(self.backfill_reason or "").strip()
        self.notes = str(self.notes or "").strip()
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if not self.case_no:
            # A temporary unique number allows the stable operator-facing R1,
            # R2 … sequence to be assigned after the database gives us an id.
            self.case_no = f"R-TMP-{uuid.uuid4().hex.upper()}"
        self.full_clean()
        result = super().save(*args, **kwargs)
        if self.process_card_id and self.origin == self.Origin.CUSTOMER_RETURN:
            self.process_card.refresh_shipping_status()
        if self.case_no.startswith("R-TMP-"):
            self.case_no = f"R{self.pk}"
            type(self).objects.filter(pk=self.pk).update(case_no=self.case_no)
        return result

    def __str__(self):
        return self.case_no


class QualityReturnAllocation(TimeStampedModel):
    """Immutable order/line share of one physical returned shipment unit."""

    case = models.ForeignKey(
        QualityReworkCase,
        related_name="shipment_allocations",
        on_delete=models.PROTECT,
    )
    shipment_line = models.ForeignKey(
        QualityShipmentLine,
        related_name="rework_allocations",
        on_delete=models.PROTECT,
    )
    piece_quantity = models.PositiveIntegerField()
    net_weight_kg = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        validators=[MinValueValidator(Decimal("0"))],
    )

    class Meta:
        ordering = ["case_id", "shipment_line_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["case", "shipment_line"],
                name="quality_return_case_line_uniq",
            ),
            models.CheckConstraint(
                condition=Q(piece_quantity__gt=0),
                name="quality_return_allocation_piece_gt_zero",
            ),
            models.CheckConstraint(
                condition=Q(net_weight_kg__gte=0),
                name="quality_return_allocation_weight_nonnegative",
            ),
        ]

    def clean(self):
        errors = {}
        if not self.piece_quantity or self.piece_quantity < 1:
            errors["piece_quantity"] = "退货分配件数必须大于0。"
        if self.net_weight_kg is None or self.net_weight_kg < 0:
            errors["net_weight_kg"] = "退货分配净重不能小于0。"
        if (
            self.case_id
            and self.case.shipment_batch_id
            and self.shipment_line_id
            and self.shipment_line.batch_id != self.case.shipment_batch_id
        ):
            errors["shipment_line"] = "退货分配明细不属于原出货批次。"
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.case.case_no} / line {self.shipment_line_id}"


class QualityReworkAttempt(TimeStampedModel):
    """One immutable-in-sequence processing/reinspection round of a case."""

    case = models.ForeignKey(
        QualityReworkCase,
        verbose_name="rework case",
        related_name="attempts",
        on_delete=models.PROTECT,
    )
    attempt_no = models.PositiveIntegerField("attempt number", null=True, blank=True)
    attempt_date = models.DateField(
        "attempt date", default=timezone.localdate, db_index=True
    )
    backfill_reason = models.TextField("historical-entry reason", blank=True, default="")
    rework_employee = models.ForeignKey(
        QualityEmployee,
        verbose_name="rework employee",
        related_name="weighted_rework_attempts",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    input_quantity = models.PositiveIntegerField(default=0)
    reworked_quantity = models.PositiveIntegerField(default=0)
    recovered_quantity = models.PositiveIntegerField(default=0)
    scrap_quantity = models.PositiveIntegerField(default=0)
    input_weight_kg = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0"))],
    )
    reworked_weight_kg = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0"))],
    )
    recovered_weight_kg = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0"))],
    )
    scrap_weight_kg = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0"))],
    )
    status = models.CharField(
        "status", max_length=20, choices=QualityReworkCase.Status.choices,
        default=QualityReworkCase.Status.PROCESSING, db_index=True
    )
    notes = models.TextField("notes", blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="created by",
        related_name="quality_rework_attempts",
        on_delete=models.PROTECT,
    )

    class Meta:
        ordering = ["case_id", "attempt_no", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["case", "attempt_no"], name="quality_rework_case_attempt_unique"
            ),
            models.CheckConstraint(
                condition=Q(input_quantity__gte=0)
                & Q(reworked_quantity__lte=F("input_quantity"))
                & Q(recovered_quantity__lte=F("reworked_quantity"))
                & Q(scrap_quantity__lte=F("reworked_quantity")),
                name="quality_rework_attempt_quantity_bounds",
            ),
        ]

    def clean(self):
        errors = {}
        if self.attempt_no is not None and self.attempt_no < 1:
            errors["attempt_no"] = "Attempt number must be greater than zero."
        if not any(
            (
                int(self.input_quantity or 0),
                int(self.reworked_quantity or 0),
                Decimal(self.input_weight_kg or 0) != 0,
                Decimal(self.reworked_weight_kg or 0) != 0,
            )
        ):
            errors["input_quantity"] = "Enter quantity or weight for the attempt."
        if int(self.reworked_quantity or 0) > int(self.input_quantity or 0):
            errors["reworked_quantity"] = "Reworked quantity cannot exceed input quantity."
        if int(self.recovered_quantity or 0) + int(self.scrap_quantity or 0) > int(
            self.reworked_quantity or 0
        ):
            errors["recovered_quantity"] = "Recovered plus scrap quantity cannot exceed reworked quantity."
        if Decimal(self.reworked_weight_kg or 0) > Decimal(self.input_weight_kg or 0):
            errors["reworked_weight_kg"] = "Reworked weight cannot exceed input weight."
        if Decimal(self.recovered_weight_kg or 0) + Decimal(self.scrap_weight_kg or 0) > Decimal(
            self.reworked_weight_kg or 0
        ):
            errors["recovered_weight_kg"] = "Recovered plus scrap weight cannot exceed reworked weight."
        if self.case_id:
            case = self.case
            if (
                not self.pk
                and case.status
                in (
                    QualityReworkCase.Status.CANCELLED,
                    QualityReworkCase.Status.SCRAPPED,
                )
            ):
                errors["case"] = "已取消或已报废的退货返工记录不能新增轮次。"
            # Each record is a processing round of the same returned goods,
            # not additive consumption.  A full batch may therefore be put
            # through R1, R2 and R3 after repeated failed reinspections.
            if (
                case.affected_quantity is not None
                and int(self.input_quantity or 0) > case.affected_quantity
            ):
                errors["input_quantity"] = "本轮投入件数不能超过退货返工记录的退货件数。"
            if (
                case.affected_weight_kg is not None
                and Decimal(self.input_weight_kg or 0) > case.affected_weight_kg
            ):
                errors["input_weight_kg"] = "本轮投入重量不能超过退货返工记录的退货重量。"
            if (
                case.origin == QualityReworkCase.Origin.CUSTOMER_RETURN
                and case.shipment_unit_no is not None
            ):
                expected_quantity = int(case.affected_quantity or 0)
                expected_weight = Decimal(case.affected_weight_kg or 0)
                if int(self.input_quantity or 0) != expected_quantity:
                    errors["input_quantity"] = "整批退货的本轮投入件数必须等于原整批件数。"
                if int(self.reworked_quantity or 0) != expected_quantity:
                    errors["reworked_quantity"] = "整批退货的本轮返工件数必须等于原整批件数。"
                if Decimal(self.input_weight_kg or 0) != expected_weight:
                    errors["input_weight_kg"] = "整批退货的本轮投入重量必须等于原整批净重。"
                if Decimal(self.reworked_weight_kg or 0) != expected_weight:
                    errors["reworked_weight_kg"] = "整批退货的本轮返工重量必须等于原整批净重。"
        if self.attempt_date and self.attempt_date < timezone.localdate() and not str(self.backfill_reason or "").strip():
            errors["backfill_reason"] = "A reason is required when entering a historical rework attempt."
        if self.rework_employee_id:
            role = QualityEmployee.objects.filter(pk=self.rework_employee_id).values_list(
                "role", flat=True
            ).first()
            if role not in (QualityEmployee.Role.REWORKER, QualityEmployee.Role.BOTH):
                errors["rework_employee"] = "The selected employee is not a rework employee."
        self.backfill_reason = str(self.backfill_reason or "").strip()
        self.notes = str(self.notes or "").strip()
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.case_id and not self.attempt_no:
            previous = type(self).objects.filter(case_id=self.case_id).order_by(
                "-attempt_no"
            ).values_list("attempt_no", flat=True).first()
            self.attempt_no = int(previous or 0) + 1
        self.full_clean()
        result = super().save(*args, **kwargs)
        if self.case_id:
            next_status = {
                QualityReworkCase.Status.COMPLETED: QualityReworkCase.Status.COMPLETED,
                QualityReworkCase.Status.SCRAPPED: QualityReworkCase.Status.SCRAPPED,
                QualityReworkCase.Status.WAITING_REINSPECTION: QualityReworkCase.Status.WAITING_REINSPECTION,
            }.get(self.status, QualityReworkCase.Status.PROCESSING)
            QualityReworkCase.objects.filter(pk=self.case_id).exclude(
                status=QualityReworkCase.Status.CANCELLED
            ).update(status=next_status)
        return result

    def __str__(self):
        return f"{self.case.case_no} / {self.attempt_no}"
