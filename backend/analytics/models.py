from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models

from molds.models import Machine, TimeStampedModel
from quality.models import QualityEmployee, ReturnRework


ZERO = Decimal("0")


def normalize_staff_name(value):
    return " ".join(str(value or "").split())


class SoftVoidModel(TimeStampedModel):
    voided_at = models.DateTimeField("作废时间", null=True, blank=True, db_index=True)
    voided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="作废人",
        related_name="%(app_label)s_%(class)s_voided",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    void_reason = models.CharField("作废原因", max_length=200, blank=True, default="")

    class Meta:
        abstract = True

    def clean_void_state(self, errors):
        self.void_reason = str(self.void_reason or "").strip()
        if bool(self.voided_at) != bool(self.voided_by_id):
            errors["voided_at"] = "作废时间和作废人必须同时保留。"
        if self.voided_at and not self.void_reason:
            self.void_reason = "用户作废"
        if not self.voided_at:
            self.void_reason = ""


class ManualPerformanceEntry(SoftVoidModel):
    class EntryType(models.TextChoices):
        PRODUCTION = "PRODUCTION", "生产"
        QUALITY = "QUALITY", "品检/出货"
        REWORK = "REWORK", "退回/返工"

    entry_date = models.DateField("绩效日期", db_index=True)
    entry_type = models.CharField(
        "记录类型", max_length=20, choices=EntryType.choices, db_index=True
    )
    staff_name = models.CharField(
        "人员名称", max_length=100, blank=True, default="", db_index=True
    )
    order_no = models.CharField(
        "订单号", max_length=100, blank=True, default="", db_index=True
    )
    machine = models.ForeignKey(
        Machine,
        verbose_name="机台",
        related_name="manual_performance_entries",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    quality_employee = models.ForeignKey(
        QualityEmployee,
        verbose_name="品检/返工员工",
        related_name="manual_performance_entries",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )

    produced_mold_count = models.PositiveIntegerField("生产模数", default=0)
    production_hours = models.DecimalField(
        "实际生产工时",
        max_digits=10,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(ZERO)],
    )
    inspection_quantity = models.PositiveIntegerField("质检数量", default=0)
    qualified_quantity = models.PositiveIntegerField("合格数量", default=0)
    defective_quantity = models.PositiveIntegerField("不良数量", default=0)
    shipped_quantity = models.PositiveIntegerField("出货数量", default=0)
    returned_quantity = models.PositiveIntegerField("退回数量", default=0)
    reason_category = models.CharField(
        "不良原因分类",
        max_length=20,
        choices=ReturnRework.ReasonCategory.choices,
        blank=True,
        default="",
    )
    reworked_quantity = models.PositiveIntegerField("返工数量", default=0)
    recovered_quantity = models.PositiveIntegerField("返工合格数量", default=0)
    scrap_quantity = models.PositiveIntegerField("报废数量", default=0)
    rework_hours = models.DecimalField(
        "返工工时",
        max_digits=10,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(ZERO)],
    )
    notes = models.TextField("备注", blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="创建人",
        related_name="manual_performance_entries",
        on_delete=models.PROTECT,
    )

    class Meta:
        ordering = ["-entry_date", "-id"]
        indexes = [
            models.Index(
                fields=["entry_type", "entry_date"],
                name="manual_perf_type_date_idx",
            ),
        ]

    @staticmethod
    def _has_positive(instance, fields):
        return any((getattr(instance, field, 0) or 0) > 0 for field in fields)

    def clean(self):
        errors = {}
        self.staff_name = normalize_staff_name(self.staff_name)
        self.order_no = str(self.order_no or "").strip().upper()
        self.notes = str(self.notes or "").strip()

        production_fields = ("produced_mold_count", "production_hours")
        quality_fields = (
            "inspection_quantity",
            "qualified_quantity",
            "defective_quantity",
            "shipped_quantity",
        )
        rework_fields = (
            "returned_quantity",
            "reworked_quantity",
            "recovered_quantity",
            "scrap_quantity",
            "rework_hours",
        )
        for field_name in ("production_hours", "rework_hours"):
            if (getattr(self, field_name, ZERO) or ZERO) < ZERO:
                errors[field_name] = (
                    f"{self._meta.get_field(field_name).verbose_name}不能小于0。"
                )

        if self.quality_employee_id and not self.staff_name:
            self.staff_name = (
                QualityEmployee.objects.filter(pk=self.quality_employee_id)
                .values_list("name", flat=True)
                .first()
                or ""
            )

        if self.entry_type == self.EntryType.PRODUCTION:
            if not self.staff_name:
                errors["staff_name"] = "生产补录必须填写人员名称。"
            if not self.machine_id:
                errors["machine"] = "生产补录必须关联机台。"
            if not self._has_positive(self, production_fields):
                errors["produced_mold_count"] = "生产模数或实际生产工时至少填写一项。"
            if self._has_positive(self, quality_fields + rework_fields):
                errors["entry_type"] = "生产记录不能填写品检或返工数量。"
            if self.quality_employee_id:
                errors["quality_employee"] = "生产记录不能关联品检/返工员工。"
            if self.reason_category:
                errors["reason_category"] = "生产记录不能填写不良原因分类。"
        elif self.entry_type == self.EntryType.QUALITY:
            if not self.quality_employee_id:
                errors["quality_employee"] = "品检补录必须关联品检员工。"
            if int(self.inspection_quantity or 0) < 1:
                errors["inspection_quantity"] = "品检记录的质检数量必须大于0。"
            if int(self.inspection_quantity or 0) != (
                int(self.qualified_quantity or 0)
                + int(self.defective_quantity or 0)
            ):
                errors["qualified_quantity"] = "合格数量与不良数量之和必须等于质检数量。"
            if int(self.shipped_quantity or 0) > int(self.qualified_quantity or 0):
                errors["shipped_quantity"] = "出货数量不能超过合格数量。"
            if self._has_positive(self, production_fields + rework_fields):
                errors["entry_type"] = "品检记录不能填写生产或返工数据。"
            if self.machine_id:
                errors["machine"] = "品检记录不能关联生产机台。"
            if self.reason_category:
                errors["reason_category"] = "品检记录不能填写返工不良原因分类。"
            if self.quality_employee_id:
                role = (
                    QualityEmployee.objects.filter(pk=self.quality_employee_id)
                    .values_list("role", flat=True)
                    .first()
                )
                if role not in (
                    QualityEmployee.Role.INSPECTOR,
                    QualityEmployee.Role.BOTH,
                ):
                    errors["quality_employee"] = "所选员工不具备品检岗位。"
        elif self.entry_type == self.EntryType.REWORK:
            if not self.quality_employee_id:
                errors["quality_employee"] = "返工补录必须关联返工员工。"
            if not self.reason_category:
                self.reason_category = ReturnRework.ReasonCategory.OTHER
            if int(self.returned_quantity or 0) < 1:
                errors["returned_quantity"] = "返工记录的退回数量必须大于0。"
            if int(self.reworked_quantity or 0) > int(self.returned_quantity or 0):
                errors["reworked_quantity"] = "返工数量不能超过退回数量。"
            if int(self.recovered_quantity or 0) + int(
                self.scrap_quantity or 0
            ) > int(self.reworked_quantity or 0):
                errors["recovered_quantity"] = "返工合格数量与报废数量之和不能超过返工数量。"
            if self._has_positive(self, production_fields + quality_fields):
                errors["entry_type"] = "返工记录不能填写生产或品检数据。"
            if self.machine_id:
                errors["machine"] = "返工记录不能关联生产机台。"
            if self.quality_employee_id:
                role = (
                    QualityEmployee.objects.filter(pk=self.quality_employee_id)
                    .values_list("role", flat=True)
                    .first()
                )
                if role not in (
                    QualityEmployee.Role.REWORKER,
                    QualityEmployee.Role.BOTH,
                ):
                    errors["quality_employee"] = "所选员工不具备返工岗位。"
        else:
            errors["entry_type"] = "无效的绩效记录类型。"

        self.clean_void_state(errors)
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.entry_date} - {self.get_entry_type_display()} - {self.staff_name}"


