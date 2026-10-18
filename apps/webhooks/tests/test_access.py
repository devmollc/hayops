"""Finding 6: /webhooks/ was @login_required only, and any customer can get a
login on the admin host via a portal magic link."""

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from apps.webhooks.forms import WebhookEndpointForm
from apps.webhooks.models import WebhookDelivery, WebhookEndpoint, validate_public_destination

pytestmark = pytest.mark.django_db


@pytest.fixture
def customer(db):
    from apps.accounts.models import User

    return User.objects.create_user(email="customer@example.com", password="x", is_staff=False)


@pytest.fixture
def resolves_public(monkeypatch):
    """Make the destination check see a public address without needing real DNS."""
    monkeypatch.setattr(
        "socket.getaddrinfo",
        lambda host, port, **kw: [(2, 1, 6, "", ("93.184.216.34", port))],
    )


@pytest.fixture
def endpoint(source_app):
    return WebhookEndpoint.objects.create(
        source_application=source_app,
        url="https://hooks.example.com/helpdesk",
        events=["created"],
        secret="s3cr3t-signing-key",
    )


def _urls(endpoint):
    return [
        reverse("webhooks:endpoint_list"),
        reverse("webhooks:endpoint_create"),
        reverse("webhooks:endpoint_edit", args=[endpoint.pk]),
        reverse("webhooks:delivery_list", args=[endpoint.pk]),
        # Last: it is the one view that answers a GET with a redirect.
        reverse("webhooks:endpoint_delete", args=[endpoint.pk]),
    ]


def test_signed_in_customer_gets_403_on_every_webhook_view(client, customer, endpoint):
    client.force_login(customer)
    for url in _urls(endpoint):
        assert client.get(url).status_code == 403, url


def test_anonymous_is_redirected_to_login(client, endpoint):
    for url in _urls(endpoint):
        assert client.get(url).status_code == 302, url


def test_staff_can_still_reach_them(client, staff_user, endpoint):
    client.force_login(staff_user)
    *renders, delete_url = _urls(endpoint)
    for url in renders:
        assert client.get(url).status_code == 200, url
    assert client.get(delete_url).status_code == 302


def test_edit_page_does_not_render_the_signing_secret(client, staff_user, endpoint):
    client.force_login(staff_user)
    body = client.get(reverse("webhooks:endpoint_edit", args=[endpoint.pk])).content
    assert endpoint.secret.encode() not in body


def test_blank_secret_on_edit_keeps_the_existing_one(endpoint, resolves_public):
    form = WebhookEndpointForm(
        {
            "source_application": endpoint.source_application_id,
            "url": endpoint.url,
            "events": '["created"]',
            "secret": "",
            "is_active": True,
        },
        instance=endpoint,
    )
    assert form.is_valid(), form.errors
    assert form.save().secret == "s3cr3t-signing-key"


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/hook",
        "http://localhost/hook",
        "http://[::1]/hook",
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://10.0.0.5/hook",
        "http://helpdesk-prod-app:8000/hook",  # a Docker service name does not resolve
        "file:///etc/passwd",
        "gopher://example.com/",
    ],
)
def test_internal_and_non_http_destinations_are_rejected(url):
    with pytest.raises(ValidationError):
        validate_public_destination(url)


def test_a_public_destination_is_accepted(resolves_public):
    validate_public_destination("https://hooks.example.com/hook")


def test_the_form_refuses_an_internal_destination(source_app):
    form = WebhookEndpointForm(
        {
            "source_application": source_app.pk,
            "url": "http://169.254.169.254/latest/meta-data/",
            "events": '["created"]',
            "secret": "k",
            "is_active": True,
        }
    )
    assert not form.is_valid()
    assert "url" in form.errors


def test_delivery_refuses_a_row_that_already_points_inside(endpoint):
    """Rows written before the validation existed must not reach an internal host."""
    from apps.webhooks.tasks import deliver_webhook

    WebhookEndpoint.objects.filter(pk=endpoint.pk).update(url="http://169.254.169.254/latest/meta-data/")
    delivery = WebhookDelivery.objects.create(endpoint=endpoint, event_type="created", payload={})

    deliver_webhook(delivery_id=delivery.pk)

    delivery.refresh_from_db()
    assert not delivery.is_success
    assert delivery.delivered_at is None
    assert "non-public" in delivery.response_body


def test_delivery_does_not_follow_redirects():
    """A public endpoint answering 302 -> internal address must not be followed."""
    import threading
    import urllib.error
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from apps.webhooks.tasks import _opener

    class Redirect(BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(302)
            self.send_header("Location", "http://169.254.169.254/latest/meta-data/")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Redirect)
    threading.Thread(target=server.handle_request, daemon=True).start()
    with pytest.raises(urllib.error.HTTPError) as exc:
        _opener.open(f"http://127.0.0.1:{server.server_port}/", data=b"{}", timeout=5)
    server.server_close()
    assert exc.value.code == 302
