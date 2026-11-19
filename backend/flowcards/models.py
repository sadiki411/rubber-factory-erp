"""Optional physical-package traceability. None of these rows post quantities."""
import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q


class Recorded(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)

    class Meta:
        abstract = True


class AppearanceIssue(models.Model):
    code = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=40)
    is_active = models.BooleanField(default=True)


class StandardRevision(Recorded):
    """Confirmed selection of existing criteria, not a second mutable standards table."""
    product = models.ForeignKey("orders.ProductSpecification", on_delete=models.PROTECT)
    mold = models.ForeignKey("molds.MoldAsset", on_delete=models.PROTECT, null=True, blank=True)
    version = models.PositiveIntegerField()
    source = models.TextField()
    request_key = models.CharField(max_length=128, unique=True)
    request_digest = models.CharField(max_length=64)
    scope = models.TextField(blank=True)
    criteria = models.ManyToManyField("orders.ProductInspectionCriterion")
    snapshot = models.JSONField(default=dict)
    whole_mold = models.CharField(max_length=12, default="UNKNOWN")
    appearance = models.ManyToManyField(AppearanceIssue)
    status = models.CharField(max_length=16, default="LEGACY", db_index=True)
    customer_key = models.CharField(max_length=100, blank=True)
    source_kind = models.CharField(max_length=24, default="UNCONFIRMED")
    applicable_order = models.ForeignKey("quality.QualityOrder", on_delete=models.PROTECT, null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["product", "version"], name="fc_product_standard_version")]
        permissions = [("maintain_product_standard", "维护及生效产品工艺检验标准")]


class PositionDiagram(Recorded):
    product = models.ForeignKey("orders.ProductSpecification", on_delete=models.PROTECT)
    mold = models.ForeignKey("molds.MoldAsset", on_delete=models.PROTECT)
    version = models.PositiveIntegerField()
    direction = models.CharField(max_length=300)
    scope = models.TextField()
    positions = models.TextField()
    attachment = models.OneToOneField("Attachment", on_delete=models.PROTECT)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["product", "mold", "version"], name="fc_diagram_version")]


class CardGroup(Recorded):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_key = models.CharField(max_length=128, unique=True)
    request_digest = models.CharField(max_length=64)
    product = models.ForeignKey("orders.ProductSpecification", on_delete=models.PROTECT)
    run = models.ForeignKey("production.ProductionRun", on_delete=models.PROTECT, null=True, blank=True)
    mold = models.ForeignKey("molds.MoldAsset", on_delete=models.PROTECT, null=True, blank=True)
    orders = models.ManyToManyField("quality.QualityOrder")
    standard = models.ForeignKey(StandardRevision, on_delete=models.PROTECT, null=True, blank=True)
    diagram = models.ForeignKey(PositionDiagram, on_delete=models.PROTECT, null=True, blank=True)
    planned_molds = models.PositiveIntegerField(null=True, blank=True)
    molds_per_card = models.PositiveIntegerField(default=100)
    plan_basis = models.TextField()
    whole_mold = models.CharField(max_length=12, default="UNKNOWN")
    status = models.CharField(max_length=12, default="DRAFT", db_index=True)
    snapshot = models.JSONField(default=dict)
    generated_at = models.DateTimeField(null=True, blank=True)
    generated_by = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="generated_flow_groups", on_delete=models.PROTECT, null=True)
    material_evidence = models.TextField(blank=True)
    context_key = models.CharField(max_length=100, blank=True, db_index=True)
    revision = models.PositiveIntegerField(default=1)
    range_start = models.PositiveIntegerField(null=True, blank=True)
    range_end = models.PositiveIntegerField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, related_name="updated_flow_groups")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["context_key"], condition=Q(status="DRAFT") & ~Q(context_key=""), name="fc_one_context_draft"),
            models.CheckConstraint(condition=(Q(range_start__isnull=True, range_end__isnull=True) | Q(range_start__isnull=False, range_end__isnull=False, range_start__gte=1, range_end__gte=models.F("range_start"))), name="fc_valid_mold_range"),
        ]


