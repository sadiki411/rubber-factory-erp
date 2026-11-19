from django.urls import include, path
from rest_framework.routers import DefaultRouter
from .views import (ContextView, StandardView, GroupViewSet, CardViewSet, EventView,
                    PrintViewSet, TemplateView, AttachmentView, AttachmentContentView,
                    DiagramView, ShipmentSourcesView, AppearanceView, ShipmentSourceReviewView, TemplatePreviewView, StandardActivationView, PreparationSourcesView, PreparationSourceFileView)

router = DefaultRouter()
router.register("groups", GroupViewSet, basename="flow-group")
router.register("cards", CardViewSet, basename="flow-card")
router.register("print-jobs", PrintViewSet, basename="flow-print")
urlpatterns = [
    path("context/", ContextView.as_view()), path("standards/", StandardView.as_view()),
    path("standards/<int:pk>/activate/", StandardActivationView.as_view()),
    path("preparation-sources/", PreparationSourcesView.as_view()),
    path("source-files/<uuid:pk>/", PreparationSourceFileView.as_view()),
    path("appearance/", AppearanceView.as_view()),
    path("events/", EventView.as_view()), path("templates/publish/", TemplateView.as_view()),
    path("templates/preview/", TemplatePreviewView.as_view()),
    path("attachments/", AttachmentView.as_view()), path("attachments/<uuid:pk>/content/", AttachmentContentView.as_view()),
    path("diagrams/", DiagramView.as_view()), path("shipments/<int:pk>/sources/", ShipmentSourcesView.as_view()),
    path("shipment-sources/<int:pk>/review/", ShipmentSourceReviewView.as_view()),
    path("", include(router.urls)),
]
