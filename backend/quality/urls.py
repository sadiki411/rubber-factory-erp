from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    QualityEmployeeViewSet,
    QualityOrderViewSet,
    QualityShipmentViewSet,
    QualitySummaryView,
    ReturnReworkViewSet,
    ProductUnitWeightViewSet, ProcessCardViewSet, QualityShipmentBatchViewSet,
    QualityShipmentLineViewSet, QualityReworkCaseViewSet, QualityReworkAttemptViewSet,
    QualityShippingCandidatesView, QualityShipmentLedgerView,
)


router = DefaultRouter()
router.register("employees", QualityEmployeeViewSet, basename="quality-employee")
router.register("orders", QualityOrderViewSet, basename="quality-order")
router.register("shipments", QualityShipmentViewSet, basename="quality-shipment")
router.register("reworks", ReturnReworkViewSet, basename="quality-rework")
router.register("product-unit-weights", ProductUnitWeightViewSet, basename="quality-product-unit-weight")
router.register("unit-weights", ProductUnitWeightViewSet, basename="quality-unit-weight")
router.register("process-cards", ProcessCardViewSet, basename="quality-process-card")
router.register("shipment-batches", QualityShipmentBatchViewSet, basename="quality-shipment-batch")
router.register("shipment-lines", QualityShipmentLineViewSet, basename="quality-shipment-line")
router.register("rework-cases", QualityReworkCaseViewSet, basename="quality-rework-case")
router.register("rework-attempts", QualityReworkAttemptViewSet, basename="quality-rework-attempt")


urlpatterns = [
    path("summary/", QualitySummaryView.as_view(), name="quality-summary"),
    path("shipment-ledger/", QualityShipmentLedgerView.as_view(), name="quality-shipment-ledger"),
    # Canonical candidate endpoint plus the two additive aliases used by
    # older/mobile clients during rollout.
    path("shipping-candidates/", QualityShippingCandidatesView.as_view(), name="quality-shipping-candidates"),
    path("shipments/candidates/", QualityShippingCandidatesView.as_view(), name="quality-legacy-shipping-candidates"),
    path("shipment-batches/shipping-candidates/", QualityShippingCandidatesView.as_view(), name="quality-shipment-batch-shipping-candidates"),
    path("", include(router.urls)),
]
