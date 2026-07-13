import uuid
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone

from molds.models import Machine, MoldAsset, TimeStampedModel


ZERO = Decimal("0")
TWO_PLACES = Decimal("0.01")


def _decimal(value):
    return value if isinstance(value, Decimal) else Decimal(str(value or 0))


def _quantize(value, places=TWO_PLACES):
    return _decimal(value).quantize(places, rounding=ROUND_HALF_UP)


def _local_date(value):
    if value is None:
        return None
    if timezone.is_naive(value):
        value = timezone.make_aware(value, timezone.get_current_timezone())
    return timezone.localtime(value).date()


def normalize_operator(value):
    """Normalize operator names so monthly performance groups are stable."""

    return " ".join(str(value or "").split())


PRODUCTION_STATION_LAYOUT = (
    ("A", 1, "1", "A01"),
    ("A", 2, "2", "A02"),
    ("B", 1, "3", "B01"),
    ("B", 2, "4", "B02"),
    ("C", 1, "5", "C01"),
    ("C", 2, "6", "C02"),
)
PRODUCTION_STATION_CODES = tuple(item[2] for item in PRODUCTION_STATION_LAYOUT)
LEGACY_PRODUCTION_STATION_ALIASES = {
    legacy_code: code for _, _, code, legacy_code in PRODUCTION_STATION_LAYOUT
}
LEGACY_PRODUCTION_STATION_ALIASES.update(
    {legacy_code.replace("0", "", 1): code for _, _, code, legacy_code in PRODUCTION_STATION_LAYOUT}
)


def canonical_production_station_code(group, position_no):
    """Return the physical machine number for a group-local position."""

    normalized_group = str(group or "").strip().upper()
    normalized_position = int(position_no or 0)
    for layout_group, layout_position, code, _ in PRODUCTION_STATION_LAYOUT:
        if (normalized_group, normalized_position) == (layout_group, layout_position):
            return code
    raise ValueError("有效机台必须是第一组1/2、第二组3/4或第三组5/6。")


def normalize_production_station_code(value):
    """Normalize current 1-6 codes and the six valid legacy A01-style aliases."""

    text = str(value or "").strip().upper().replace(" ", "")
    if text in LEGACY_PRODUCTION_STATION_ALIASES:
        return LEGACY_PRODUCTION_STATION_ALIASES[text]
    if text.isdigit() and 1 <= int(text) <= 6:
        return str(int(text))
    return text


