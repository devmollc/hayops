import pytest
from django.core.cache import cache

from apps.core.models import SourceApplication
from apps.kb.models import Article, Category
from apps.tickets.magic_link import generate_ticket_access_token
from apps.tickets.models import Comment, Ticket, TicketParticipant


@pytest.fixture(autouse=True)
def _allow_test_hosts(settings, source_app):
    settings.ALLOWED_HOSTS = [*settings.ALLOWED_HOSTS, source_app.portal_domain, settings.ADMIN_DOMAIN]


@pytest.mark.django_db
def test_portal_home_renders_branded_submit_form(client, source_app):
    resp = client.get("/", HTTP_HOST=source_app.portal_domain)
    assert resp.status_code == 200
    assert source_app.branding_name in resp.content.decode()


@pytest.mark.django_db
def test_portal_submit_creates_ticket(client, source_app):
    cache.clear()
    resp = client.post(
        "/submit/",
        {"name": "Jo", "email": "jo@example.com", "subject": "Help", "body": "It broke", "website": ""},
        HTTP_HOST=source_app.portal_domain,
    )
    assert resp.status_code == 200
    ticket = Ticket.objects.get(subject="Help")
    assert ticket.source_application_id == source_app.pk
    assert TicketParticipant.objects.filter(ticket=ticket, email="jo@example.com").exists()
    assert "check your email" in resp.content.decode().lower()


@pytest.mark.django_db
def test_portal_submit_honeypot_blocks_silently(client, source_app):
    cache.clear()
    resp = client.post(
        "/submit/",
        {
            "name": "Bot",
            "email": "bot@example.com",
            "subject": "spam",
            "body": "spam",
            "website": "http://spam.example",
        },
        HTTP_HOST=source_app.portal_domain,
    )
    assert resp.status_code == 200
    assert not Ticket.objects.filter(subject="spam").exists()


@pytest.mark.django_db
def test_portal_submit_rate_limited_after_five(client, source_app):
    cache.clear()
    data = {"email": "x@example.com", "subject": "s", "body": "b", "website": ""}
    for _ in range(5):
        resp = client.post("/submit/", data, HTTP_HOST=source_app.portal_domain)
        assert resp.status_code == 200
    resp = client.post("/submit/", data, HTTP_HOST=source_app.portal_domain)
    assert resp.status_code == 429


@pytest.mark.django_db
def test_agent_queue_hidden_on_portal_host(client, source_app):
    resp = client.get("/tickets/", HTTP_HOST=source_app.portal_domain)
    assert resp.status_code == 404


@pytest.mark.django_db
def test_agent_queue_reachable_on_admin_domain(client, staff_user, settings):
    client.force_login(staff_user)
    resp = client.get("/tickets/", HTTP_HOST=settings.ADMIN_DOMAIN)
    assert resp.status_code == 200


@pytest.mark.django_db
def test_magic_link_reply_creates_public_comment(client, ticket):
    email = ticket.participants.first().email
    token = generate_ticket_access_token(ticket.pk, email)
    resp = client.post(f"/ticket/{token}/", {"body": "Thanks for the help"})
    assert resp.status_code == 302
    comment = Comment.objects.get(ticket=ticket, author_email=email)
    assert comment.body == "Thanks for the help"
    assert comment.comment_type == Comment.CommentType.PUBLIC


@pytest.mark.django_db
def test_magic_link_reply_rejected_on_closed_ticket(client, ticket):
    ticket.status = Ticket.Status.CLOSED
    ticket.save(update_fields=["status"])
    email = ticket.participants.first().email
    token = generate_ticket_access_token(ticket.pk, email)
    resp = client.post(f"/ticket/{token}/", {"body": "Still reading?"})
    assert resp.status_code == 302
    assert not Comment.objects.filter(ticket=ticket, body="Still reading?").exists()


@pytest.mark.django_db
def test_kb_filters_by_source_application(client, source_app):
    other_app = SourceApplication.objects.create(
        name="Other",
        slug="other-test",
        portal_domain="support.other.example",
        mail_domain="mail.other.example",
        allowed_domains=[],
    )
    category = Category.objects.create(name="General", slug="general")
    Article.objects.create(
        title="Other app article",
        slug="other-app-article",
        body="x",
        category=category,
        is_published=True,
        source_application=other_app,
    )
    Article.objects.create(
        title="Global article",
        slug="global-article",
        body="x",
        category=category,
        is_published=True,
    )
    resp = client.get("/kb/", HTTP_HOST=source_app.portal_domain)
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "Global article" in body
    assert "Other app article" not in body
