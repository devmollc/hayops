import pytest
from django.core import mail
from django.core.cache import cache

from apps.accounts.models import MagicLoginToken, User
from apps.core.models import SourceApplication
from apps.tickets.models import Comment, Ticket, TicketParticipant
from apps.tickets.services import TicketService

pytestmark = pytest.mark.django_db

SEND_TASK = "apps.inbound_email.tasks.send_magic_login"


@pytest.fixture(autouse=True)
def _allow_test_hosts(settings, source_app):
    settings.ALLOWED_HOSTS = [*settings.ALLOWED_HOSTS, source_app.portal_domain, settings.ADMIN_DOMAIN]


@pytest.fixture(autouse=True)
def _clear_rate_limits():
    cache.clear()


def _host(source_app):
    return {"HTTP_HOST": source_app.portal_domain}


def _sign_in(client, source_app, email, next_url=""):
    """Run the full request-link -> click -> confirm flow and return the raw token."""
    _, raw = MagicLoginToken.issue(email=email, source_application=source_app, next_url=next_url)
    client.post(f"/portal/login/{raw}/", **_host(source_app))
    return raw


def test_login_page_is_reachable_on_a_branded_host(client, source_app):
    resp = client.get("/portal/login/", **_host(source_app))
    assert resp.status_code == 200


