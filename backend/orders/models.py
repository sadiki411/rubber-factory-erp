import uuid
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q

from molds.models import TimeStampedModel


def business_import_path(instance, filename):
    extension = Path(filename).suffix.lower() or ".xlsx"
    return f"business-imports/{instance.id}/{uuid.uuid4().hex}{extension}"


class BusinessImportBatch(models.Model):
    class SourceType(models.TextChoices):
        PRODUCT_SPECIFICATIONS = "PRODUCT_SPECIFICATIONS", "产品规格数据"
        INTERNAL_ORDERS = "INTERNAL_ORDERS", "内部季度订单"
        FACTORY_WORK_CONTACT = "FACTORY_WORK_CONTACT", "生产工作联络单"
        MATERIAL_ISSUE = "MATERIAL_ISSUE", "混料发料清单"

    class Status(models.TextChoices):
        PREVIEWED = "PREVIEWED", "已预检"
        COMMITTING = "COMMITTING", "导入中"
        COMMITTED = "COMMITTED", "已导入"
        FAILED = "FAILED", "失败"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source_type = models.CharField(max_length=40, choices=SourceType.choices)
    parser = models.CharField(max_length=100)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PREVIEWED, db_index=True
    )
    original_name = models.CharField(max_length=255)
    original_file = models.FileField(upload_to=business_import_path)
    sha256 = models.CharField(max_length=64, db_index=True)
    payload = models.JSONField(default=dict)
    errors = models.JSONField(default=list)
    warnings = models.JSONField(default=list)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="business_import_batches",
        on_delete=models.PROTECT,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    committed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["sha256", "source_type"], name="business_import_sha_type_idx"
            )
        ]

    def __str__(self):
        return f"{self.original_name} - {self.get_status_display()}"


class ProductSpecification(TimeStampedModel):
    product_name = models.CharField("产品名称", max_length=200, blank=True, default="")
    customer_product_no = models.CharField(
        "客户产品号", max_length=100, blank=True, default="", db_index=True
    )
    specification = models.CharField("规格", max_length=200, blank=True, default="")
    material = models.CharField("材质", max_length=100, blank=True, default="")
    material_length = models.CharField("料长", max_length=100, blank=True, default="")
    cut_weight = models.CharField("切料重", max_length=100, blank=True, default="")
    strip_count = models.CharField("条数", max_length=100, blank=True, default="")
    primary_curing = models.CharField(
        "一次加硫条件", max_length=300, blank=True, default=""
    )
    secondary_curing = models.CharField(
        "二次加硫条件", max_length=300, blank=True, default=""
    )
    total_cavities = models.CharField("总孔数", max_length=100, blank=True, default="")
    effective_cavities = models.CharField(
        "有效孔数", max_length=100, blank=True, default=""
    )
    mold_in_stock = models.CharField(
        "模具在库", max_length=100, blank=True, default=""
    )
    mold_no = models.CharField("模具号", max_length=100, blank=True, default="")
    mold_size = models.CharField("模具尺寸", max_length=100, blank=True, default="")
    standard_hours = models.CharField(
        "标准工时", max_length=100, blank=True, default=""
    )
    notes = models.TextField("备注", blank=True, default="")
    normalized_key = models.CharField(max_length=500, blank=True, default="", db_index=True)
    is_active = models.BooleanField("启用", default=True, db_index=True)
    source_batch = models.ForeignKey(
        BusinessImportBatch,
        related_name="product_specifications",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    source_sheet = models.CharField(max_length=100, blank=True, default="")
    source_row = models.PositiveIntegerField(null=True, blank=True)
    source_key = models.CharField(max_length=255, blank=True, default="", db_index=True)
    raw_data = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["specification", "material", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["source_key"],
                condition=~Q(source_key=""),
                name="uniq_product_spec_source_key",
            )
        ]

    def clean(self):
        for field_name in (
            "product_name",
            "customer_product_no",
            "specification",
            "material",
            "material_length",
            "cut_weight",
            "strip_count",
            "primary_curing",
            "secondary_curing",
            "total_cavities",
            "effective_cavities",
            "mold_in_stock",
            "mold_no",
            "mold_size",
            "standard_hours",
            "notes",
            "source_sheet",
            "source_key",
        ):
            setattr(self, field_name, str(getattr(self, field_name, "") or "").strip())
        if not any((self.product_name, self.customer_product_no, self.specification)):
            raise ValidationError("产品名称、客户产品号和规格至少填写一项。")
        self.normalized_key = normalize_product_key(
            self.product_name,
            self.customer_product_no,
            self.specification,
            self.material,
            self.mold_no,
        )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("产品规格资料不能物理删除，请改为停用。")

    def __str__(self):
        return self.customer_product_no or self.specification or self.product_name


def normalize_product_key(*values):
    return "|".join(" ".join(str(value or "").split()).casefold() for value in values)


