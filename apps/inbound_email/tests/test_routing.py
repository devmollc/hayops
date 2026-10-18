import io
import re

import pytest

from apps.core.models import BouncedAddress
from apps.inbound_email import tasks
from apps.inbound_email.email_parser import parse_email
from apps.inbound_email.handler import process_inbound_email
from apps.tickets.models import Comment, Ticket

pytestmark = pytest.mark.django_db


def _parsed(**overrides):
    base = {
        "subject": "Widget is broken",
        "body": "It does not load.",
        "from": "customer@example.com",
        "to": "support@mail.appsfol.io",
        "reply_to": "",
        "attachments": [],
        "message_id": "",
        "in_reply_to": "",
        "references": [],
        "recipients": [],
        "spam_flagged": False,
        # DMARC passed, i.e. an ordinary customer reply. Spoof tests override this.
        "from_authenticated": True,
    }
    base.update(overrides)
    return base


def test_recipient_domain_attaches_source_app(source_app):
    ticket = process_inbound_email(
        _parsed(
            message_id="<new1@example.com>",
            recipients=["support@mail.appsfol.io"],
        )
    )
    assert ticket.source_application_id == source_app.pk


def test_reply_from_participant_adds_comment(ticket):
    result = process_inbound_email(
        _parsed(
            subject=f"Re: [{ticket.reference_number}] Widget is broken",
            body="More info",
            message_id="<reply1@example.com>",
        )
    )
    assert result.pk == ticket.pk
    assert ticket.comments.count() == 1
    comment = ticket.comments.first()
    assert comment.email_message_id == "<reply1@example.com>"


def test_reply_from_stranger_creates_new_ticket(ticket):
    result = process_inbound_email(
        _parsed(
            subject=f"Re: [{ticket.reference_number}] Widget is broken",
            body="I am guessing the ref",
            message_id="<stranger1@example.com>",
            **{"from": "stranger@example.com"},
        )
    )
    assert result.pk != ticket.pk
    assert ticket.comments.count() == 0


def test_duplicate_message_id_no_second_comment(ticket):
    parsed = _parsed(
        subject=f"Re: [{ticket.reference_number}] Widget is broken",
        body="First reply",
        message_id="<dup@example.com>",
    )
    process_inbound_email(parsed)
    assert ticket.comments.count() == 1

    process_inbound_email(parsed)
    assert ticket.comments.count() == 1


def test_threading_fallback_via_references(ticket):
    Comment.objects.create(
        ticket=ticket,
        author_email="agent@devmosolutions.com",
        body="Our reply",
        email_message_id="<agent-reply@mail.appsfol.io>",
    )

    result = process_inbound_email(
        _parsed(
            body="Thanks, more info",
            message_id="<customer-followup@example.com>",
            in_reply_to="<agent-reply@mail.appsfol.io>",
            references=["<agent-reply@mail.appsfol.io>"],
        )
    )
    assert result.pk == ticket.pk
    assert ticket.comments.filter(body="Thanks, more info").exists()


def test_threading_via_in_reply_to_alone(ticket):
    """In-Reply-To is the most reliable threading header and was previously ignored."""
    Comment.objects.create(
        ticket=ticket,
        author_email="agent@devmosolutions.com",
        body="Our reply",
        email_message_id="<agent-reply@mail.appsfol.io>",
    )

    result = process_inbound_email(
        _parsed(
            subject="Re: something with no reference",
            body="Thanks",
            message_id="<followup@example.com>",
            in_reply_to="<agent-reply@mail.appsfol.io>",
        )
    )
    assert result.pk == ticket.pk


def test_threading_matches_the_ticket_acknowledgement_id(ticket):
    ticket.email_message_id = "<hd-ack@mail.appsfol.io>"
    ticket.save(update_fields=["email_message_id"])

    result = process_inbound_email(
        _parsed(
            subject="Re: no reference here",
            body="Adding detail",
            message_id="<followup2@example.com>",
            in_reply_to="<hd-ack@mail.appsfol.io>",
        )
    )
    assert result.pk == ticket.pk


def test_cc_recipients_become_participants(source_app):
    ticket = process_inbound_email(
        _parsed(
            message_id="<withcc@example.com>",
            recipients=["support@mail.appsfol.io", "colleague@example.com", "customer@example.com"],
        )
    )
    emails = set(ticket.participants.values_list("email", flat=True))
    # Our own inbound address must not be recorded as a participant.
    assert emails == {"customer@example.com", "colleague@example.com"}


def test_cc_colleague_reply_appends_instead_of_forking(source_app):
    ticket = process_inbound_email(
        _parsed(
            message_id="<orig@example.com>",
            recipients=["support@mail.appsfol.io", "colleague@example.com"],
        )
    )

    result = process_inbound_email(
        _parsed(
            subject=f"Re: [{ticket.reference_number}] Widget is broken",
            body="Colleague chiming in",
            message_id="<ccreply@example.com>",
            **{"from": "colleague@example.com"},
        )
    )

    assert result.pk == ticket.pk
    assert Ticket.objects.count() == 1


