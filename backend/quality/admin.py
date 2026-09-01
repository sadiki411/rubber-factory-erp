from django.contrib import admin

from orders.models import BusinessRecordRevision
from orders.services import model_snapshot, record_revision

from .models import (DefectReason, QualityEmployee, QualityOrder, QualityShipment, ReturnRework,
                     ProductUnitWeight, ProcessCard, ProcessCardUnitBinding, QualityShipmentBatch,
                     QualityShipmentBatchRevision, QualityShipmentLine, QualityReworkCase,
                     QualityReworkAttempt)


class NoDeleteAdmin(admin.ModelAdmin):
    def has_delete_permission(self, request, obj=None):
        return False


class AuditAdmin(NoDeleteAdmin):
    readonly_fields = ("created_by", "created_at", "updated_at")

    def save_model(self, request, obj, form, change):
        if not obj.created_by_id:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(QualityEmployee)
class QualityEmployeeAdmin(NoDeleteAdmin):
    list_display = ("employee_no", "name", "team", "role", "is_active", "updated_at")
    list_filter = ("role", "team", "is_active")
    search_fields = ("employee_no", "name", "team")
    readonly_fields = ("created_at", "updated_at")


@admin.register(QualityOrder)
class QualityOrderAdmin(AuditAdmin):
    list_display = (
        "order_no",
        "item_no",
        "batch_no",
        "product_name",
        "specification",
        "material",
        "production_required",
        "order_quantity",
        "order_date",
        "due_date",
        "status",
    )
    list_filter = ("status", "order_date", "due_date", "material")
    search_fields = (
        "order_no",
        "item_no",
        "batch_no",
        "product_code",
        "product_name",
        "specification",
        "material",
    )
    date_hierarchy = "order_date"

    def save_model(self, request, obj, form, change):
        before = model_snapshot(QualityOrder.objects.get(pk=obj.pk)) if change else None
        super().save_model(request, obj, form, change)
        record_revision(
            obj,
            request.user,
            BusinessRecordRevision.Action.UPDATE
            if change
            else BusinessRecordRevision.Action.CREATE,
            before=before,
        )


@admin.register(QualityShipment)
class QualityShipmentAdmin(AuditAdmin):
    list_display = (
        "shipment_no",
        "shipment_date",
        "order",
        "inspector",
        "inspection_quantity",
        "qualified_quantity",
        "defective_quantity",
        "shipped_quantity",
        "rework_count_display",
    )
    list_filter = ("shipment_date", "inspector__team", "inspector")
    search_fields = (
        "shipment_no",
        "order__order_no",
        "order__batch_no",
        "order__product_code",
        "order__product_name",
        "inspector__employee_no",
        "inspector__name",
    )
    date_hierarchy = "shipment_date"

    @admin.display(description="累计返工次数")
    def rework_count_display(self, obj):
        return obj.rework_count


@admin.register(ReturnRework)
class ReturnReworkAdmin(AuditAdmin):
    list_display = (
        "shipment",
        "rework_date",
        "reason_category",
        "responsible_inspector",
        "rework_employee",
        "returned_quantity",
        "reworked_quantity",
        "recovered_quantity",
        "scrap_quantity",
        "status",
    )
    list_filter = (
        "status",
        "reason_category",
        "rework_date",
        "responsible_inspector__team",
        "rework_employee__team",
    )
    search_fields = (
        "shipment__shipment_no",
        "shipment__order__order_no",
        "reason",
        "responsible_inspector__employee_no",
        "responsible_inspector__name",
        "rework_employee__employee_no",
        "rework_employee__name",
    )
    date_hierarchy = "rework_date"


@admin.register(ProductUnitWeight)
class ProductUnitWeightAdmin(AuditAdmin):
    list_display = ("product_specification", "mold_model", "unit_weight_g", "measured_on", "is_active")
    list_filter = ("is_active", "measured_on")
    search_fields = ("product_specification__product_name", "mold_model__code")


