from django.urls import path

from . import views

app_name = "inbound_email"

urlpatterns = [
    path("inbound/", views.SESInboundWebhookView.as_view(), name="ses_inbound"),
]