class ProductionStation(TimeStampedModel):
    class Group(models.TextChoices):
        A = "A", "一组"
        B = "B", "二组"
        C = "C", "三组"

    code = models.CharField("机台编号", max_length=3, unique=True)
    group = models.CharField("机台组", max_length=1, choices=Group.choices)
    position_no = models.PositiveSmallIntegerField("组内编号")
    machine = models.OneToOneField(
        Machine,
        verbose_name="关联机台",
        related_name="production_station",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    is_active = models.BooleanField("启用", default=True)

    class Meta:
        ordering = ["group", "position_no"]
        constraints = [
            models.UniqueConstraint(
                fields=["group", "position_no"], name="uniq_production_group_position"
            ),
            models.CheckConstraint(
                condition=(
                    Q(is_active=True, position_no__gte=1, position_no__lte=2)
                    | Q(is_active=False, position_no__gte=1, position_no__lte=6)
                ),
                name="production_station_position_valid",
            ),
        ]

    def clean(self):
        self.group = str(self.group or "").strip().upper()
        if self.group not in self.Group.values:
            raise ValidationError({"group": "机台组必须为A、B或C。"})
        position_no = int(self.position_no or 0)
        if not 1 <= position_no <= 6:
            raise ValidationError({"position_no": "组内编号必须为1至6。"})
        if self.is_active and position_no > 2:
            raise ValidationError({"position_no": "每组只有两台机台，组内编号必须为1或2。"})
        if self.is_active:
            expected_code = canonical_production_station_code(self.group, position_no)
            if self.code and normalize_production_station_code(self.code) != expected_code:
                raise ValidationError({"code": f"机台编号应为{expected_code}。"})
            self.code = expected_code

    def save(self, *args, **kwargs):
        self.group = str(self.group or "").strip().upper()
        if self.is_active:
            self.code = canonical_production_station_code(self.group, self.position_no)
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.code


class ProductionRun(TimeStampedModel):
    class Status(models.TextChoices):
        PLANNED = "PLANNED", "待上机"
        RUNNING = "RUNNING", "生产中"
        COMPLETED = "COMPLETED", "已完成"
        CANCELLED = "CANCELLED", "已取消"

    ACTIVE_STATUSES = (Status.PLANNED, Status.RUNNING)

    station = models.ForeignKey(
        ProductionStation,
        verbose_name="生产站位",
        related_name="runs",
        on_delete=models.PROTECT,
    )
    order_no = models.CharField("订单号", max_length=100, db_index=True)
    specification = models.CharField("规格", max_length=200)
    material = models.CharField("材质", max_length=100, blank=True)
    mold = models.ForeignKey(
        MoldAsset,
        verbose_name="模具",
        related_name="production_runs",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    order_quantity = models.PositiveIntegerField(
        "订单数量", validators=[MinValueValidator(1)]
    )
    cavities = models.PositiveSmallIntegerField(
        "模具孔数", default=6, validators=[MinValueValidator(1)]
    )
    estimated_defect_rate = models.DecimalField(
        "预估不良率(%)",
        max_digits=5,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(Decimal("0"))],
    )
    planned_mold_count = models.PositiveIntegerField(
        "计划生产模数", validators=[MinValueValidator(1)]
    )
    compound_size = models.CharField("胶料尺寸", max_length=100, blank=True)
    strip_weight_kg = models.DecimalField(
        "条重(kg)",
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
    )
    strips_per_batch = models.PositiveIntegerField(
        "每批条数", null=True, blank=True, validators=[MinValueValidator(1)]
    )
    curing_seconds = models.PositiveIntegerField("硫化时间(秒)", default=0)
    estimated_hours = models.DecimalField(
        "预计生产工时",
        max_digits=10,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(Decimal("0"))],
    )
    loaded_at = models.DateTimeField("上模时间", null=True, blank=True, db_index=True)
    expected_change_at = models.DateTimeField(
        "预计换模时间", null=True, blank=True, db_index=True
    )
    unloaded_at = models.DateTimeField("下机时间", null=True, blank=True)
    status = models.CharField(
        "生产状态", max_length=20, choices=Status.choices, default=Status.PLANNED, db_index=True
    )
    operator = models.CharField("作业员", max_length=100, blank=True)
    unit_price = models.DecimalField(
        "产品单价",
        max_digits=14,
        decimal_places=4,
        default=0,
        validators=[MinValueValidator(Decimal("0"))],
    )
    material_unit_price = models.DecimalField(
        "材料单价(元/kg)",
        max_digits=14,
        decimal_places=4,
        default=0,
        validators=[MinValueValidator(Decimal("0"))],
    )
    actual_good_quantity = models.PositiveIntegerField("实际良品数量", default=0)
    actual_defective_quantity = models.PositiveIntegerField("实际不良数量", default=0)
    total_material_kg = models.DecimalField(
        "总材料用量(kg)",
        max_digits=14,
        decimal_places=3,
        default=0,
        validators=[MinValueValidator(Decimal("0"))],
    )
    labor_cost = models.DecimalField(
        "人工成本",
        max_digits=14,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(Decimal("0"))],
    )
    energy_cost = models.DecimalField(
        "能耗成本",
        max_digits=14,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(Decimal("0"))],
    )
    other_cost = models.DecimalField(
        "其他成本",
        max_digits=14,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(Decimal("0"))],
    )
    settlement_notes = models.TextField("结算备注", blank=True)
    settled_at = models.DateTimeField("结算时间", null=True, blank=True, db_index=True)
    settled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="结算人",
        related_name="settled_production_runs",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    notes = models.TextField("备注", blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="创建人",
        related_name="production_runs",
        on_delete=models.PROTECT,
    )

    class Meta:
        ordering = ["-loaded_at", "-created_at", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(
                        status="PLANNED",
                        loaded_at__isnull=True,
                        expected_change_at__isnull=True,
                        unloaded_at__isnull=True,
                    )
                    | Q(
                        status="RUNNING",
                        loaded_at__isnull=False,
                        unloaded_at__isnull=True,
                    )
                    | Q(
                        status="COMPLETED",
                        loaded_at__isnull=False,
                        unloaded_at__isnull=False,
                    )
                    | Q(
                        status="CANCELLED",
                        loaded_at__isnull=True,
                        expected_change_at__isnull=True,
                        unloaded_at__isnull=True,
                    )
                    | Q(
                        status="CANCELLED",
                        loaded_at__isnull=False,
                        unloaded_at__isnull=False,
                    )
                ),
                name="prod_run_status_time_ck",
            ),
            models.CheckConstraint(
                condition=(
                    Q(unloaded_at__isnull=True)
                    | Q(
                        loaded_at__isnull=False,
                        unloaded_at__gte=models.F("loaded_at"),
                    )
                ),
                name="prod_run_unload_order_ck",
            ),
            models.CheckConstraint(
                condition=(
                    Q(expected_change_at__isnull=True)
                    | Q(
                        loaded_at__isnull=False,
                        expected_change_at__gte=models.F("loaded_at"),
                    )
                ),
                name="prod_run_expected_order_ck",
            ),
            models.UniqueConstraint(
                fields=["station", "order_no"],
                name="uniq_run_station_order",
            ),
            models.UniqueConstraint(
                fields=["station"],
                condition=Q(status__in=["PLANNED", "RUNNING"]),
                name="uniq_active_run_per_station",
            ),
            models.UniqueConstraint(
                fields=["mold"],
                condition=Q(
                    status__in=["PLANNED", "RUNNING"], mold__isnull=False
                ),
                name="uniq_active_run_per_mold",
            ),
        ]

    def clean(self):
        errors = {}
        if not self.order_quantity or self.order_quantity < 1:
            errors["order_quantity"] = "订单数量必须大于0。"
        if not self.cavities or self.cavities < 1:
            errors["cavities"] = "模具孔数必须大于0。"
        if not self.planned_mold_count or self.planned_mold_count < 1:
            errors["planned_mold_count"] = "计划生产模数必须大于0。"
        if not ZERO <= _decimal(self.estimated_defect_rate) <= Decimal("100"):
            errors["estimated_defect_rate"] = "预估不良率必须在0至100之间。"
        if self.status == self.Status.PLANNED:
            if self.loaded_at:
                errors["loaded_at"] = "待上机订单不能填写上模时间。"
            if self.expected_change_at:
                errors["expected_change_at"] = "待上机订单不能填写预计换模时间。"
            if self.unloaded_at:
                errors["unloaded_at"] = "待上机订单不能填写下机时间。"
        elif self.status == self.Status.RUNNING:
            if not self.loaded_at:
                errors["loaded_at"] = "生产中订单必须填写上模时间。"
            if self.unloaded_at:
                errors["unloaded_at"] = "生产中订单不能填写下机时间。"
        elif self.status == self.Status.COMPLETED:
            if not self.loaded_at:
                errors["loaded_at"] = "完成订单必须填写上模时间。"
            if not self.unloaded_at:
                errors["unloaded_at"] = "完成订单必须填写下机时间。"
        elif self.status == self.Status.CANCELLED:
            if bool(self.loaded_at) != bool(self.unloaded_at):
                errors["unloaded_at"] = "已开机的取消订单必须同时保留上模和下机时间。"
            if not self.loaded_at and self.expected_change_at:
                errors["expected_change_at"] = "未开机的取消订单不能填写预计换模时间。"
        if self.settled_at:
            may_settle = self.status == self.Status.COMPLETED or (
                self.status == self.Status.CANCELLED and self.loaded_at is not None
            )
            if not may_settle:
                errors["settled_at"] = "只有已完成或已上模后取消的订单可以结算。"
            if not self.settled_by_id:
                errors["settled_by"] = "已结算订单必须保留结算人。"
            expected_quantity = self.produced_mold_count * int(self.cavities or 0) if self.pk else 0
            actual_quantity = self.actual_good_quantity + self.actual_defective_quantity
            if actual_quantity != expected_quantity:
                errors["actual_good_quantity"] = (
                    "实际良品与实际不良之和必须等于累计生产模数乘以模具孔数。"
                )
        elif self.settled_by_id:
            errors["settled_by"] = "未结算订单不能填写结算人。"
        if self.pk and self.status == self.Status.PLANNED and self.daily_logs.exists():
            errors["status"] = "待上机订单不能保留生产日报。"
        if (
            self.pk
            and self.status == self.Status.CANCELLED
            and not self.loaded_at
            and self.daily_logs.exists()
        ):
            errors["status"] = "未上模即取消的订单不能保留生产日报。"
        if self.loaded_at and self.unloaded_at and self.unloaded_at < self.loaded_at:
            errors["unloaded_at"] = "下机时间不能早于上模时间。"
        if self.loaded_at and self.expected_change_at and self.expected_change_at < self.loaded_at:
            errors["expected_change_at"] = "预计换模时间不能早于上模时间。"

        if self.status in self.ACTIVE_STATUSES and self.mold_id:
            station_machine_id = (
                ProductionStation.objects.filter(pk=self.station_id)
                .values_list("machine_id", flat=True)
                .first()
                if self.station_id
                else None
            )
            mold_state = MoldAsset.objects.filter(pk=self.mold_id).values_list(
                "status", "current_machine_id", "is_active"
            ).first()
            mold_status, mold_machine_id, mold_is_active = mold_state or (
                None,
                None,
                False,
            )
            if not mold_is_active:
                errors["mold"] = "待上机或生产中的订单不能关联已删除的模具。"
            elif mold_status == MoldAsset.Status.OUTSOURCED:
                errors["mold"] = "待上机或生产中的订单不能关联客户收回的模具。"
            elif self.status == self.Status.RUNNING:
                if station_machine_id is None:
                    errors["station"] = "该生产机台尚未关联模具台账机台，不能登记为生产中。"
                elif (
                    mold_status != MoldAsset.Status.ON_MACHINE
                    or mold_machine_id != station_machine_id
                ):
                    errors["mold"] = "生产中的模具必须已上到该生产机台关联的模具台账机台。"
            elif (
                mold_status == MoldAsset.Status.ON_MACHINE
                and station_machine_id is not None
                and mold_machine_id != station_machine_id
            ):
                errors["mold"] = "待上机订单中的模具不能占用其他机台。"

        has_production_interval = bool(
            self.loaded_at
            and (self.unloaded_at is None or self.unloaded_at > self.loaded_at)
        )
        if self.station_id and has_production_interval:
            overlaps = ProductionRun.objects.filter(
                station_id=self.station_id,
                loaded_at__isnull=False,
            )
            if self.pk:
                overlaps = overlaps.exclude(pk=self.pk)
            if self.unloaded_at and self.unloaded_at > self.loaded_at:
                overlaps = overlaps.filter(loaded_at__lt=self.unloaded_at)
            overlaps = overlaps.filter(
                Q(unloaded_at__isnull=True) | Q(unloaded_at__gt=self.loaded_at)
            )
            if overlaps.exists():
                errors["station"] = "该机台在所填生产时段内已有其他订单。"
        if errors:
            raise ValidationError(errors)

    def calculated_expected_change_at(self):
        if not self.loaded_at:
            return None
        return self.loaded_at + timedelta(
            seconds=float(_decimal(self.estimated_hours) * Decimal("3600"))
        )

    def save(self, *args, **kwargs):
        previous = None
        if self.pk:
            previous = ProductionRun.objects.filter(pk=self.pk).only(
                "loaded_at", "estimated_hours", "expected_change_at"
            ).first()
        expected_changed = bool(
            previous and self.expected_change_at != previous.expected_change_at
        )
        drivers_changed = bool(
            previous
            and (
                self.loaded_at != previous.loaded_at
                or self.estimated_hours != previous.estimated_hours
            )
        )
        preserve_override = bool(
            getattr(self, "_preserve_expected_change", False) or expected_changed
        )
        previous_expected = self.expected_change_at
        if not self.loaded_at:
            self.expected_change_at = None
        elif self.expected_change_at is None or (drivers_changed and not preserve_override):
            self.expected_change_at = self.calculated_expected_change_at()

        update_fields = kwargs.get("update_fields")
        if update_fields is not None and self.expected_change_at != previous_expected:
            kwargs["update_fields"] = set(update_fields) | {"expected_change_at"}
        self.full_clean()
        return super().save(*args, **kwargs)

    def _logs(self):
        return list(self.daily_logs.all())

    @property
    def produced_mold_count(self):
        return sum(log.produced_mold_count for log in self._logs())

    @property
    def good_quantity(self):
        return self.actual_good_quantity

    @property
    def defective_quantity(self):
        return self.actual_defective_quantity

    @property
    def material_kg(self):
        return _quantize(self.total_material_kg, Decimal("0.001"))

    @property
    def is_settled(self):
        return self.settled_at is not None

    @property
    def actual_hours(self):
        if not self.loaded_at:
            return ZERO
        end = self.unloaded_at or timezone.now()
        seconds = max((end - self.loaded_at).total_seconds(), 0)
        return _quantize(Decimal(str(seconds)) / Decimal("3600"))

    @property
    def progress_percent(self):
        if not self.planned_mold_count:
            return ZERO
        return _quantize(
            Decimal(self.produced_mold_count) / Decimal(self.planned_mold_count) * Decimal("100")
        )

    @property
    def remaining_mold_count(self):
        return max(self.planned_mold_count - self.produced_mold_count, 0)

    @property
    def revenue(self):
        if not self.is_settled:
            return ZERO.quantize(TWO_PLACES)
        return _quantize(Decimal(self.actual_good_quantity) * _decimal(self.unit_price))

    @property
    def total_cost(self):
        if not self.is_settled:
            return ZERO.quantize(TWO_PLACES)
        direct_cost = self.labor_cost + self.energy_cost + self.other_cost
        material_cost = self.total_material_kg * _decimal(self.material_unit_price)
        return _quantize(direct_cost + material_cost)

    @property
    def profit(self):
        return _quantize(self.revenue - self.total_cost)

    @property
    def hourly_efficiency(self):
        actual = self.actual_hours
        if actual <= 0 or self.planned_mold_count <= 0 or _decimal(self.estimated_hours) <= 0:
            return ZERO
        earned_hours = (
            _decimal(self.estimated_hours)
            * Decimal(self.produced_mold_count)
            / Decimal(self.planned_mold_count)
        )
        return _quantize(earned_hours / actual * Decimal("100"))

    def __str__(self):
        return f"{self.order_no} - {self.station.code}"


