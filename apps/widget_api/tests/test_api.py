import hashlib
import hmac
import json
import re

import pytest
from django.conf import settings
from django.urls import reverse
from rest_framework.test import APIClient

from apps.tickets.models import Ticket, TicketParticipant


def hmac_headers(app, email, name="Tester"):
    user_hash = hmac.new(app.api_secret.encode(), email.encode(), hashlib.sha256).hexdigest()
    return {
        "HTTP_X_APP_KEY": app.api_key,
        "HTTP_X_USER_EMAIL": email,
        "HTTP_X_USER_NAME": name,
        "HTTP_X_USER_HASH": user_hash,
    }


@pytest.fixture
def client():
    return APIClient()


@pytest.mark.django_db
def test_hmac_auth_valid_hash_lists_tickets(client, source_app, ticket):
    headers = hmac_headers(source_app, "customer@example.com")
    res = client.get(reverse("widget-ticket-list"), **headers)
    assert res.status_code == 200
    assert len(res.data) == 1
    assert res.data[0]["id"] == ticket.id


@pytest.mark.django_db
def test_hmac_auth_tampered_hash_rejected(client, source_app):
    headers = hmac_headers(source_app, "customer@example.com")
    headers["HTTP_X_USER_HASH"] = "0" * 64
    res = client.get(reverse("widget-ticket-list"), **headers)
    assert res.status_code in (401, 403)


@pytest.mark.django_db
def test_app_key_only_list_returns_empty_not_500(client, source_app, ticket):
    res = client.get(reverse("widget-ticket-list"), HTTP_X_APP_KEY=source_app.api_key)
    assert res.status_code == 200
    assert res.data == []


@pytest.mark.django_db
def test_app_key_only_create_stores_context(client, source_app):
    context = {"viewport": "1920x1080", "app_context": {"plan": "pro"}}
    res = client.post(
        reverse("widget-ticket-list"),
        {
            "subject": "Anonymous ticket",
            "body": "Help please",
            "submitter_email": "anon@example.com",
            "submitter_name": "Anon",
            "context": json.dumps(context),
        },
        format="multipart",
        HTTP_X_APP_KEY=source_app.api_key,
    )
    assert res.status_code == 201

    ticket_obj = Ticket.objects.get(id=res.data["id"])
    assert ticket_obj.source_application_id == source_app.id
    assert ticket_obj.custom_fields["widget_context"] == context

    participant = TicketParticipant.objects.get(ticket=ticket_obj, email="anon@example.com")
    assert participant.role == TicketParticipant.Role.SUBMITTER


@pytest.mark.django_db
def test_invalid_context_json_is_dropped_ticket_still_created(client, source_app):
    res = client.post(
        reverse("widget-ticket-list"),
        {
            "subject": "Bad context",
            "body": "Help please",
            "submitter_email": "anon2@example.com",
            "context": "{not valid json",
        },
        format="multipart",
        HTTP_X_APP_KEY=source_app.api_key,
    )
    assert res.status_code == 201
    ticket_obj = Ticket.objects.get(id=res.data["id"])
    assert "widget_context" not in ticket_obj.custom_fields


@pytest.mark.django_db
def test_oversized_context_is_dropped_ticket_still_created(client, source_app):
    oversized = json.dumps({"blob": "x" * 9000})
    res = client.post(
        reverse("widget-ticket-list"),
        {
            "subject": "Oversized context",
            "body": "Help please",
            "submitter_email": "anon3@example.com",
            "context": oversized,
        },
        format="multipart",
        HTTP_X_APP_KEY=source_app.api_key,
    )
    assert res.status_code == 201
    ticket_obj = Ticket.objects.get(id=res.data["id"])
    assert "widget_context" not in ticket_obj.custom_fields


@pytest.mark.django_db
def test_unsafe_page_url_scheme_is_dropped_ticket_still_created(client, source_app):
    res = client.post(
        reverse("widget-ticket-list"),
        {
            "subject": "XSS attempt",
            "body": "Help please",
            "submitter_email": "anon4@example.com",
            "page_url": "javascript:alert(1)",
        },
        format="multipart",
        HTTP_X_APP_KEY=source_app.api_key,
    )
    assert res.status_code == 201
    ticket_obj = Ticket.objects.get(id=res.data["id"])
    assert "page_url" not in ticket_obj.custom_fields


@pytest.mark.django_db
def test_unsafe_context_referrer_scheme_is_dropped_rest_of_context_kept(client, source_app):
    context = {"viewport": "1920x1080", "referrer": "javascript:alert(1)"}
    res = client.post(
        reverse("widget-ticket-list"),
        {
            "subject": "XSS attempt via referrer",
            "body": "Help please",
            "submitter_email": "anon5@example.com",
            "context": json.dumps(context),
        },
        format="multipart",
        HTTP_X_APP_KEY=source_app.api_key,
    )
    assert res.status_code == 201
    ticket_obj = Ticket.objects.get(id=res.data["id"])
    stored = ticket_obj.custom_fields["widget_context"]
    assert "referrer" not in stored
    assert stored["viewport"] == "1920x1080"


