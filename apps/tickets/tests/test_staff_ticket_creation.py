"""Tickets an agent opens on a customer's behalf must still reach the customer."""

import pytest
from django.core import mail
from django.urls import reverse

from apps.inbound_email.tasks import send_ticket_notification
from apps.tickets.models import Ticket, TicketEvent, TicketParticipant

pytestmark = pytest.mark.django_db

CREATE_URL = reverse("tickets:create")


def _post(client, source_app, **overrides):
    payload = {
        "subject": "Printer jams on duplex",
        "body": "Reported over the phone.",
        "submitter_email": "customer@example.com",
        "priority": Ticket.Priority.NORMAL,
        "source_application": source_app.pk,
        **overrides,
    }
    resp = client.post(CREATE_URL, payload)
    assert resp.status_code == 302, resp.status_code
    return Ticket.objects.latest("created_at")


def test_agent_created_ticket_emails_the_customer(client, staff_user, source_app):
    client.force_login(staff_user)
    ticket = _post(client, source_app)
    mail.outbox.clear()

    send_ticket_notification(ticket_id=ticket.pk, notification_type="created")

    assert [address for sent in mail.outbox for address in sent.to] == ["customer@example.com"]


def test_agent_is_not_recorded_as_the_submitter(client, staff_user, source_app):
    client.force_login(staff_user)
    ticket = _post(client, source_app)

    submitter = ticket.participants.get(role=TicketParticipant.Role.SUBMITTER)
    assert submitter.email == "customer@example.com"
    # Attaching the agent's user here is what made the ticket look staff-only and
    # silently dropped the "we received your request" mail.
    assert submitter.user is None


def test_submitter_user_is_linked_when_the_customer_already_has_an_account(client, staff_user, source_app):
    from apps.accounts.models import User

    customer = User.objects.create_user(email="customer@example.com", password="x")
    client.force_login(staff_user)
    ticket = _post(client, source_app)

    submitter = ticket.participants.get(role=TicketParticipant.Role.SUBMITTER)
    assert submitter.user == customer
    assert not submitter.user.is_staff


def test_created_event_records_the_agent_who_filed_it(client, staff_user, source_app):
    client.force_login(staff_user)
    ticket = _post(client, source_app)

    event = ticket.events.get(event_type=TicketEvent.EventType.CREATED)
    assert event.actor == staff_user


def test_customer_sees_the_ticket_after_signing_in(client, staff_user, source_app):
    """The portal list filters on TicketParticipant.user.

    link_participants() only claims rows where that FK is NULL, so recording the agent
    there did not just skip the email -- it permanently hid the ticket from the customer,
    because there was nothing left for their first sign-in to claim.
    """
    from apps.accounts.services import link_participants

    client.force_login(staff_user)
    ticket = _post(client, source_app)

    from apps.accounts.models import User

    customer = User.objects.create_user(email="customer@example.com", password="x")
    link_participants(customer)

    visible = Ticket.objects.filter(participants__user=customer).distinct()
    assert list(visible) == [ticket]


def test_blank_submitter_email_falls_back_to_the_agent(client, staff_user, source_app):
    client.force_login(staff_user)
    ticket = _post(client, source_app, submitter_email="")

    submitter = ticket.participants.get(role=TicketParticipant.Role.SUBMITTER)
    # cleaned_data holds "" rather than omitting the key, so the fallback has to be
    # `or`, not a dict default -- otherwise the participant has no address at all.
    assert submitter.email == staff_user.email
    assert submitter.user == staff_user


def test_the_application_is_required(client, staff_user, source_app):
    """A ticket with no application is invisible on every branded portal."""
    client.force_login(staff_user)
    before = Ticket.objects.count()

    resp = client.post(
        CREATE_URL,
        {
            "subject": "Printer jams on duplex",
            "body": "Reported over the phone.",
            "submitter_email": "customer@example.com",
            "priority": Ticket.Priority.NORMAL,
            "source_application": "",
        },
    )

    assert resp.status_code == 200
    assert Ticket.objects.count() == before
    assert "source_application" in resp.context["form"].errors
