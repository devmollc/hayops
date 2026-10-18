import pytest
from django.test import RequestFactory
from django.urls import reverse
from rest_framework.test import APIClient

from apps.core.ratelimit import client_ip
from apps.widget_api.views import ANON_EMAIL_LIMIT


def test_client_ip_ignores_proxy_headers_unless_configured(settings):
    request = RequestFactory().get("/", HTTP_CF_CONNECTING_IP="1.2.3.4", HTTP_X_FORWARDED_FOR="5.6.7.8")
    settings.CLIENT_IP_HEADER = ""
    assert client_ip(request) == "127.0.0.1"
    settings.CLIENT_IP_HEADER = "HTTP_CF_CONNECTING_IP"
    assert client_ip(request) == "1.2.3.4"


@pytest.mark.django_db
def test_widget_caps_anonymous_tickets_per_submitter_address(source_app):
    client = APIClient()

    def submit(ip):
        # A fresh "client IP" every time: the per-address cap must hold regardless.
        return client.post(
            reverse("widget-ticket-list"),
            {"subject": "s", "body": "b", "submitter_email": "Victim@Example.com"},
            format="multipart",
            HTTP_X_APP_KEY=source_app.api_key,
            REMOTE_ADDR=ip,
        )

    for i in range(ANON_EMAIL_LIMIT):
        assert submit(f"10.0.0.{i}").status_code == 201
    assert submit("10.0.1.1").status_code == 429
