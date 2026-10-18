import pytest
from django.urls import reverse

from apps.accounts.models import User
from apps.tickets.models import TicketEvent

pytestmark = pytest.mark.django_db


@pytest.fixture
def customer_user(db):
    return User.objects.create_user(email="customer2@example.com", password="x", is_staff=False)


def _htmx_get(client, url, **params):
    return client.get(url, params, HTTP_HX_REQUEST="true")


# --- Queue poll -------------------------------------------------------------


def test_queue_poll_204_when_last_event_matches_cache(client, staff_user, ticket):
    client.force_login(staff_user)
    event = TicketEvent.objects.create(ticket=ticket, event_type=TicketEvent.EventType.CREATED)
    resp = _htmx_get(client, reverse("tickets:queue"), last_event=str(event.pk))
    assert resp.status_code == 204


def test_queue_poll_200_when_stale(client, staff_user, ticket):
    client.force_login(staff_user)
    TicketEvent.objects.create(ticket=ticket, event_type=TicketEvent.EventType.CREATED)
    resp = _htmx_get(client, reverse("tickets:queue"), last_event="0")
    assert resp.status_code == 200


# --- Timeline poll ------------------------------------------------------------


def test_timeline_poll_204_when_marker_matches(client, staff_user, ticket):
    client.force_login(staff_user)
    event = TicketEvent.objects.create(ticket=ticket, event_type=TicketEvent.EventType.COMMENT_ADDED)
    url = reverse("tickets:timeline_poll", args=[ticket.pk])
    resp = _htmx_get(client, url, last_event=str(event.pk))
    assert resp.status_code == 204


def test_timeline_poll_200_when_stale(client, staff_user, ticket):
    client.force_login(staff_user)
    TicketEvent.objects.create(ticket=ticket, event_type=TicketEvent.EventType.COMMENT_ADDED)
    url = reverse("tickets:timeline_poll", args=[ticket.pk])
    resp = _htmx_get(client, url, last_event="0")
    assert resp.status_code == 200


def test_timeline_poll_non_participant_non_staff_denied(client, ticket, customer_user):
    client.force_login(customer_user)
    url = reverse("tickets:timeline_poll", args=[ticket.pk])
    resp = client.get(url)
    assert resp.status_code in (403, 404)


# --- Activity endpoint (sound alerts) ----------------------------------------


def test_activity_staff_only(client, customer_user):
    client.force_login(customer_user)
    resp = client.get(reverse("tickets:activity"))
    assert resp.status_code == 403


def test_activity_counts_new_tickets_and_customer_replies(client, staff_user, ticket):
    client.force_login(staff_user)
    TicketEvent.objects.create(ticket=ticket, event_type=TicketEvent.EventType.CREATED)
    TicketEvent.objects.create(ticket=ticket, event_type=TicketEvent.EventType.COMMENT_ADDED, actor=None)
    TicketEvent.objects.create(ticket=ticket, event_type=TicketEvent.EventType.COMMENT_ADDED, actor=staff_user)

    resp = client.get(reverse("tickets:activity"), {"after": 0})
    assert resp.status_code == 200
    data = resp.json()
    assert data["new_tickets"] == 1
    assert data["customer_replies"] == 1
    assert data["latest"] > 0


def test_activity_short_circuits_when_after_matches_cache(client, staff_user, ticket):
    client.force_login(staff_user)
    event = TicketEvent.objects.create(ticket=ticket, event_type=TicketEvent.EventType.CREATED)
    resp = client.get(reverse("tickets:activity"), {"after": event.pk})
    assert resp.status_code == 200
    assert resp.json() == {"latest": event.pk}


# --- Comment form bug fix -----------------------------------------------------


def test_invalid_comment_post_rerenders_with_errors_not_500(client, staff_user, ticket):
    client.force_login(staff_user)
    url = reverse("tickets:add_comment", args=[ticket.pk])
    resp = client.post(url, {"body": "", "comment_type": "public"}, HTTP_HX_REQUEST="true")
    assert resp.status_code == 200
    assert b"required" in resp.content.lower()


# --- Sidebar context bug fix --------------------------------------------------


def test_htmx_status_change_sidebar_includes_agents(client, staff_user, ticket):
    client.force_login(staff_user)
    url = reverse("tickets:change_status", args=[ticket.pk])
    resp = client.post(url, {"status": "open"}, HTTP_HX_REQUEST="true")
    assert resp.status_code == 200
    assert staff_user.email.encode() in resp.content


# --- Sidebar Context XSS guard ------------------------------------------------


def test_sidebar_page_url_javascript_scheme_not_rendered_as_link(client, staff_user, ticket):
    ticket.custom_fields = {"page_url": "javascript:alert(1)"}
    ticket.save(update_fields=["custom_fields"])
    client.force_login(staff_user)
    resp = client.get(reverse("tickets:detail", args=[ticket.reference_number]))
    assert resp.status_code == 200
    assert b'href="javascript:alert(1)"' not in resp.content


def test_sidebar_page_url_https_rendered_as_link(client, staff_user, ticket):
    ticket.custom_fields = {"page_url": "https://example.com/page"}
    ticket.save(update_fields=["custom_fields"])
    client.force_login(staff_user)
    resp = client.get(reverse("tickets:detail", args=[ticket.reference_number]))
    assert resp.status_code == 200
    assert b'href="https://example.com/page"' in resp.content


def test_claim_assigns_ticket_and_returns_row(client, staff_user, ticket):
    """Regression: select_for_update needs an explicit transaction or this 500s."""
    client.force_login(staff_user)
    response = client.post(f"/tickets/{ticket.pk}/claim/", HTTP_HX_REQUEST="true")
    assert response.status_code == 200
    ticket.refresh_from_db()
    assert ticket.assigned_to == staff_user


def test_claim_twice_does_not_steal(client, staff_user, ticket, django_user_model):
    other = django_user_model.objects.create_user(email="second@devmosolutions.com", password="x", is_staff=True)
    client.force_login(staff_user)
    client.post(f"/tickets/{ticket.pk}/claim/")
    client.force_login(other)
    client.post(f"/tickets/{ticket.pk}/claim/")
    ticket.refresh_from_db()
    assert ticket.assigned_to == staff_user


def test_queue_claim_button_is_not_inside_a_form(client, staff_user, ticket):
    """Regression (#11): a <form> around Claim is nested in the bulk-action form, so the
    browser drops it and the button submits the bulk form -> 'Unknown bulk action.'"""
    client.force_login(staff_user)
    row = client.get(reverse("tickets:queue")).content.decode().split(f'id="ticket-row-{ticket.pk}"')[1]
    assert "<form" not in row.split("</tr>")[0]
