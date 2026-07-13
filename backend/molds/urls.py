from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    HealthView,
    ImportCommitView,
    ImportErrorReportView,
    ImportPreviewView,
    ImportTemplateView,
    LoginView,
    LogoutView,
    MachineViewSet,
    MoldModelViewSet,
    MoldViewSet,
    ProcessorViewSet,
    RackViewSet,
    SessionView,
    SlotViewSet,
)


router = DefaultRouter()
router.register("molds", MoldViewSet, basename="mold")
router.register("racks", RackViewSet, basename="rack")
router.register("slots", SlotViewSet, basename="slot")
router.register("mold-models", MoldModelViewSet, basename="mold-model")
router.register("machines", MachineViewSet, basename="machine")
router.register("processors", ProcessorViewSet, basename="processor")


urlpatterns = [
    path("health/", HealthView.as_view(), name="health"),
    path("auth/session/", SessionView.as_view(), name="auth-session"),
    path("auth/login/", LoginView.as_view(), name="auth-login"),
    path("auth/logout/", LogoutView.as_view(), name="auth-logout"),
    path("imports/template/", ImportTemplateView.as_view(), name="import-template"),
    path("imports/preview/", ImportPreviewView.as_view(), name="import-preview"),
    path("imports/commit/", ImportCommitView.as_view(), name="import-commit"),
    path(
        "imports/<uuid:token>/errors/",
        ImportErrorReportView.as_view(),
        name="import-error-report",
    ),
    path("", include(router.urls)),
]
