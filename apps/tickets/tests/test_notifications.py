import pytest
from django.core import mail

from apps.inbound_email.tasks import send_ticket_notification
from apps.tickets.models import Comment, Ticket, TicketParticipant
from apps.tickets.services import TicketService

pytestmark = pytest.mark.django_db

TASK_NAME = "apps.inbound_email.tasks.send_ticket_notification"


def _deferred_types(job_connector):
    return [job["args"]["notification_type"] for job in job_connector.jobs.values() if job["task_name"] == TASK_NAME]


def test_create_ticket_defers_created_and_team_new_ticket(
    job_connector, source_app, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        TicketService.create_ticket(
            subject="Help",
            body="Something broke",
            submitter_email="newcustomer@example.com",
            source_application=source_app,
        )
    assert sorted(_deferred_types(job_connector)) == ["created", "team_new_ticket"]


def test_staff_public_reply_defers_reply(job_connector, ticket, staff_user, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        TicketService.add_comment(
            ticket=ticket,
            body="We're on it",
            author=staff_user,
            comment_type=Comment.CommentType.PUBLIC,
        )
    assert _deferred_types(job_connector) == ["reply"]


def test_customer_public_comment_defers_team_reply(job_connector, ticket, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        TicketService.add_comment(
            ticket=ticket,
            body="Still broken",
            author_email="customer@example.com",
            comment_type=Comment.CommentType.PUBLIC,
        )
    assert _deferred_types(job_connector) == ["team_reply"]


def test_internal_note_defers_nothing(job_connector, ticket, staff_user, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        TicketService.add_comment(
            ticket=ticket,
            body="internal note",
            author=staff_user,
            comment_type=Comment.CommentType.INTERNAL,
        )
    assert _deferred_types(job_connector) == []


def test_resolve_defers_csat_survey_only(job_connector, ticket, staff_user, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        TicketService.change_status(ticket, Ticket.Status.RESOLVED, actor=staff_user)
    assert _deferred_types(job_connector) == ["csat_survey"]


def test_other_status_change_defers_status_changed(
    job_connector, ticket, staff_user, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        TicketService.change_status(ticket, Ticket.Status.OPEN, actor=staff_user)
    assert _deferred_types(job_connector) == ["status_changed"]


def test_created_notification_sends_branded_email_with_login_link(ticket):
    send_ticket_notification(ticket_id=ticket.pk, notification_type="created")

    assert len(mail.outbox) == 1
    sent = mail.outbox[0]
    assert sent.from_email == f"ticket+{ticket.reference_number.lower()}@mail.appsfol.io"
    assert f"[{ticket.reference_number}]" in sent.subject
    html_body = sent.alternatives[0][0]
    assert "https://support.appsfol.io/portal/login/" in html_body

    # The Message-ID we threaded under is recorded so inbound replies can match it
    ticket.refresh_from_db()
    assert ticket.email_message_id == sent.extra_headers["Message-ID"]


def test_reply_notification_includes_reply_text_and_login_link(ticket, staff_user):
    comment = TicketService.add_comment(
        ticket=ticket,
        body="We rolled back the bad deploy.",
        author=staff_user,
        comment_type=Comment.CommentType.PUBLIC,
    )
    mail.outbox.clear()

    send_ticket_notification(ticket_id=ticket.pk, notification_type="reply", comment_id=comment.pk)

    assert len(mail.outbox) == 1
    sent = mail.outbox[0]
    assert sent.to == ["customer@example.com"]
    html_body = sent.alternatives[0][0]
    assert "We rolled back the bad deploy." in html_body
    assert "https://support.appsfol.io/portal/login/" in html_body


def test_reply_notifies_every_participant_except_staff_and_the_author(ticket, staff_user):
    TicketParticipant.objects.create(ticket=ticket, email="cc@example.com", role=TicketParticipant.Role.CC)
    comment = TicketService.add_comment(
        ticket=ticket,
        body="Fixed",
        author=staff_user,
        comment_type=Comment.CommentType.PUBLIC,
    )
    mail.outbox.clear()

    send_ticket_notification(ticket_id=ticket.pk, notification_type="reply", comment_id=comment.pk)

    # add_comment registers the replying agent as a participant; they must not be mailed.
    recipients = sorted(address for sent in mail.outbox for address in sent.to)
    assert recipients == ["cc@example.com", "customer@example.com"]


def test_customer_reply_does_not_notify_the_customer_who_wrote_it(ticket):
    comment = TicketService.add_comment(
        ticket=ticket,
        body="Any update?",
        author_email="customer@example.com",
        comment_type=Comment.CommentType.PUBLIC,
    )
    mail.outbox.clear()

    send_ticket_notification(ticket_id=ticket.pk, notification_type="reply", comment_id=comment.pk)

    assert mail.outbox == []


def test_team_new_ticket_notification_requires_support_notify_email(ticket, settings):
    settings.SUPPORT_NOTIFY_EMAIL = ""
    send_ticket_notification(ticket_id=ticket.pk, notification_type="team_new_ticket")
    assert len(mail.outbox) == 0

    settings.SUPPORT_NOTIFY_EMAIL = "support-team@devmosolutions.com"
    send_ticket_notification(ticket_id=ticket.pk, notification_type="team_new_ticket")

    assert len(mail.outbox) == 1
    sent = mail.outbox[0]
    assert sent.to == ["support-team@devmosolutions.com"]
    assert f"[{ticket.reference_number}]" in sent.subject


def test_resend_names_every_address_the_link_actually_goes_to(client, staff_user, ticket, job_connector):
    """The confirmation used to name only the submitter; the mail goes to every participant."""
    from django.urls import reverse

    TicketParticipant.objects.create(ticket=ticket, email="colleague@example.com", role=TicketParticipant.Role.CC)
    client.force_login(staff_user)

    resp = client.post(reverse("tickets:resend_magic_link", args=[ticket.pk]), follow=True, HTTP_HOST="testserver")

    text = " ".join(str(m) for m in resp.context["messages"])
    assert "customer@example.com" in text
    assert "colleague@example.com" in text