class OrderCardPreparation(Recorded):
    """Suggestion only. Never a production/quantity posting or historical backfill."""
    order = models.OneToOneField("quality.QualityOrder", on_delete=models.PROTECT, related_name="card_preparation")
    draft = models.ForeignKey(CardGroup, on_delete=models.PROTECT, null=True, blank=True)
    state = models.CharField(max_length=24, default="PENDING")
    updated_at = models.DateTimeField(auto_now=True)


class InternalCard(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    card_no = models.CharField(max_length=40, unique=True)
    group = models.ForeignKey(CardGroup, related_name="cards", on_delete=models.PROTECT)
    sequence = models.PositiveIntegerField()
    denominator = models.PositiveIntegerField()
    planned_molds = models.PositiveIntegerField()
    status = models.CharField(max_length=12, default="ACTIVE")
    original_date = models.DateField()
    reprint_count = models.PositiveIntegerField(default=0)
    printed_at = models.DateTimeField(null=True)

    class Meta:
        ordering = ["sequence"]
        constraints = [models.UniqueConstraint(fields=["group", "sequence"], name="fc_group_sequence")]


class PrintJob(Recorded):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_key = models.CharField(max_length=128, unique=True)
    request_digest = models.CharField(max_length=64)
    status = models.CharField(max_length=16, default="PENDING")
    template = models.JSONField(default=dict)
    pdf_storage_name = models.CharField(max_length=200, blank=True)
    pdf_sha256 = models.CharField(max_length=64, blank=True)


class PrintItem(models.Model):
    job = models.ForeignKey(PrintJob, related_name="items", on_delete=models.PROTECT)
    card = models.ForeignKey(InternalCard, on_delete=models.PROTECT)
    proposed_reprint = models.PositiveIntegerField()
    status = models.CharField(max_length=12, default="UNKNOWN")
    active = models.BooleanField(default=True)
    confirmed_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["job", "card"], name="fc_job_card"),
            models.UniqueConstraint(fields=["card"], condition=Q(active=True), name="fc_active_print_card"),
        ]


class TemplatePublication(Recorded):
    renderer = models.CharField(max_length=40, unique=True)
    version = models.PositiveIntegerField(unique=True)
    confirmation = models.TextField()


class CardEvent(Recorded):
    """One fact may cover several cards; it is never copied per card or added to output."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_key = models.CharField(max_length=128, unique=True)
    request_digest = models.CharField(max_length=64)
    cards = models.ManyToManyField(InternalCard, related_name="events")
    kind = models.CharField(max_length=24, db_index=True)
    business_at = models.DateTimeField(null=True, blank=True, db_index=True)
    employee = models.ForeignKey("quality.QualityEmployee", on_delete=models.PROTECT, null=True, blank=True)
    employee_name = models.CharField(max_length=100, blank=True)
    values = models.JSONField(default=dict)
    scope = models.TextField(blank=True)
    evidence = models.TextField(blank=True)
    replaces = models.ForeignKey("self", on_delete=models.PROTECT, null=True, blank=True)


class Attachment(Recorded):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    card = models.ForeignKey(InternalCard, related_name="attachments", on_delete=models.PROTECT, null=True)
    # Served exclusively by the authenticated endpoint, never /media/.
    storage_name = models.CharField(max_length=200)
    original_name = models.CharField(max_length=255)
    sha256 = models.CharField(max_length=64)
    content_type = models.CharField(max_length=40)
    replaces = models.ForeignKey("self", on_delete=models.PROTECT, null=True)


class ShipmentSource(Recorded):
    card = models.ForeignKey(InternalCard, related_name="shipment_sources", on_delete=models.PROTECT)
    batch = models.ForeignKey("quality.QualityShipmentBatch", related_name="internal_sources", on_delete=models.PROTECT)
    request_key = models.CharField(max_length=128)
    scope = models.CharField(max_length=16, default="SET_UNKNOWN")
    shipment_unit_no = models.PositiveIntegerField(null=True, blank=True)
    customer_tracking_id = models.UUIDField(null=True, blank=True)
    evidence = models.TextField()
    snapshot = models.JSONField(default=dict)
    state = models.CharField(max_length=16, default="CONFIRMED")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["batch", "card", "request_key"], name="fc_source_idempotent")]


class TraceAudit(Recorded):
    object_type = models.CharField(max_length=30)
    object_id = models.CharField(max_length=100, db_index=True)
    action = models.CharField(max_length=40)
    evidence = models.JSONField(default=dict)
