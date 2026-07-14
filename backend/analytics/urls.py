from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    AnalyticsDashboardView,
    ManualFinancialEntryViewSet,
    ManualPerformanceEntryViewSet,
)


router = DefaultRouter()
router.register(
    "manual-entries", ManualPerformanceEntryViewSet, basename="manual-performance-entry"
)
router.register(
    "financial-entries", ManualFinancialEntryViewSet, basename="manual-financial-entry"
)


urlpatterns = [
    path("dashboard/", AnalyticsDashboardView.as_view(), name="analytics-dashboard"),
    path("", include(router.urls)),
]