@admin.register(ProcessCard)
class ProcessCardAdmin(AuditAdmin):
    list_display = ("card_no", "order", "quantity", "unit_weight_g", "status", "shipped_net_weight_kg", "reprint_count")
    list_filter = ("status", "received_on", "demand_date")
    search_fields = ("card_no", "source_order_no", "source_item_no", "product_code_snapshot", "qr_text")

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.shipment_lines.exists():
            return tuple(field.name for field in self.model._meta.fields)
        return super().get_readonly_fields(request, obj)


@admin.register(ProcessCardUnitBinding)
class ProcessCardUnitBindingAdmin(AuditAdmin):
    list_display = (
        "process_card", "shipment_batch", "shipment_unit_no",
        "piece_quantity", "net_weight_kg", "updated_at",
    )
    search_fields = ("process_card__card_no", "shipment_batch__shipment_no")
    list_filter = ("shipment_batch__shipment_date",)


@admin.register(DefectReason)
class DefectReasonAdmin(NoDeleteAdmin):
    list_display = ("code", "name", "is_active", "is_system", "sort_order")
    list_filter = ("is_active", "is_system")
    search_fields = ("code", "name")
    readonly_fields = ("created_at", "updated_at")


class QualityShipmentLineInline(admin.TabularInline):
    model = QualityShipmentLine
    extra = 0
    # Shipment history is append-only once a batch is posted.  Draft lines
    # are edited by replacing the draft batch through the API; keeping the
    # inline non-destructive prevents an accidental historical deletion in
    # Django admin as well.
    can_delete = False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(QualityShipmentBatch)
class QualityShipmentBatchAdmin(AuditAdmin):
    readonly_fields = ("status", "created_by", "created_at", "updated_at")
    list_display = ("shipment_no", "client_key", "shipment_date", "status", "customer", "net_weight_kg", "line_count")
    list_filter = ("status", "shipment_date")
    search_fields = ("shipment_no", "client_key", "customer", "delivery_info")
    inlines = [QualityShipmentLineInline]

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.status == QualityShipmentBatch.Status.CONFIRMED:
            return tuple(field.name for field in self.model._meta.fields)
        return super().get_readonly_fields(request, obj)


@admin.register(QualityShipmentBatchRevision)
class QualityShipmentBatchRevisionAdmin(NoDeleteAdmin):
    """Read-only audit trail for confirmed shipment amendments/voids."""

    list_display = ("batch", "action", "reason", "operator", "created_at")
    list_filter = ("action", "created_at")
    search_fields = ("batch__shipment_no", "reason", "operator__username")
    readonly_fields = tuple(field.name for field in QualityShipmentBatchRevision._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(QualityShipmentLine)
class QualityShipmentLineAdmin(NoDeleteAdmin):
    list_display = ("batch", "process_card", "net_weight_kg", "piece_quantity")
    list_filter = ("batch__status",)
    search_fields = ("batch__shipment_no", "process_card__card_no")

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.batch.status != QualityShipmentBatch.Status.DRAFT:
            return tuple(field.name for field in self.model._meta.fields)
        return super().get_readonly_fields(request, obj)


@admin.register(QualityReworkCase)
class QualityReworkCaseAdmin(AuditAdmin):
    list_display = ("case_no", "origin", "process_card", "shipment_line", "opened_on", "status", "attempt_count")
    list_filter = ("origin", "status", "opened_on")
    search_fields = ("case_no", "reason", "process_card__card_no")

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.attempts.exists():
            return tuple(field.name for field in self.model._meta.fields)
        return super().get_readonly_fields(request, obj)


@admin.register(QualityReworkAttempt)
class QualityReworkAttemptAdmin(AuditAdmin):
    readonly_fields = ("case", "attempt_no", "created_by", "created_at", "updated_at")
    list_display = ("case", "attempt_no", "attempt_date", "input_quantity", "reworked_quantity", "recovered_quantity", "scrap_quantity", "status")
    list_filter = ("status", "attempt_date")
    search_fields = ("case__case_no", "notes")