def test_stranger_on_the_thread_appends_but_a_guessed_reference_does_not(ticket):
    """A Message-ID we generated proves the sender was really on the thread."""
    ticket.email_message_id = "<hd-ack2@mail.appsfol.io>"
    ticket.save(update_fields=["email_message_id"])

    guessed = process_inbound_email(
        _parsed(
            subject=f"Re: [{ticket.reference_number}] Widget is broken",
            message_id="<guess@example.com>",
            **{"from": "stranger@example.com"},
        )
    )
    assert guessed.pk != ticket.pk

    forwarded = process_inbound_email(
        _parsed(
            subject="Re: forwarded to me",
            body="I was forwarded this",
            message_id="<onthread@example.com>",
            in_reply_to="<hd-ack2@mail.appsfol.io>",
            **{"from": "colleague@example.com"},
        )
    )
    assert forwarded.pk == ticket.pk
    assert ticket.participants.filter(email="colleague@example.com").exists()


def test_spam_flagged_moves_to_failed_no_ticket(monkeypatch):
    raw_email = (
        b"From: spammer@example.com\r\n"
        b"To: support@mail.appsfol.io\r\n"
        b"Subject: Buy now\r\n"
        b"X-SES-Spam-Verdict: FAIL\r\n"
        b"Message-ID: <spam1@example.com>\r\n"
        b"\r\n"
        b"Spammy body\r\n"
    )

    class FakeS3:
        def __init__(self):
            self.copied = []
            self.deleted = []

        def get_object(self, Bucket, Key):  # noqa: N803
            return {"Body": io.BytesIO(raw_email)}

        def copy_object(self, Bucket, CopySource, Key):  # noqa: N803
            self.copied.append(Key)

        def delete_object(self, Bucket, Key):  # noqa: N803
            self.deleted.append(Key)

    fake_s3 = FakeS3()
    monkeypatch.setattr(tasks, "_get_s3_client", lambda: fake_s3)

    tasks.process_ses_inbound(bucket="test-bucket", key="incoming/spam.eml")

    assert Ticket.objects.count() == 0
    assert fake_s3.copied == ["failed/spam.eml"]
    assert fake_s3.deleted == ["incoming/spam.eml"]


def _raw(from_addr, to_addr, subject, extra_headers=""):
    # The id must be a valid dot-atom. Subjects here contain "[HD-0001]", and
    # brackets are illegal in id-left -- newer CPython patch releases raise
    # IndexError out of get_msg_id rather than tolerating it.
    msg_id = re.sub(r"[^A-Za-z0-9]", "", subject) or "msg"
    return (
        f"From: {from_addr}\r\nTo: {to_addr}\r\nSubject: {subject}\r\n"
        f"Message-ID: <{msg_id}@test>\r\n{extra_headers}"
        "Content-Type: text/plain\r\n\r\nbody text\r\n"
    ).encode()


def test_auto_submitted_email_creates_no_ticket(source_app):
    parsed = parse_email(
        _raw("someone@example.com", "support@mail.appsfol.io", "Out of office", "Auto-Submitted: auto-replied\r\n")
    )
    assert parsed["auto_generated"] is True
    assert process_inbound_email(parsed) is None
    assert Ticket.objects.count() == 0


def _dsn(recipient_groups, subject="Delivery Status Notification (Failure)"):
    return (
        f"From: MAILER-DAEMON@amazonses.com\r\nTo: support@mail.appsfol.io\r\nSubject: {subject}\r\n"
        "Message-ID: <dsn@test>\r\n"
        'Content-Type: multipart/report; report-type=delivery-status; boundary="b"\r\n\r\n'
        "--b\r\nContent-Type: text/plain\r\n\r\nAn error occurred while trying to deliver the mail.\r\n"
        "--b\r\nContent-Type: message/delivery-status\r\n\r\n"
        "Reporting-MTA: dns; a8-51.smtp-out.amazonses.com\r\n\r\n"
        f"{recipient_groups}"
        "--b--\r\n"
    ).encode()


def test_bounce_report_creates_no_ticket(source_app):
    """SES's MAILER-DAEMON sets no Auto-Submitted header; the report body is the signal."""
    parsed = parse_email(_dsn("Final-Recipient: rfc822; gone@example.com\r\nAction: failed\r\nStatus: 5.1.1\r\n"))
    assert parsed["auto_generated"] is True
    assert process_inbound_email(parsed) is None
    assert Ticket.objects.count() == 0


def test_bounce_flags_the_failed_address(source_app):
    parsed = parse_email(
        _dsn(
            "Final-Recipient: rfc822; Gone@Example.com\r\nAction: failed\r\nStatus: 5.1.1\r\n"
            "Diagnostic-Code: smtp; 550 user unknown\r\n\r\n"
        )
    )
    process_inbound_email(parsed)

    bounced = BouncedAddress.objects.get()
    assert bounced.email == "gone@example.com"  # normalised, so lookups match participants
    assert bounced.status == "5.1.1"
    assert "550 user unknown" in bounced.diagnostic