class ManualFinancialEntry(SoftVoidModel):
    class Direction(models.TextChoices):
        INCOME = "INCOME", "收入"
        EXPENSE = "EXPENSE", "支出"

    class Category(models.TextChoices):
        SALES = "SALES", "销售收入"
        MATERIAL = "MATERIAL", "材料成本"
        LABOR = "LABOR", "人工成本"
        ENERGY = "ENERGY", "能耗成本"
        OTHER = "OTHER", "其他"
        ADJUSTMENT = "ADJUSTMENT", "调整"

    occurred_on = models.DateField("发生日期", db_index=True)
    direction = models.CharField(
        "收支方向", max_length=10, choices=Direction.choices, db_index=True
    )
    category = models.CharField(
        "财务分类", max_length=20, choices=Category.choices, db_index=True
    )
    amount = models.DecimalField(
        "金额",
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    machine = models.ForeignKey(
        Machine,
        verbose_name="关联机台",
        related_name="manual_financial_entries",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    staff_name = models.CharField("关联人员", max_length=100, blank=True, default="")
    order_no = models.CharField(
        "订单号", max_length=100, blank=True, default="", db_index=True
    )
    description = models.CharField("事项说明", max_length=200)
    notes = models.TextField("备注", blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="创建人",
        related_name="manual_financial_entries",
        on_delete=models.PROTECT,
    )

    class Meta:
        ordering = ["-occurred_on", "-id"]
        indexes = [
            models.Index(
                fields=["direction", "occurred_on"],
                name="manual_fin_dir_date_idx",
            ),
        ]

    def clean(self):
        errors = {}
        self.staff_name = normalize_staff_name(self.staff_name)
        self.order_no = str(self.order_no or "").strip().upper()
        self.description = str(self.description or "").strip()
        self.notes = str(self.notes or "").strip()
        if not self.description:
            errors["description"] = "事项说明不能为空。"
        if self.amount is None or self.amount <= ZERO:
            errors["amount"] = "金额必须大于0。"
        if self.direction == self.Direction.INCOME and self.category not in (
            self.Category.SALES,
            self.Category.OTHER,
            self.Category.ADJUSTMENT,
        ):
            errors["category"] = "收入只能使用销售收入、其他或调整分类。"
        if self.direction == self.Direction.EXPENSE and self.category == self.Category.SALES:
            errors["category"] = "支出不能使用销售收入分类。"
        self.clean_void_state(errors)
        if errors:
            raise ValidationError(errors)

    @property
    def profit_effect(self):
        value = self.amount or ZERO
        return value if self.direction == self.Direction.INCOME else -value

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.occurred_on} - {self.get_direction_display()} - {self.amount}"
