import pytest

from apps.accounts.models import User
from apps.core.models import SourceApplication
from apps.tickets.models import Queue, TicketParticipant


@pytest.mark.django_db
def test_submitter_can_edit_wording_but_not_routing(client, settings, ticket):
    customer = User.objects.create_user(email="customer@example.com", password="x", is_staff=False)
    TicketParticipant.objects.filter(ticket=ticket).update(user=customer)
    other_app = SourceApplication.objects.create(name="Other", slug="other")
    queue = Queue.objects.create(name="Billing", slug="billing")
    client.force_login(customer)

    client.post(
        f"/tickets/{ticket.pk}/edit/",
        {"subject": "New wording", "body": "b", "queue": queue.pk, "source_application": other_app.pk},
        HTTP_HOST=settings.ADMIN_DOMAIN,
    )

    ticket.refresh_from_db()
    assert ticket.subject == "New wording"
    assert ticket.queue_id is None
    assert ticket.source_application_id != other_app.pk


@pytest.mark.django_db
def test_staff_can_reroute(client, settings, staff_user, ticket):
    queue = Queue.objects.create(name="Billing", slug="billing")
    client.force_login(staff_user)
    client.post(
        f"/tickets/{ticket.pk}/edit/",
        {"subject": ticket.subject, "body": ticket.body, "queue": queue.pk, "source_application": ""},
        HTTP_HOST=settings.ADMIN_DOMAIN,
    )
    ticket.refresh_from_db()
    assert ticket.queue_id == queue.pk