class ProductionDailyLog(TimeStampedModel):
    run = models.ForeignKey(
        ProductionRun,
        verbose_name="生产订单",
        related_name="daily_logs",
        on_delete=models.CASCADE,
    )
    production_date = models.DateField("生产日期", db_index=True)
    operator = models.CharField("作业员", max_length=100)
    produced_mold_count = models.PositiveIntegerField(
        "生产模数", validators=[MinValueValidator(1)]
    )
    curing_seconds_snapshot = models.PositiveIntegerField(
        "硫化时间快照(秒)", default=0, editable=False
    )
    # These legacy columns retain pre-refactor per-day accounting detail for audit
    # only. New API and imports never write them; order settlement lives on the run.
    legacy_good_quantity = models.PositiveIntegerField(
        "历史良品数量", default=0, editable=False
    )
    legacy_defective_quantity = models.PositiveIntegerField(
        "历史不良数量", default=0, editable=False
    )
    legacy_material_kg = models.DecimalField(
        "历史材料用量(kg)", max_digits=12, decimal_places=3, default=0, editable=False
    )
    legacy_labor_cost = models.DecimalField(
        "历史人工成本", max_digits=14, decimal_places=2, default=0, editable=False
    )
    legacy_energy_cost = models.DecimalField(
        "历史能耗成本", max_digits=14, decimal_places=2, default=0, editable=False
    )
    legacy_other_cost = models.DecimalField(
        "历史其他成本", max_digits=14, decimal_places=2, default=0, editable=False
    )
    notes = models.TextField("备注", blank=True)

    class Meta:
        ordering = ["production_date", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["run", "production_date", "operator"],
                name="uniq_daily_log_per_run_date_operator",
            ),
            models.CheckConstraint(
                condition=~Q(operator=""), name="prod_daily_operator_not_empty_ck"
            ),
            models.CheckConstraint(
                condition=Q(produced_mold_count__gte=1),
                name="prod_daily_molds_positive_ck",
            ),
        ]

    def clean(self):
        errors = {}
        if self.run_id:
            run_state = ProductionRun.objects.filter(pk=self.run_id).values_list(
                "status", "loaded_at", "unloaded_at"
            ).first()
            run_status, loaded_at, unloaded_at = run_state or (
                None,
                None,
                None,
            )
            if run_status == ProductionRun.Status.PLANNED:
                errors["run"] = "待上机订单不能填写生产日报。"
            elif run_status == ProductionRun.Status.CANCELLED and not loaded_at:
                errors["run"] = "未上模即取消的订单不能填写生产日报。"
            loaded_date = _local_date(loaded_at)
            unloaded_date = _local_date(unloaded_at)
            if self.production_date and loaded_date and self.production_date < loaded_date:
                errors["production_date"] = "生产日期不能早于上模日期。"
            if (
                self.production_date
                and run_status == ProductionRun.Status.RUNNING
                and self.production_date > timezone.localdate()
            ):
                errors["production_date"] = "生产中订单的日报日期不能晚于今天。"
            if (
                self.production_date
                and run_status
                in (ProductionRun.Status.COMPLETED, ProductionRun.Status.CANCELLED)
                and unloaded_date
                and self.production_date > unloaded_date
            ):
                errors["production_date"] = "生产日期不能晚于下机日期。"
        self.operator = normalize_operator(self.operator)
        if not self.operator:
            errors["operator"] = "作业员不能为空。"
        if not self.produced_mold_count or self.produced_mold_count < 1:
            errors["produced_mold_count"] = "生产模数必须大于0。"
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.operator = normalize_operator(self.operator)
        if not self.pk and self.run_id:
            self.curing_seconds_snapshot = self.run.curing_seconds
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.run.order_no} - {self.production_date} - {self.operator}"


