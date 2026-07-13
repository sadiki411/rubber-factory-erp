from django.contrib import admin

from .models import (
    ImportBatch,
    Machine,
    MoldAsset,
    MoldModel,
    MoldMovement,
    Processor,
    Rack,
    RackLevel,
    RackSlot,
    RackZone,
)


@admin.register(MoldModel)
class MoldModelAdmin(admin.ModelAdmin):
    list_display = ("code", "product_name", "is_active", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("code", "product_name")


@admin.register(Machine)
class MachineAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "is_active", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("code", "name")


@admin.register(Processor)
class ProcessorAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "contact", "phone", "is_active")
    list_filter = ("is_active",)
    search_fields = ("code", "name", "contact", "phone")


@admin.register(Rack)
class RackAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "is_configured", "structure_locked", "is_active")
    list_filter = ("is_configured", "structure_locked", "is_active")
    search_fields = ("code", "name")
    readonly_fields = ("is_configured", "structure_locked", "created_at", "updated_at")


class StructureReadOnlyAdmin(admin.ModelAdmin):
    """Rack structure is maintained through the validated rack configuration API."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(RackLevel)
class RackLevelAdmin(StructureReadOnlyAdmin):
    list_display = ("rack", "level_no")
    list_filter = ("rack",)


@admin.register(RackZone)
class RackZoneAdmin(StructureReadOnlyAdmin):
    list_display = (
        "level",
        "code",
        "label",
        "capacity_mode",
        "allowed_capacities",
        "supports_stacking",
        "is_active",
    )
    list_filter = ("level__rack", "supports_stacking", "is_active")


@admin.register(RackSlot)
class RackSlotAdmin(StructureReadOnlyAdmin):
    list_display = (
        "display_code",
        "technical_code",
        "capacity_mode",
        "position_no",
        "stack_level",
        "is_blocked",
    )
    list_filter = ("zone__level__rack", "capacity_mode", "stack_level", "is_blocked")
    search_fields = ("display_code", "technical_code")


@admin.register(MoldAsset)
class MoldAssetAdmin(admin.ModelAdmin):
    list_display = (
        "asset_code",
        "mold_model",
        "status",
        "current_slot",
        "current_machine",
        "current_processor",
        "status_changed_at",
        "is_active",
    )
    list_filter = ("status", "is_active", "allows_stacking")
    search_fields = ("asset_code", "mold_model__code", "mold_model__product_name")
    readonly_fields = (
        "status",
        "current_slot",
        "current_machine",
        "current_processor",
        "status_changed_at",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(MoldMovement)
class MoldMovementAdmin(admin.ModelAdmin):
    list_display = ("mold", "action", "from_status", "to_status", "operator", "created_at")
    list_filter = ("action", "from_status", "to_status", "created_at")
    search_fields = ("mold__asset_code", "note", "operator__username")
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ImportBatch)
class ImportBatchAdmin(admin.ModelAdmin):
    list_display = ("id", "original_name", "kind", "status", "created_by", "created_at", "committed_at")
    list_filter = ("kind", "status", "created_at")
    search_fields = ("id", "original_name", "created_by__username")
    readonly_fields = (
        "id",
        "kind",
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

    def has_delete_permission(self, request, obj=None):
        return False
