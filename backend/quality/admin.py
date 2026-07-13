from django.contrib import admin

from .models import QualityEmployee, QualityOrder, QualityShipment, ReturnRework


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
        "batch_no",
        "product_name",
        "specification",
        "material",
        "order_quantity",
        "order_date",
        "due_date",
        "status",
    )
    list_filter = ("status", "order_date", "due_date", "material")
    search_fields = (
        "order_no",
        "batch_no",
        "product_code",
        "product_name",
        "specification",
        "material",
    )
    date_hierarchy = "order_date"


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
