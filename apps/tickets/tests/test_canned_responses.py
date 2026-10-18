"""Agents manage canned responses themselves, from a menu item rather than the reply box."""

import pytest
from django.urls import reverse

from apps.tickets.models import CannedResponse, Queue

pytestmark = pytest.mark.django_db

CREATE_URL = reverse("tickets:canned_create")


def _payload(**overrides):
    return {"title": "Refund policy", "body": "We refund within 30 days.", "queue": "", "shortcut": "", **overrides}


def test_staff_can_create_one(client, staff_user):
    client.force_login(staff_user)

    resp = client.post(CREATE_URL, _payload())

    assert resp.status_code == 302
    canned = CannedResponse.objects.get()
    assert canned.title == "Refund policy"
    assert canned.created_by == staff_user
    # Blank queue is what makes it show up on every ticket, not just one queue.
    assert canned.queue is None


def test_non_staff_is_refused(client, django_user_model):
    customer = django_user_model.objects.create_user(email="customer@example.com", password="x")
    client.force_login(customer)

    resp = client.post(CREATE_URL, _payload())

    assert resp.status_code == 403
    assert not CannedResponse.objects.exists()


def test_it_returns_to_the_ticket_it_was_written_from(client, staff_user, ticket):
    client.force_login(staff_user)
    back = reverse("tickets:detail", kwargs={"reference": ticket.reference_number})

    resp = client.post(CREATE_URL, _payload(next=back))

    assert resp.url == back


def test_an_off_site_next_is_ignored(client, staff_user):
    client.force_login(staff_user)

    resp = client.post(CREATE_URL, _payload(next="https://evil.example.com/"))

    assert resp.url == reverse("tickets:queue")


def test_the_queue_dropdown_hides_retired_queues(client, staff_user):
    Queue.objects.create(name="Old", slug="old", is_active=False)
    live = Queue.objects.create(name="Billing", slug="billing")
    client.force_login(staff_user)

    resp = client.get(CREATE_URL)

    assert list(resp.context["form"].fields["queue"].queryset) == [live]


def test_the_list_ranks_by_use_so_the_dead_weight_sinks(client, staff_user):
    CannedResponse.objects.create(title="Rarely used", body="...", usage_count=1)
    CannedResponse.objects.create(title="Everyday", body="...", usage_count=42)
    client.force_login(staff_user)

    resp = client.get(reverse("tickets:canned_list"))

    assert [c.title for c in resp.context["canned_responses"]] == ["Everyday", "Rarely used"]


def test_editing_keeps_the_original_author(client, staff_user, django_user_model):
    author = django_user_model.objects.create_user(email="first@example.com", password="x", is_staff=True)
    canned = CannedResponse.objects.create(title="Refunds", body="Old text.", created_by=author)
    client.force_login(staff_user)

    resp = client.post(reverse("tickets:canned_edit", args=[canned.pk]), _payload(title="Refunds", body="New text."))

    assert resp.status_code == 302
    canned.refresh_from_db()
    assert canned.body == "New text."
    assert canned.created_by == author
    assert CannedResponse.objects.count() == 1


def test_delete_asks_first_and_then_removes_it(client, staff_user):
    canned = CannedResponse.objects.create(title="Obsolete", body="...")
    url = reverse("tickets:canned_delete", args=[canned.pk])
    client.force_login(staff_user)

    assert client.get(url).status_code == 200
    assert CannedResponse.objects.exists()

    resp = client.post(url)

    assert resp.url == reverse("tickets:canned_list")
    assert not CannedResponse.objects.exists()


@pytest.mark.parametrize("name,args", [("canned_list", []), ("canned_edit", [1]), ("canned_delete", [1])])
def test_the_management_pages_are_staff_only(client, django_user_model, name, args):
    customer = django_user_model.objects.create_user(email="nosy@example.com", password="x")
    client.force_login(customer)

    assert client.get(reverse(f"tickets:{name}", args=args)).status_code == 403


def test_the_reply_box_no_longer_offers_a_new_response_form(client, staff_user, ticket):
    """Writing one is a menu item now; the link used to sit beside the insert buttons."""
    client.force_login(staff_user)

    body = client.get(
        reverse("tickets:detail", kwargs={"reference": ticket.reference_number}), HTTP_HOST="testserver"
    ).content.decode()

    assert "Insert canned response" in body
    assert CREATE_URL not in body
