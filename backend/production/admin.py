from django.contrib import admin

from .models import (
    ProductionDailyLog,
    ProductionImportBatch,
    ProductionRun,
    ProductionSettlementRevision,
    ProductionStation,
)


@admin.register(ProductionStation)
class ProductionStationAdmin(admin.ModelAdmin):
    list_display = ("code", "group", "position_no", "machine", "is_active")
    list_filter = ("group", "is_active")
    search_fields = ("code", "machine__code", "machine__name")


class ProductionDailyLogInline(admin.TabularInline):
    model = ProductionDailyLog
    extra = 0


@admin.register(ProductionRun)
class ProductionRunAdmin(admin.ModelAdmin):
    list_display = (
        "order_no",
        "station",
        "specification",
        "status",
        "operator",
        "loaded_at",
        "expected_change_at",
        "unloaded_at",
        "settled_at",
    )
    list_filter = ("status", "station__group", "loaded_at")
    search_fields = (
        "order_no",
        "specification",
        "material",
        "operator",
        "mold__asset_code",
    )
    date_hierarchy = "loaded_at"
    readonly_fields = ("settled_at", "settled_by", "created_at", "updated_at")
    inlines = [ProductionDailyLogInline]


@admin.register(ProductionDailyLog)
class ProductionDailyLogAdmin(admin.ModelAdmin):
    list_display = (
        "run",
        "production_date",
        "operator",
        "produced_mold_count",
    )
    list_filter = ("production_date", "run__station__group")
    search_fields = ("run__order_no", "operator", "notes")
    date_hierarchy = "production_date"


@admin.register(ProductionSettlementRevision)
class ProductionSettlementRevisionAdmin(admin.ModelAdmin):
    list_display = (
        "run",
        "revision_no",
        "action",
        "changed_by",
        "changed_at",
    )
    list_filter = ("action", "changed_at")
    search_fields = ("run__order_no", "changed_by__username")
    readonly_fields = (
        "run",
        "revision_no",
        "action",
        "cavities",
        "produced_mold_count",
        "unit_price",
        "material_unit_price",
        "actual_good_quantity",
        "actual_defective_quantity",
        "total_material_kg",
        "labor_cost",
        "energy_cost",
        "other_cost",
        "settlement_notes",
        "changed_by",
        "changed_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ProductionImportBatch)
class ProductionImportBatchAdmin(admin.ModelAdmin):
    list_display = ("id", "original_name", "status", "created_by", "created_at", "committed_at")
    list_filter = ("status", "created_at")
    search_fields = ("id", "original_name", "created_by__username")
    readonly_fields = (
        "id",
        "status",
        "original_name",
        "payload",
        "errors",
        "warnings",
        "created_by",
        "created_at",
        "committed_at",
    )

    def has_add_permission(self, request):
        return False
