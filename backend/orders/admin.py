from django.contrib import admin

from .models import (
    BusinessImportBatch,
    BusinessRecordRevision,
    MaterialReceipt,
    ProductInspectionCriterion,
    ProductSpecification,
)
from .services import model_snapshot, record_revision


class NoDeleteAdmin(admin.ModelAdmin):
    def has_delete_permission(self, request, obj=None):
        return False


class AuditedAdmin(NoDeleteAdmin):
    def save_model(self, request, obj, form, change):
        before = None
        was_active = None
        if change and obj.pk:
            current = type(obj).objects.get(pk=obj.pk)
            before = model_snapshot(current)
            was_active = getattr(current, "is_active", None)
        super().save_model(request, obj, form, change)
        action = BusinessRecordRevision.Action.CREATE
        if change:
            action = BusinessRecordRevision.Action.UPDATE
            if was_active is True and getattr(obj, "is_active", None) is False:
                action = BusinessRecordRevision.Action.DEACTIVATE
        record_revision(obj, request.user, action, before=before)


@admin.register(ProductSpecification)
class ProductSpecificationAdmin(AuditedAdmin):
    list_display = (
        "specification",
        "material",
        "customer_product_no",
        "mold_no",
        "is_active",
        "updated_at",
    )
    list_filter = ("is_active", "material")
    search_fields = (
        "product_name",
        "customer_product_no",
        "specification",
        "material",
        "mold_no",
    )
    readonly_fields = (
        "normalized_key",
        "source_batch",
        "source_sheet",
        "source_row",
        "source_key",
        "raw_data",
        "created_at",
        "updated_at",
    )


@admin.register(MaterialReceipt)
class MaterialReceiptAdmin(AuditedAdmin):
    list_display = (
        "order_no",
        "item_no",
        "batch_no",
        "weight_kg",
        "manufactured_on",
    )
    search_fields = ("order_no", "item_no", "batch_no", "finished_product_name")
    list_filter = ("manufactured_on", "material")
    readonly_fields = ("source_batch", "source_sheet", "source_row", "source_key", "raw_data")


@admin.register(ProductInspectionCriterion)
class ProductInspectionCriterionAdmin(AuditedAdmin):
    list_display = (
        "product_specification",
        "project_no",
        "customer",
        "category",
        "inspection_item",
        "unit",
    )
    search_fields = ("project_no", "customer", "category", "inspection_item")
    readonly_fields = ("source_batch", "source_sheet", "source_row", "source_key", "raw_data")


@admin.register(BusinessImportBatch)
class BusinessImportBatchAdmin(admin.ModelAdmin):
    list_display = ("original_name", "source_type", "parser", "status", "created_at")
    list_filter = ("source_type", "status", "parser")
    search_fields = ("original_name", "sha256")
    readonly_fields = (
        "source_type",
        "parser",
        "status",
        "original_name",
        "original_file",
        "sha256",
        "payload",
        "errors",
        "warnings",
        "created_by",
        "created_at",
        "committed_at",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(BusinessRecordRevision)
class BusinessRecordRevisionAdmin(admin.ModelAdmin):
    list_display = ("record_type", "record_id", "action", "operator", "created_at")
    list_filter = ("record_type", "action", "created_at")
    search_fields = ("record_id", "operator__username")
    readonly_fields = (
        "record_type",
        "record_id",
        "action",
        "snapshot",
        "changes",
        "source_batch",
        "operator",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
