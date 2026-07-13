from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    ProductionBoardView,
    ProductionImportCommitView,
    ProductionImportErrorReportView,
    ProductionImportPreviewView,
    ProductionImportTemplateView,
    ProductionMonthlyPerformanceView,
    ProductionRunViewSet,
    ProductionStationViewSet,
    ProductionSummaryView,
)


router = DefaultRouter()
router.register("stations", ProductionStationViewSet, basename="production-station")
router.register("runs", ProductionRunViewSet, basename="production-run")


urlpatterns = [
    path("board/", ProductionBoardView.as_view(), name="production-board"),
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
    path("", include(router.urls)),
]
