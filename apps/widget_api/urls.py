from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import WidgetConfigView, WidgetTicketViewSet

router = DefaultRouter()
router.register("tickets", WidgetTicketViewSet, basename="widget-ticket")

urlpatterns = [
    path("config/", WidgetConfigView.as_view(), name="widget-config"),
    *router.urls,
]
