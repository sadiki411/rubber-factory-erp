from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    QualityEmployeeViewSet,
    QualityOrderViewSet,
    QualityShipmentViewSet,
    QualitySummaryView,
    ReturnReworkViewSet,
)


router = DefaultRouter()
router.register("employees", QualityEmployeeViewSet, basename="quality-employee")
router.register("orders", QualityOrderViewSet, basename="quality-order")
router.register("shipments", QualityShipmentViewSet, basename="quality-shipment")
router.register("reworks", ReturnReworkViewSet, basename="quality-rework")


urlpatterns = [
    path("summary/", QualitySummaryView.as_view(), name="quality-summary"),
    path("", include(router.urls)),
]
