import pytest

from apps.tickets.magic_link import generate_ticket_access_token
from apps.tickets.models import Comment


@pytest.fixture(autouse=True)
def _allow_test_hosts(settings, source_app):
    settings.ALLOWED_HOSTS = [*settings.ALLOWED_HOSTS, source_app.portal_domain, settings.ADMIN_DOMAIN]


@pytest.fixture
def linky_ticket(ticket):
    ticket.body = "See https://example.com/kb/reset-password for steps."
    ticket.save(update_fields=["body"])
    Comment.objects.create(
        ticket=ticket,
        author_email="agent@devmosolutions.com",
        body="Article: https://example.com/kb/billing",
        comment_type=Comment.CommentType.PUBLIC,
    )
    return ticket


@pytest.mark.django_db
def test_magic_link_page_linkifies_urls(client, linky_ticket):
    token = generate_ticket_access_token(linky_ticket.pk, linky_ticket.participants.first().email)
    body = client.get(f"/ticket/{token}/").content.decode()
    assert 'href="https://example.com/kb/reset-password"' in body
    assert 'href="https://example.com/kb/billing"' in body


@pytest.mark.django_db
def test_staff_detail_linkifies_urls(client, staff_user, settings, linky_ticket):
    client.force_login(staff_user)
    resp = client.get(f"/tickets/{linky_ticket.reference_number}/", HTTP_HOST=settings.ADMIN_DOMAIN)
    body = resp.content.decode()
    assert 'href="https://example.com/kb/reset-password"' in body
    assert 'href="https://example.com/kb/billing"' in body
