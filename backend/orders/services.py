import json
from decimal import Decimal

from django.core.serializers.json import DjangoJSONEncoder
from django.db.models import DateTimeField, DecimalField, F, Max, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce, Greatest
from django.forms.models import model_to_dict

from .models import BusinessRecordRevision, MaterialReceipt


MODEL_RECORD_TYPES = {
    "ProductSpecification": BusinessRecordRevision.RecordType.PRODUCT_SPECIFICATION,
    "QualityOrder": BusinessRecordRevision.RecordType.ORDER,
    "MaterialReceipt": BusinessRecordRevision.RecordType.MATERIAL_RECEIPT,
    "ProductInspectionCriterion": BusinessRecordRevision.RecordType.INSPECTION_CRITERION,
}


def json_safe(value):
    return json.loads(json.dumps(value, cls=DjangoJSONEncoder, ensure_ascii=False))


def model_snapshot(instance):
    snapshot = model_to_dict(instance)
    snapshot["id"] = instance.pk
    if hasattr(instance, "created_at"):
        snapshot["created_at"] = instance.created_at
    if hasattr(instance, "updated_at"):
        snapshot["updated_at"] = instance.updated_at
    return json_safe(snapshot)


def order_identity_exists(order_no, item_no, *, exclude_pk=None):
    """Check the user-facing order number + item identity used by manual entry."""
    from quality.models import QualityOrder

    order_no = str(order_no or "").strip()
    item_no = str(item_no or "").strip()
    if not order_no:
        return False
    queryset = QualityOrder.objects.filter(
        order_no__iexact=order_no,
        item_no__iexact=item_no,
    )
    if exclude_pk is not None:
        queryset = queryset.exclude(pk=exclude_pk)
    return queryset.exists()


def diff_snapshots(before, after):
    changes = {}
    for key in sorted(set(before) | set(after)):
        if before.get(key) != after.get(key):
            changes[key] = {"from": before.get(key), "to": after.get(key)}
    return changes


def record_revision(
    instance,
    operator,
    action,
    *,
    source_batch=None,
    before=None,
):
    after = model_snapshot(instance)
    return BusinessRecordRevision.objects.create(
        record_type=MODEL_RECORD_TYPES[type(instance).__name__],
        record_id=instance.pk,
        action=action,
        snapshot=after,
        changes=diff_snapshots(before or {}, after) if before is not None else {},
        source_batch=source_batch,
        operator=operator,
    )


def with_order_activity(queryset):
    """Annotate order material totals and the newest linked business activity.

    Correlated subqueries avoid multiplying receipt weights when an order has
    several production runs and shipments at the same time.
    """
    from production.models import ProductionRun
    from quality.models import QualityShipment, QualityShipmentBatch

    decimal_field = DecimalField(max_digits=18, decimal_places=3)
    receipt_totals = (
        MaterialReceipt.objects.filter(order_id=OuterRef("pk"))
        .values("order_id")
        .annotate(total=Sum("weight_kg"))
        .values("total")[:1]
    )
    receipt_latest = (
        MaterialReceipt.objects.filter(order_id=OuterRef("pk"))
        .values("order_id")
        .annotate(latest=Max("updated_at"))
        .values("latest")[:1]
    )
    run_latest = (
        ProductionRun.objects.filter(order_id=OuterRef("pk"))
        .values("order_id")
        .annotate(latest=Max("updated_at"))
        .values("latest")[:1]
    )
    shipment_latest = (
        QualityShipment.objects.filter(order_id=OuterRef("pk"))
        .values("order_id")
        .annotate(latest=Max("updated_at"))
        .values("latest")[:1]
    )
    weighted_shipment_latest = (
        QualityShipmentBatch.objects.filter(
            status=QualityShipmentBatch.Status.CONFIRMED,
        )
        .filter(
            Q(order_id=OuterRef("pk"))
            | Q(lines__order_id=OuterRef("pk"))
            | Q(lines__process_card__order_id=OuterRef("pk"))
        )
        .order_by("-updated_at")
        .values("updated_at")[:1]
    )
    return (
        queryset.annotate(
            imported_received_material_kg_value=Coalesce(
                Subquery(receipt_totals, output_field=decimal_field),
                Value(Decimal("0")),
                output_field=decimal_field,
            )
        )
        .annotate(
            received_material_kg_value=F("imported_received_material_kg_value")
            + Coalesce(
                F("manual_received_material_kg"),
                Value(Decimal("0")),
                output_field=decimal_field,
            )
        )
        .annotate(
            last_data_updated_at_value=Greatest(
                F("updated_at"),
                Coalesce(
                    Subquery(receipt_latest, output_field=DateTimeField()), F("updated_at")
                ),
                Coalesce(Subquery(run_latest, output_field=DateTimeField()), F("updated_at")),
                Coalesce(
                    Subquery(shipment_latest, output_field=DateTimeField()), F("updated_at")
                ),
                Coalesce(
                    Subquery(weighted_shipment_latest, output_field=DateTimeField()),
                    F("updated_at"),
                ),
            )
        )
    )
