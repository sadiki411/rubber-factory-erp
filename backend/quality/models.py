from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import F, Q, Sum

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
    source_sheet = models.CharField(max_length=100, blank=True, default="")
    source_row = models.PositiveIntegerField(null=True, blank=True)
    source_key = models.CharField(max_length=255, blank=True, default="", db_index=True)
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
            "source_sheet",
            "source_key",
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