@pytest.mark.django_db
def test_config_endpoint_valid_key(client, source_app):
    res = client.get(reverse("widget-config"), {"key": source_app.api_key})
    assert res.status_code == 200
    assert res.data["product_name"] == source_app.branding_name
    assert res.data["primary_color"] == source_app.primary_color
    assert res.data["logo_url"] is None


@pytest.mark.django_db
def test_config_endpoint_bad_key(client):
    res = client.get(reverse("widget-config"), {"key": "not-a-real-key"})
    assert res.status_code == 403


@pytest.mark.django_db
def test_widget_reads_the_date_fields_the_api_actually_returns(client, source_app):
    """The widget rendered "Created Invalid Date" because it read `ticket.created`
    while the API returns `created_at`. new Date(undefined) doesn't throw, so the
    typo reached the panel as text. Pin the contract from both ends."""
    res = client.post(
        reverse("widget-ticket-list"),
        {"subject": "Date fields", "body": "b", "submitter_email": "c@example.com"},
        HTTP_X_APP_KEY=source_app.api_key,
    )
    assert res.status_code == 201
    assert "created_at" in res.data

    listed = client.get(reverse("widget-ticket-list"), **hmac_headers(source_app, "c@example.com"))
    assert {"created_at", "updated_at"} <= set(listed.data[0])

    # Every value the widget hands to formatDate must be one of those keys.
    source = (settings.BASE_DIR / "widget" / "src" / "widget.js").read_text()
    read_fields = set(re.findall(r"this\.formatDate\(\s*\w+\.(\w+)\s*\)", source))
    assert read_fields, "formatDate calls not found -- update this test with the widget"
    assert read_fields <= {"created_at", "updated_at"}, f"widget reads unknown date fields: {read_fields}"


@pytest.fixture
def other_app(db):
    from apps.core.models import SourceApplication

    return SourceApplication.objects.create(
        name="GxPSign", slug="gxpsign-test-other", product_name="GxPSign",
        portal_domain="support.gxpsign.app", mail_domain="mail.gxpsign.app",
    )


def _ticket_for(app, email, subject):
    ticket = Ticket.objects.create(subject=subject, body="b", source_application=app)
    TicketParticipant.objects.create(ticket=ticket, email=email, role=TicketParticipant.Role.SUBMITTER)
    return ticket


@pytest.mark.django_db
def test_list_shows_only_this_applications_tickets(client, source_app, other_app):
    mine = _ticket_for(source_app, "customer@example.com", "AppsFolio issue")
    theirs = _ticket_for(other_app, "customer@example.com", "GxPSign issue")

    res = client.get(reverse("widget-ticket-list"), **hmac_headers(source_app, "customer@example.com"))

    assert res.status_code == 200
    ids = {row["id"] for row in res.data}
    assert mine.id in ids
    assert theirs.id not in ids


@pytest.mark.django_db
def test_another_applications_ticket_cannot_be_read_or_replied_to_by_id(client, source_app, other_app):
    """The list is cosmetic; these two take an id, so this is the real boundary."""
    theirs = _ticket_for(other_app, "customer@example.com", "GxPSign issue")
    headers = hmac_headers(source_app, "customer@example.com")

    assert client.get(reverse("widget-ticket-detail", args=[theirs.id]), **headers).status_code == 404
    reply = client.post(
        reverse("widget-ticket-reply", args=[theirs.id]), {"body": "hello"}, format="json", **headers
    )
    assert reply.status_code == 404
    assert theirs.comments.count() == 0


@pytest.mark.django_db
def test_a_ticket_with_no_application_stays_visible(client, source_app):
    """Unattributed tickets are legacy and belong to no product; scoping them out
    would hide them from every widget as well as the portal."""
    orphan = _ticket_for(None, "customer@example.com", "Filed by an agent")

    res = client.get(reverse("widget-ticket-list"), **hmac_headers(source_app, "customer@example.com"))

    assert orphan.id in {row["id"] for row in res.data}


# ── finding 15: the widget must not rename accounts ──────────────────────────


def _anon_submit(client, source_app, email, name):
    return client.post(
        reverse("widget-ticket-list"),
        {
            "subject": "Anonymous ticket",
            "body": "Help please",
            "submitter_email": email,
            "submitter_name": name,
        },
        format="multipart",
        HTTP_X_APP_KEY=source_app.api_key,
    )


@pytest.mark.django_db
def test_anonymous_submission_cannot_rename_an_existing_user(client, source_app, staff_user):
    """The app key is public, so submitter_name is an attacker-controlled string."""
    assert _anon_submit(client, source_app, staff_user.email, "Totally Legit Support").status_code == 201

    staff_user.refresh_from_db()
    assert staff_user.first_name == ""
    assert Ticket.objects.get().custom_fields["submitter_name"] == "Totally Legit Support"


@pytest.mark.django_db
def test_a_brand_new_submitter_still_gets_their_name(client, source_app):
    from apps.accounts.models import User

    assert _anon_submit(client, source_app, "fresh@example.com", "Fresh Face").status_code == 201
    assert User.objects.get(email="fresh@example.com").first_name == "Fresh Face"
