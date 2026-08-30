from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    ProductionBoardView,
    ProductionImportCommitView,
    ProductionImportErrorReportView,
    ProductionImportPreviewView,
    ProductionImportTemplateView,
    ProductionLedgerImportCommitView,
    ProductionLedgerImportPreviewView,
    ProductionLedgerImportTemplateView,
    ProductionPhotoOcrPreviewView,
    ProductionMonthlyPerformanceView,
    ProductionOrderProgressView,
    ProductionRunViewSet,
    ProductionEmployeeViewSet,
    ProductionStationViewSet,
    ProductionSummaryView,
)


router = DefaultRouter()
router.register("stations", ProductionStationViewSet, basename="production-station")
router.register("runs", ProductionRunViewSet, basename="production-run")
router.register("employees", ProductionEmployeeViewSet, basename="production-employee")


urlpatterns = [
    path("board/", ProductionBoardView.as_view(), name="production-board"),
    path(
        "order-progress/",
        ProductionOrderProgressView.as_view(),
        name="production-order-progress",
    ),
    path("summary/", ProductionSummaryView.as_view(), name="production-summary"),
    path(
        "performance/monthly/",
        ProductionMonthlyPerformanceView.as_view(),
        name="production-monthly-performance",
    ),
    path(
        "imports/template/",
        ProductionImportTemplateView.as_view(),
        name="production-import-template",
    ),
    path(
        "imports/preview/",
        ProductionImportPreviewView.as_view(),
        name="production-import-preview",
    ),
    path(
        "imports/commit/",
        ProductionImportCommitView.as_view(),
        name="production-import-commit",
    ),
    path(
        "imports/<uuid:token>/errors/",
        ProductionImportErrorReportView.as_view(),
        name="production-import-error-report",
    ),
    path(
        "ledger-imports/template/",
        ProductionLedgerImportTemplateView.as_view(),
        name="production-ledger-import-template",
    ),
    path(
        "ledger-imports/preview/",
        ProductionLedgerImportPreviewView.as_view(),
        name="production-ledger-import-preview",
    ),
    path(
        "ledger-imports/commit/",
        ProductionLedgerImportCommitView.as_view(),
        name="production-ledger-import-commit",
    ),
    path(
        "ocr/preview/",
        ProductionPhotoOcrPreviewView.as_view(),
        name="production-photo-ocr-preview",
    ),
    path("", include(router.urls)),
]