class ProductInspectionCriterion(TimeStampedModel):
    product_specification = models.ForeignKey(
        ProductSpecification,
        related_name="inspection_criteria",
        on_delete=models.PROTECT,
    )
    order = models.ForeignKey(
        "quality.QualityOrder",
        related_name="inspection_criteria",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    item_no = models.CharField("项次", max_length=100, blank=True, default="")
    project_no = models.CharField("项目号", max_length=150, blank=True, default="")
    customer = models.CharField("客户", max_length=150, blank=True, default="")
    category = models.CharField("类别", max_length=100, blank=True, default="")
    version = models.CharField("版本", max_length=100, blank=True, default="")
    inspection_item = models.CharField("检验项目", max_length=200)
    lower_limit = models.CharField("下限", max_length=100, blank=True, default="")
    upper_limit = models.CharField("上限", max_length=100, blank=True, default="")
    unit = models.CharField("单位", max_length=100, blank=True, default="")
    source_batch = models.ForeignKey(
        BusinessImportBatch,
        related_name="inspection_criteria",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    source_sheet = models.CharField(max_length=100, blank=True, default="")
    source_row = models.PositiveIntegerField(null=True, blank=True)
    source_key = models.CharField(max_length=255, blank=True, default="", db_index=True)
    raw_data = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["product_specification_id", "category", "inspection_item", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["source_key"],
                condition=~Q(source_key=""),
                name="uniq_inspection_criterion_source_key",
            )
        ]

    def clean(self):
        for field_name in (
            "item_no",
            "project_no",
            "customer",
            "category",
            "version",
            "inspection_item",
            "lower_limit",
            "upper_limit",
            "unit",
            "source_sheet",
            "source_key",
        ):
            setattr(self, field_name, str(getattr(self, field_name, "") or "").strip())
        if not self.inspection_item:
            raise ValidationError({"inspection_item": "检验项目不能为空。"})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class MaterialReceipt(TimeStampedModel):
    order = models.ForeignKey(
        "quality.QualityOrder",
        related_name="material_receipts",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    order_no = models.CharField("订单号", max_length=100, db_index=True)
    item_no = models.CharField("项次", max_length=100, blank=True, default="")
    finished_product_name = models.CharField(
        "成品品名", max_length=300, blank=True, default=""
    )
    specification = models.CharField("规格", max_length=200, blank=True, default="")
    material = models.CharField("材质", max_length=100, blank=True, default="")
    batch_no = models.CharField("批号", max_length=150, blank=True, default="", db_index=True)
    sheet_size = models.CharField("出片尺寸", max_length=100, blank=True, default="")
    weight_kg = models.DecimalField(
        "重量(kg)",
        max_digits=14,
        decimal_places=3,
        validators=[MinValueValidator(0)],
    )
    manufactured_on = models.DateField("制造日期", null=True, blank=True, db_index=True)
    source_batch = models.ForeignKey(
        BusinessImportBatch,
        related_name="material_receipts",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    source_sheet = models.CharField(max_length=100, blank=True, default="")
    source_row = models.PositiveIntegerField(null=True, blank=True)
    source_key = models.CharField(max_length=255, blank=True, default="", db_index=True)
    raw_data = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-manufactured_on", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(weight_kg__gte=0), name="material_receipt_weight_nonnegative"
            ),
            models.UniqueConstraint(
                fields=["source_key"],
                condition=~Q(source_key=""),
                name="uniq_material_receipt_source_key",
            ),
        ]

    def clean(self):
        for field_name in (
            "order_no",
            "item_no",
            "finished_product_name",
            "specification",
            "material",
            "batch_no",
            "sheet_size",
            "source_sheet",
            "source_key",
        ):
            setattr(self, field_name, str(getattr(self, field_name, "") or "").strip())
        if not self.order_no:
            raise ValidationError({"order_no": "订单号不能为空。"})
        if self.weight_kg is None or self.weight_kg < 0:
            raise ValidationError({"weight_kg": "重量不能小于0。"})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class BusinessRecordRevision(models.Model):
    class RecordType(models.TextChoices):
        PRODUCT_SPECIFICATION = "PRODUCT_SPECIFICATION", "产品规格"
        ORDER = "ORDER", "订单"
        MATERIAL_RECEIPT = "MATERIAL_RECEIPT", "胶料收料"
        INSPECTION_CRITERION = "INSPECTION_CRITERION", "检验标准"

    class Action(models.TextChoices):
        CREATE = "CREATE", "新建"
        IMPORT = "IMPORT", "导入"
        UPDATE = "UPDATE", "修改"
        DEACTIVATE = "DEACTIVATE", "停用"

    record_type = models.CharField(max_length=40, choices=RecordType.choices, db_index=True)
    record_id = models.PositiveBigIntegerField(db_index=True)
    action = models.CharField(max_length=20, choices=Action.choices)
    snapshot = models.JSONField(default=dict)
    changes = models.JSONField(default=dict)
    source_batch = models.ForeignKey(
        BusinessImportBatch,
        related_name="record_revisions",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    operator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="business_record_revisions",
        on_delete=models.PROTECT,
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(
                fields=["record_type", "record_id", "created_at"],
                name="business_revision_record_idx",
            )
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("业务数据审计记录不可修改。")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("业务数据审计记录不可删除。")
