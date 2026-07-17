import json

from django.core.serializers.json import DjangoJSONEncoder
from django.forms.models import model_to_dict

from .models import BusinessRecordRevision


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
