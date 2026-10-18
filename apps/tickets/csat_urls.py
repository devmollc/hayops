from django.urls import path

from .csat_views import rate_ticket

urlpatterns = [
    path("csat/<str:token>/", rate_ticket, name="csat_rate"),
    path("csat/<str:token>/<int:rating>/", rate_ticket, name="csat_rate_direct"),
]