def test_transient_delay_does_not_flag_the_address(source_app):
    """A full mailbox retries and usually succeeds; flagging it would cry wolf."""
    parsed = parse_email(_dsn("Final-Recipient: rfc822; busy@example.com\r\nAction: delayed\r\nStatus: 4.2.2\r\n\r\n"))
    process_inbound_email(parsed)

    assert BouncedAddress.objects.count() == 0


def test_repeat_bounce_updates_rather_than_duplicates(source_app):
    for status in ("5.1.1", "5.2.1"):
        parsed = parse_email(
            _dsn(f"Final-Recipient: rfc822; gone@example.com\r\nAction: failed\r\nStatus: {status}\r\n\r\n")
        )
        process_inbound_email(parsed)
        # A second identical Message-ID must not short-circuit before the bounce is recorded
        Comment.objects.all().delete()

    assert BouncedAddress.objects.count() == 1
    assert BouncedAddress.objects.get().status == "5.2.1"


def test_null_return_path_creates_no_ticket(source_app):
    """RFC 5321 null envelope sender -- only bounces and DSNs carry it."""
    parsed = parse_email(
        _raw("MAILER-DAEMON@amazonses.com", "support@mail.appsfol.io", "Undelivered Mail", "Return-Path: <>\r\n")
    )
    assert parsed["auto_generated"] is True
    assert process_inbound_email(parsed) is None
    assert Ticket.objects.count() == 0


def test_precedence_bulk_email_creates_no_ticket(source_app):
    parsed = parse_email(_raw("list@example.com", "support@mail.appsfol.io", "Newsletter", "Precedence: bulk\r\n"))
    assert process_inbound_email(parsed) is None
    assert Ticket.objects.count() == 0


def test_our_own_notification_bouncing_back_creates_no_ticket(source_app):
    """The mail-loop guard: our ack arriving at another of our inbound domains."""
    parsed = parse_email(
        _raw("ticket+hd-0001@mail.appsfol.io", "support@mail.gxpsign.app", "[HD-0001] We received your request")
    )
    assert process_inbound_email(parsed) is None
    assert Ticket.objects.count() == 0


def test_email_from_our_mail_domain_creates_no_ticket(source_app):
    parsed = parse_email(_raw("smoketest@mail.appsfol.io", "support@mail.appsfol.io", "Smoke test"))
    assert process_inbound_email(parsed) is None
    assert Ticket.objects.count() == 0


def test_normal_customer_email_still_creates_a_ticket(source_app):
    parsed = parse_email(_raw("customer@example.com", "support@mail.appsfol.io", "Real problem"))
    assert parsed["auto_generated"] is False
    ticket = process_inbound_email(parsed)
    assert ticket is not None
    assert ticket.source_application_id == source_app.pk


def test_mail_forwarded_from_the_apex_address_is_still_attributed(source_app):
    """support@appsfol.io is a Migadu mailbox forwarding to support@mail.appsfol.io.

    SES receives the forwarded copy, but it still carries the apex address in To:
    and Migadu adds no Delivered-To -- so matching mail_domain alone left every
    forwarded ticket with no source application.
    """
    ticket = process_inbound_email(
        _parsed(
            message_id="<forwarded@example.com>",
            to="support@appsfol.io",
            recipients=["support@appsfol.io"],
        )
    )

    assert ticket.source_application == source_app
    # The forwarding address must not become a participant, or every reply is
    # addressed back into the forward.
    assert set(ticket.participants.values_list("email", flat=True)) == {"customer@example.com"}


def test_spoofed_from_on_a_guessed_reference_does_not_post_as_the_customer(ticket):
    """`From:` is just a header; without DMARC it proves nothing about the sender."""
    spoofed = process_inbound_email(
        _parsed(
            subject=f"Re: [{ticket.reference_number}] Widget is broken",
            body="Wire the money to this account",
            message_id="<spoof@example.com>",
            from_authenticated=False,
        )
    )
    assert spoofed.pk != ticket.pk
    assert ticket.comments.count() == 0


def test_spoofed_from_still_threads_when_it_carries_a_message_id_we_issued(ticket):
    """Our own Message-ID is the proof DMARC cannot give us, so it stays sufficient."""
    ticket.email_message_id = "<hd-ack3@mail.appsfol.io>"
    ticket.save(update_fields=["email_message_id"])

    result = process_inbound_email(
        _parsed(
            subject="Re: no reference here",
            body="Still me",
            message_id="<unauth-thread@example.com>",
            in_reply_to="<hd-ack3@mail.appsfol.io>",
            from_authenticated=False,
        )
    )
    assert result.pk == ticket.pk
