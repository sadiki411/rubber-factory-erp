from django.contrib import admin

from .models import (
    InventoryBatch,
    InventoryContainer,
    InventoryLocation,
    InventoryOutbound,
    InventoryOutboundLine,
    InventoryProduct,
    InventoryTransaction,
    MaterialRemainder,
    MaterialRemainderUse,
)


@admin.register(InventoryLocation)
class InventoryLocationAdmin(admin.ModelAdmin):
    list_display = ("code", "rack_code", "rack_type", "level_no", "position_no", "is_active", "allows_basket", "allows_bag")
    list_filter = ("rack_type", "is_active", "allows_basket", "allows_bag")
    search_fields = ("code", "rack_code", "label")


@admin.register(InventoryProduct)
class InventoryProductAdmin(admin.ModelAdmin):
    list_display = ("product_code", "product_name", "specification", "material", "unit_weight_g", "is_active")
    list_filter = ("is_active",)
    search_fields = ("product_code", "product_name", "specification", "material")


@admin.register(InventoryBatch)
class InventoryBatchAdmin(admin.ModelAdmin):
    list_display = ("batch_no", "product", "received_on", "quality_status", "inspector")
    list_filter = ("quality_status", "source_type")
    search_fields = ("batch_no", "product__product_code", "product__specification")


@admin.register(InventoryContainer)
class InventoryContainerAdmin(admin.ModelAdmin):
    list_display = ("container_code", "batch", "container_type", "location", "quantity", "status")
    list_filter = ("container_type", "status")
    search_fields = ("container_code", "batch__batch_no", "location__code")


@admin.register(InventoryOutbound)
class InventoryOutboundAdmin(admin.ModelAdmin):
    list_display = ("outbound_no", "order", "shipment_ref", "status", "created_by", "created_at")
    list_filter = ("status",)
    search_fields = ("outbound_no", "shipment_ref")


admin.site.register(InventoryOutboundLine)
admin.site.register(InventoryTransaction)


@admin.register(MaterialRemainder)
class MaterialRemainderAdmin(admin.ModelAdmin):
    list_display = ("material", "weight_kg", "stored_on", "fridge_code", "created_by")
    search_fields = ("material", "fridge_code")


admin.site.register(MaterialRemainderUse)

