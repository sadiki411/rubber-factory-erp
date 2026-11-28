from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .supplement_views import (
    SupplementConfigView, SupplementDeliveryReleaseView, SupplementDetailView, SupplementIssueView, SupplementListView,
    SupplementLookupView, SupplementOrdersView, SupplementSearchView,
)

from .views import (
    DefectReasonViewSet,
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
router.register("defect-reasons", DefectReasonViewSet, basename="quality-defect-reason")
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
    path("supplements/config/", SupplementConfigView.as_view(), name="quality-supplement-config"),
    path("supplements/", SupplementListView.as_view(), name="quality-supplements"),
    path("supplements/origins/", SupplementLookupView.as_view(), name="quality-supplement-origins"),
    path("supplements/preflight/", SupplementSearchView.as_view(), name="quality-supplement-preflight"),
    path("supplements/issues/", SupplementIssueView.as_view(), name="quality-supplement-issues"),
    path("supplements/issues/<int:pk>/", SupplementIssueView.as_view(), name="quality-supplement-issue"),
    path("supplements/issues/<int:pk>/delivery-release/", SupplementDeliveryReleaseView.as_view(), name="quality-supplement-delivery-release"),
    path("supplements/<int:pk>/", SupplementDetailView.as_view(), name="quality-supplement-detail"),
    path("supplements/<int:pk>/orders/", SupplementOrdersView.as_view(), name="quality-supplement-orders"),
    path("supplements/<int:pk>/<str:operation>/", SupplementDetailView.as_view(), name="quality-supplement-operation"),
    path("summary/", QualitySummaryView.as_view(), name="quality-summary"),
    path("shipment-ledger/", QualityShipmentLedgerView.as_view(), name="quality-shipment-ledger"),
    # Canonical candidate endpoint plus the two additive aliases used by
    # older/mobile clients during rollout.
    path("shipping-candidates/", QualityShippingCandidatesView.as_view(), name="quality-shipping-candidates"),
    path("shipments/candidates/", QualityShippingCandidatesView.as_view(), name="quality-legacy-shipping-candidates"),
    path("shipment-batches/shipping-candidates/", QualityShippingCandidatesView.as_view(), name="quality-shipment-batch-shipping-candidates"),
    path("", include(router.urls)),
]
