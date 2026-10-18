"""A bounced address is flagged wherever it appears, so agents stop replying into the void."""

import pytest

from apps.core.models import BouncedAddress
from apps.tickets.models import Ticket, TicketParticipant

pytestmark = pytest.mark.django_db


def test_bouncing_participant_is_flagged_on_the_staff_detail_page(client, staff_user, ticket):
    BouncedAddress.objects.create(email="customer@example.com", status="5.1.1")
    client.force_login(staff_user)

    resp = client.get(f"/tickets/{ticket.reference_number}/", HTTP_HOST="testserver")

    assert resp.status_code == 200
    assert "Bouncing" in resp.content.decode()


def test_reachable_participant_is_not_flagged(client, staff_user, ticket):
    client.force_login(staff_user)

    resp = client.get(f"/tickets/{ticket.reference_number}/", HTTP_HOST="testserver")

    assert "Bouncing" not in resp.content.decode()


def test_flag_applies_to_tickets_opened_after_the_bounce(client, staff_user, source_app):
    """The flag lives on the address, so it is not stuck on whichever ticket bounced first."""
    BouncedAddress.objects.create(email="customer@example.com", status="5.1.1")
    later = Ticket.objects.create(subject="New problem", body="...", source_application=source_app)
    TicketParticipant.objects.create(ticket=later, email="Customer@Example.com", role=TicketParticipant.Role.SUBMITTER)
    client.force_login(staff_user)

    resp = client.get(f"/tickets/{later.reference_number}/", HTTP_HOST="testserver")

    assert "Bouncing" in resp.content.decode()