class ProductionSettlementRevision(models.Model):
    class Action(models.TextChoices):
        SETTLED = "SETTLED", "结算"
        INVALIDATED = "INVALIDATED", "日报变更后失效"

    run = models.ForeignKey(
        ProductionRun,
        verbose_name="生产订单",
        related_name="settlement_revisions",
        on_delete=models.PROTECT,
    )
    revision_no = models.PositiveIntegerField("修订号")
    action = models.CharField("操作", max_length=20, choices=Action.choices)
    cavities = models.PositiveSmallIntegerField("结算孔数")
    produced_mold_count = models.PositiveIntegerField("累计生产模数")
    unit_price = models.DecimalField("产品单价", max_digits=14, decimal_places=4)
    material_unit_price = models.DecimalField(
        "材料单价(元/kg)", max_digits=14, decimal_places=4
    )
    actual_good_quantity = models.PositiveIntegerField("实际良品数量", default=0)
    actual_defective_quantity = models.PositiveIntegerField("实际不良数量", default=0)
    total_material_kg = models.DecimalField(
        "总材料用量(kg)", max_digits=14, decimal_places=3, default=0
    )
    labor_cost = models.DecimalField("人工成本", max_digits=14, decimal_places=2, default=0)
    energy_cost = models.DecimalField("能耗成本", max_digits=14, decimal_places=2, default=0)
    other_cost = models.DecimalField("其他成本", max_digits=14, decimal_places=2, default=0)
    settlement_notes = models.TextField("结算备注", blank=True)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="操作人",
        related_name="production_settlement_revisions",
        on_delete=models.PROTECT,
    )
    changed_at = models.DateTimeField("操作时间", auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-revision_no", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["run", "revision_no"],
                name="uniq_production_settlement_revision",
            )
        ]

    def __str__(self):
        return f"{self.run.order_no} - #{self.revision_no}"


class ProductionImportBatch(models.Model):
    class Status(models.TextChoices):
        PREVIEWED = "PREVIEWED", "已预检"
        COMMITTING = "COMMITTING", "导入中"
        COMMITTED = "COMMITTED", "已导入"
        FAILED = "FAILED", "失败"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PREVIEWED
    )
    original_name = models.CharField(max_length=255)
    payload = models.JSONField(default=dict)
    errors = models.JSONField(default=list)
    warnings = models.JSONField(default=list)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="production_import_batches",
        on_delete=models.PROTECT,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    committed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