def test_unknown_email_gets_the_same_response_and_no_mail(client, source_app, ticket, job_connector,
                                                          django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        known = client.post("/portal/login/", {"email": "customer@example.com"}, **_host(source_app))
    cache.clear()
    with django_capture_on_commit_callbacks(execute=True):
        unknown = client.post("/portal/login/", {"email": "nobody@example.com"}, **_host(source_app))

    assert known.status_code == unknown.status_code == 200
    # Only the address that actually has a ticket triggers a send.
    sent = [job for job in job_connector.jobs.values() if job["task_name"] == SEND_TASK]
    assert [job["args"]["email"] for job in sent] == ["customer@example.com"]


def test_a_ticket_with_no_source_application_still_gets_a_link_and_shows_up(
    client, source_app, job_connector, django_capture_on_commit_callbacks
):
    """Staff-filed tickets could leave source_application blank; the customer is still a customer."""
    unscoped = Ticket.objects.create(subject="Filed by an agent", body="x")
    TicketParticipant.objects.create(ticket=unscoped, email="orphan@example.com", role=TicketParticipant.Role.SUBMITTER)

    with django_capture_on_commit_callbacks(execute=True):
        client.post("/portal/login/", {"email": "orphan@example.com"}, **_host(source_app))
    sent = [job for job in job_connector.jobs.values() if job["task_name"] == SEND_TASK]
    assert [job["args"]["email"] for job in sent] == ["orphan@example.com"]

    _sign_in(client, source_app, "orphan@example.com")
    resp = client.get("/portal/tickets/", **_host(source_app))
    assert list(resp.context["open_tickets"]) == [unscoped]


def test_repeated_requests_for_one_address_are_rate_limited(client, source_app, ticket):
    """Stops one mailbox being bombed from rotating IPs."""
    for _ in range(3):
        resp = client.post("/portal/login/", {"email": "customer@example.com"}, **_host(source_app))
        assert resp.status_code == 200

    resp = client.post("/portal/login/", {"email": "customer@example.com"}, **_host(source_app))
    assert resp.status_code == 429


def test_requests_from_one_ip_are_rate_limited(client, source_app, ticket):
    for i in range(5):
        resp = client.post("/portal/login/", {"email": f"person{i}@example.com"}, **_host(source_app))
        assert resp.status_code == 200

    resp = client.post("/portal/login/", {"email": "person9@example.com"}, **_host(source_app))
    assert resp.status_code == 429


def test_magic_login_email_carries_a_working_link(client, source_app, ticket):
    from apps.inbound_email.tasks import send_magic_login

    send_magic_login(email="customer@example.com", source_application_id=source_app.pk)

    assert len(mail.outbox) == 1
    html_body = mail.outbox[0].alternatives[0][0]
    assert "https://support.appsfol.io/portal/login/" in html_body

    # The link in that email must actually sign the customer in.
    raw = html_body.split("/portal/login/")[1].split("/")[0]
    resp = client.post(f"/portal/login/{raw}/", **_host(source_app))
    assert resp.status_code == 302
    assert "_auth_user_id" in client.session


def test_get_does_not_consume_the_token(client, source_app, ticket):
    """Mail scanners GET every link in a delivered message; that must not burn it."""
    _, raw = MagicLoginToken.issue(email="customer@example.com", source_application=source_app)

    resp = client.get(f"/portal/login/{raw}/", **_host(source_app))

    assert resp.status_code == 200
    assert "_auth_user_id" not in client.session
    assert MagicLoginToken.find_active(raw) is not None


def test_post_signs_in_and_links_existing_participations(client, source_app, ticket):
    _, raw = MagicLoginToken.issue(email="customer@example.com", source_application=source_app)

    resp = client.post(f"/portal/login/{raw}/", **_host(source_app))

    assert resp.status_code == 302
    assert resp.url == "/portal/tickets/"
    user = User.objects.get(email="customer@example.com")
    assert not user.is_staff
    assert not user.has_usable_password()
    # The submitter row was created with user=NULL by the ticket fixture.
    assert TicketParticipant.objects.get(ticket=ticket, email="customer@example.com").user_id == user.pk


def test_a_used_link_stops_working(client, source_app, ticket):
    _, raw = MagicLoginToken.issue(email="customer@example.com", source_application=source_app)
    client.post(f"/portal/login/{raw}/", **_host(source_app))
    client.post("/portal/logout/", **_host(source_app))

    resp = client.post(f"/portal/login/{raw}/", **_host(source_app))

    assert resp.status_code == 410
    assert "_auth_user_id" not in client.session


def test_next_url_sends_the_customer_to_the_right_ticket(client, source_app, ticket):
    _sign_in(client, source_app, "customer@example.com")
    _, raw2 = MagicLoginToken.issue(
        email="customer@example.com",
        source_application=source_app,
        next_url=f"/portal/tickets/{ticket.reference_number}/",
    )
    resp = client.post(f"/portal/login/{raw2}/", **_host(source_app))
    assert resp.url == f"/portal/tickets/{ticket.reference_number}/"


def test_ticket_list_shows_open_and_closed_tickets(client, source_app, ticket):
    closed = Ticket.objects.create(
        subject="Old thing", body="done", status=Ticket.Status.CLOSED, source_application=source_app
    )
    TicketParticipant.objects.create(
        ticket=closed, email="customer@example.com", role=TicketParticipant.Role.SUBMITTER
    )
    _sign_in(client, source_app, "customer@example.com")

    body = client.get("/portal/tickets/", **_host(source_app)).content.decode()

    assert ticket.subject in body
    assert closed.subject in body


def test_ticket_list_hides_other_customers_tickets(client, source_app, ticket):
    other = Ticket.objects.create(subject="Not yours", body="x", source_application=source_app)
    TicketParticipant.objects.create(
        ticket=other, email="someone@example.com", role=TicketParticipant.Role.SUBMITTER
    )
    _sign_in(client, source_app, "customer@example.com")

    body = client.get("/portal/tickets/", **_host(source_app)).content.decode()

    assert "Not yours" not in body


def test_ticket_list_is_scoped_to_the_branded_host(client, source_app, ticket):
    other_app = SourceApplication.objects.create(
        name="Other", slug="other-test", product_name="Other", portal_domain="support.other.test"
    )
    elsewhere = Ticket.objects.create(subject="Other product issue", body="x", source_application=other_app)
    TicketParticipant.objects.create(
        ticket=elsewhere, email="customer@example.com", role=TicketParticipant.Role.SUBMITTER
    )
    _sign_in(client, source_app, "customer@example.com")

    body = client.get("/portal/tickets/", **_host(source_app)).content.decode()

    assert ticket.subject in body
    assert "Other product issue" not in body


def test_ticket_detail_hides_internal_notes(client, source_app, ticket, staff_user):
    Comment.objects.create(
        ticket=ticket, author=staff_user, body="do not show this", comment_type=Comment.CommentType.INTERNAL
    )
    Comment.objects.create(
        ticket=ticket, author=staff_user, body="visible reply", comment_type=Comment.CommentType.PUBLIC
    )
    _sign_in(client, source_app, "customer@example.com")

    body = client.get(f"/portal/tickets/{ticket.reference_number}/", **_host(source_app)).content.decode()

    assert "visible reply" in body
    assert "do not show this" not in body


def test_ticket_detail_hides_the_internal_note_event(client, source_app, ticket, staff_user):
    """The note is hidden, but so is the "comment added" line that betrays it exists."""
    TicketService.add_comment(
        ticket=ticket, body="do not show this", author=staff_user, comment_type=Comment.CommentType.INTERNAL
    )
    _sign_in(client, source_app, "customer@example.com")

    body = client.get(f"/portal/tickets/{ticket.reference_number}/", **_host(source_app)).content.decode()

    assert "do not show this" not in body
    assert "comment added" not in body.lower()
    assert staff_user.email not in body


def test_customer_cannot_open_someone_elses_ticket(client, source_app, ticket):
    other = Ticket.objects.create(subject="Not yours", body="x", source_application=source_app)
    TicketParticipant.objects.create(
        ticket=other, email="someone@example.com", role=TicketParticipant.Role.SUBMITTER
    )
    _sign_in(client, source_app, "customer@example.com")

    resp = client.get(f"/portal/tickets/{other.reference_number}/", **_host(source_app))

    assert resp.status_code == 404


def test_customer_can_reply_from_the_portal(client, source_app, ticket):
    _sign_in(client, source_app, "customer@example.com")

    client.post(
        f"/portal/tickets/{ticket.reference_number}/", {"body": "Still broken"}, **_host(source_app)
    )

    comment = ticket.comments.get()
    assert comment.body == "Still broken"
    assert comment.comment_type == Comment.CommentType.PUBLIC
    assert comment.author.email == "customer@example.com"


def test_anonymous_visitor_is_sent_to_the_portal_login_not_accounts(client, source_app):
    resp = client.get("/portal/tickets/", **_host(source_app))

    assert resp.status_code == 302
    # /accounts/ is not in PORTAL_ALLOWED_PREFIXES, so LOGIN_URL would 404 here.
    assert resp.url.startswith("/portal/login/")


def test_signup_is_closed(client, settings):
    resp = client.get("/accounts/signup/", HTTP_HOST=settings.ADMIN_DOMAIN)
    assert resp.status_code == 404


def test_staff_queue_redirects_customers_to_the_portal(client, settings, ticket):
    user = User.objects.create(email="customer@example.com", is_staff=False)
    client.force_login(user)

    resp = client.get("/tickets/", HTTP_HOST=settings.ADMIN_DOMAIN)

    assert resp.status_code == 302
    assert resp.url == "/portal/tickets/"


def test_staff_login_offers_customers_a_route_to_their_ticket(client, settings):
    """Staff ticket links reach customers; the password form must not be a dead end."""
    resp = client.get(
        "/accounts/login/?next=/tickets/HD-0038/", HTTP_HOST=settings.ADMIN_DOMAIN
    )

    assert resp.status_code == 200
    assert "/portal/login/?next=/portal/tickets/HD-0038/" in resp.content.decode()


def test_staff_login_ignores_a_non_ticket_next(client, settings):
    resp = client.get(
        "/accounts/login/?next=https://evil.example.com/", HTTP_HOST=settings.ADMIN_DOMAIN
    )

    assert resp.status_code == 200
    assert "evil.example.com" not in resp.context["portal_login_url"]
    assert resp.context["portal_login_url"] == "/portal/login/"
