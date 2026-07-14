from django.contrib import admin

from .models import ManualFinancialEntry, ManualPerformanceEntry


class SoftVoidAdminMixin:
    readonly_fields = (
        "voided_at",
        "voided_by",
        "void_reason",
        "created_at",
        "updated_at",
    )

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ManualPerformanceEntry)
class ManualPerformanceEntryAdmin(SoftVoidAdminMixin, admin.ModelAdmin):
    list_display = (
        "entry_date",
        "entry_type",
        "staff_name",
        "machine",
        "quality_employee",
        "created_by",
        "voided_at",
    )
    list_filter = ("entry_type", "entry_date", "machine", "voided_at")
    search_fields = ("staff_name", "order_no", "notes")
    date_hierarchy = "entry_date"


@admin.register(ManualFinancialEntry)
class ManualFinancialEntryAdmin(SoftVoidAdminMixin, admin.ModelAdmin):
    list_display = (
        "occurred_on",
        "direction",
        "category",
        "amount",
        "machine",
        "order_no",
        "created_by",
        "voided_at",
    )
    list_filter = ("direction", "category", "occurred_on", "machine", "voided_at")
    search_fields = ("staff_name", "order_no", "description", "notes")
    date_hierarchy = "occurred_on"
