from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    BusinessImportCommitView,
    BusinessImportErrorReportView,
    BusinessImportPreviewView,
    BusinessImportTemplateView,
    BusinessOrderViewSet,
    MaterialReceiptViewSet,
    ProductInspectionCriterionViewSet,
    ProductSpecificationViewSet,
)


router = DefaultRouter()
router.register(
    "product-specifications",
    ProductSpecificationViewSet,
    basename="product-specification",
)
router.register("orders", BusinessOrderViewSet, basename="business-order")
router.register("material-receipts", MaterialReceiptViewSet, basename="material-receipt")
router.register(
    "inspection-criteria",
    ProductInspectionCriterionViewSet,
    basename="product-inspection-criterion",
)


urlpatterns = [
    path("imports/template/", BusinessImportTemplateView.as_view(), name="business-import-template"),
    path("imports/preview/", BusinessImportPreviewView.as_view(), name="business-import-preview"),
    path("imports/commit/", BusinessImportCommitView.as_view(), name="business-import-commit"),
    path(
        "imports/<uuid:token>/errors/",
        BusinessImportErrorReportView.as_view(),
        name="business-import-error-report",
    ),
    path("", include(router.urls)),
]
