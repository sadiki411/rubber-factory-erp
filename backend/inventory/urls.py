from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    InventoryAvailabilityView,
    InventoryBatchViewSet,
    InventoryContainerViewSet,
    InventoryLocationViewSet,
    InventoryOutboundViewSet,
    InventoryProductViewSet,
    InventoryReceiptView,
    InventorySummaryView,
    InventoryTransactionViewSet,
    MaterialRemainderViewSet,
)


router = DefaultRouter()
router.register("locations", InventoryLocationViewSet, basename="inventory-location")
router.register("products", InventoryProductViewSet, basename="inventory-product")
router.register("batches", InventoryBatchViewSet, basename="inventory-batch")
router.register("containers", InventoryContainerViewSet, basename="inventory-container")
router.register("outbounds", InventoryOutboundViewSet, basename="inventory-outbound")
router.register("transactions", InventoryTransactionViewSet, basename="inventory-transaction")
router.register("material-remainders", MaterialRemainderViewSet, basename="material-remainder")


urlpatterns = [
    path("summary/", InventorySummaryView.as_view(), name="inventory-summary"),
    path("availability/", InventoryAvailabilityView.as_view(), name="inventory-availability"),
    path("receipts/", InventoryReceiptView.as_view(), name="inventory-receipt"),
    path("", include(router.urls)),
]

