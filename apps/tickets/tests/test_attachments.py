"""Attachment upload/display across the surfaces that accept files.

Covers the portal submit form, the portal and magic-link reply boxes, and the
inbound-email path (which saved attachments already but had no coverage).
"""

import pytest
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.inbound_email.handler import process_inbound_email
from apps.tickets.magic_link import generate_ticket_access_token
from apps.tickets.models import Attachment, Comment, Ticket, TicketParticipant
from apps.tickets.services import MAX_ATTACHMENT_BYTES, MAX_ATTACHMENTS, save_attachments


@pytest.fixture(autouse=True)
def _allow_test_hosts(settings, source_app):
    settings.ALLOWED_HOSTS = [*settings.ALLOWED_HOSTS, source_app.portal_domain, settings.ADMIN_DOMAIN]


def _upload(name="shot.png", content=b"png-bytes", content_type="image/png"):
    return SimpleUploadedFile(name, content, content_type=content_type)


# ── save_attachments: the shared trust boundary ──────────────────────────


@pytest.mark.django_db
def test_save_attachments_records_metadata(ticket):
    attachments, errors = save_attachments(ticket, [_upload()])

    assert errors == []
    assert len(attachments) == 1
    assert attachments[0].filename == "shot.png"
    assert attachments[0].content_type == "image/png"
    assert attachments[0].size_bytes == len(b"png-bytes")
    assert attachments[0].file.read() == b"png-bytes"


@pytest.mark.django_db
def test_oversized_file_is_rejected_without_losing_the_rest(ticket):
    big = _upload("huge.bin", b"x" * (MAX_ATTACHMENT_BYTES + 1), "application/octet-stream")

    attachments, errors = save_attachments(ticket, [big, _upload("ok.png")])

    assert [a.filename for a in attachments] == ["ok.png"]
    assert len(errors) == 1 and "huge.bin" in errors[0]


@pytest.mark.django_db
def test_file_count_is_capped(ticket):
    files = [_upload(f"f{i}.png") for i in range(MAX_ATTACHMENTS + 2)]

    attachments, errors = save_attachments(ticket, files)

    assert len(attachments) == MAX_ATTACHMENTS
    assert any("first" in e for e in errors)


@pytest.mark.django_db
def test_hostile_filename_is_truncated_to_the_column_width(ticket):
    """A 300-char name would be a DataError, not a validation message."""
    attachments, errors = save_attachments(ticket, [_upload("a" * 300 + ".png")])

    assert errors == []
    assert len(attachments[0].filename) == 255


@pytest.mark.django_db
def test_initial_attachments_excludes_reply_files(ticket):
    comment = Comment.objects.create(ticket=ticket, body="reply", author_email="c@example.com")
    save_attachments(ticket, [_upload("submitted.png")])
    save_attachments(ticket, [_upload("replied.png")], comment=comment)

    assert [a.filename for a in ticket.initial_attachments] == ["submitted.png"]
    assert ticket.attachments.count() == 2


# ── Display ──────────────────────────────────────────────────────────────
#
# Submission-time attachments (widget screenshots, inbound email, portal submit)
# used to be saved but rendered nowhere: the timelines only show comment-scoped
# files, and detail.html never used the `attachments` context var it was handed.


@pytest.mark.django_db
@pytest.mark.parametrize("surface", ["staff", "portal", "magic_link"])
def test_submission_attachments_are_visible_to_readers(surface, client, django_user_model, staff_user, ticket):
    save_attachments(ticket, [_upload("submitted.png")])

    if surface == "staff":
        client.force_login(staff_user)
        url = f"/tickets/{ticket.reference_number}/"
    elif surface == "portal":
        user = django_user_model.objects.create_user(email="customer@example.com", password="x")
        TicketParticipant.objects.filter(ticket=ticket, email="customer@example.com").update(user=user)
        client.force_login(user)
        url = f"/portal/tickets/{ticket.reference_number}/"
    else:
        url = f"/ticket/{generate_ticket_access_token(ticket.pk, 'customer@example.com')}/"

    resp = client.get(url, HTTP_HOST="testserver")

    assert resp.status_code == 200
    assert "submitted.png" in resp.content.decode()


# ── Portal submit ────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_portal_submit_saves_attachments(client, source_app):
    cache.clear()
    resp = client.post(
        "/submit/",
        {
            "email": "jo@example.com",
            "subject": "Broken",
            "body": "See screenshot",
            "website": "",
            "attachments": [_upload("bug.png")],
        },
        HTTP_HOST=source_app.portal_domain,
    )

    assert resp.status_code == 200
    ticket = Ticket.objects.get(subject="Broken")
    attachment = ticket.initial_attachments.get()
    assert attachment.filename == "bug.png"
    assert attachment.comment_id is None


@pytest.mark.django_db
def test_portal_submit_reports_rejected_file_but_still_creates_ticket(client, source_app):
    cache.clear()
    resp = client.post(
        "/submit/",
        {
            "email": "jo@example.com",
            "subject": "Broken",
            "body": "See screenshot",
            "website": "",
            "attachments": [_upload("huge.bin", b"x" * (MAX_ATTACHMENT_BYTES + 1), "application/octet-stream")],
        },
        HTTP_HOST=source_app.portal_domain,
    )

    assert Ticket.objects.filter(subject="Broken").exists()
    assert Attachment.objects.count() == 0
    assert "huge.bin" in resp.content.decode()


# ── Portal reply ─────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_portal_reply_attaches_to_the_comment(client, django_user_model, ticket, source_app):
    user = django_user_model.objects.create_user(email="customer@example.com", password="x")
    TicketParticipant.objects.filter(ticket=ticket, email="customer@example.com").update(user=user)
    client.force_login(user)

    resp = client.post(
        f"/portal/tickets/{ticket.reference_number}/",
        {"body": "Here it is", "attachments": [_upload("repro.png")]},
        HTTP_HOST=source_app.portal_domain,
    )

    assert resp.status_code == 302
    attachment = Attachment.objects.get()
    assert attachment.filename == "repro.png"
    assert attachment.comment.body == "Here it is"
    assert not ticket.initial_attachments.exists()


# ── Magic link reply ─────────────────────────────────────────────────────


@pytest.mark.django_db
def test_magic_link_reply_attaches_to_the_comment(client, ticket):
    token = generate_ticket_access_token(ticket.pk, "customer@example.com")

    resp = client.post(
        f"/ticket/{token}/",
        {"body": "Screenshot attached", "attachments": [_upload("magic.png")]},
        HTTP_HOST="testserver",
    )

    assert resp.status_code == 302
    attachment = Attachment.objects.get()
    assert attachment.filename == "magic.png"
    assert attachment.comment.body == "Screenshot attached"


# ── Inbound email ────────────────────────────────────────────────────────


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


@pytest.mark.django_db
def test_inbound_new_ticket_saves_mime_attachments(source_app):
    new_ticket = process_inbound_email(
        _parsed(
            message_id="<a1@example.com>",
            recipients=["support@mail.appsfol.io"],
            attachments=[{"filename": "trace.log", "content_type": "text/plain", "data": "boom"}],
        )
    )

    attachment = new_ticket.initial_attachments.get()
    assert attachment.filename == "trace.log"
    assert attachment.file.read() == b"boom"
    assert attachment.size_bytes == 4


@pytest.mark.django_db
def test_inbound_reply_attaches_to_the_new_comment(ticket, source_app):
    replied_to = process_inbound_email(
        _parsed(
            subject=f"Re: [{ticket.reference_number}] Widget is broken",
            message_id="<a2@example.com>",
            recipients=["support@mail.appsfol.io"],
            attachments=[{"filename": "shot.png", "content_type": "image/png", "data": b"\x89PNG"}],
        )
    )

    assert replied_to.pk == ticket.pk
    attachment = Attachment.objects.get()
    assert attachment.comment is not None
    assert attachment.file.read() == b"\x89PNG"
    assert not ticket.initial_attachments.exists()


# ── opening viewable files in a browser tab ──────────────────────────────


@pytest.mark.django_db
@pytest.mark.parametrize(
    "content_type,viewable",
    [
        ("image/png", True),
        ("image/jpeg", True),
        ("IMAGE/PNG", True),  # case and parameters are normalised
        ("image/png; charset=binary", True),
        ("application/pdf", True),
        ("text/plain", True),
        # Both execute script in whichever origin serves them, and uploads are
        # attacker-controlled, so they must keep downloading.
        ("text/html", False),
        ("image/svg+xml", False),
        ("application/octet-stream", False),
        ("", False),
    ],
)
def test_only_safe_types_are_offered_for_viewing(ticket, content_type, viewable):
    attachment = Attachment.objects.create(
        ticket=ticket, file=_upload(content_type=content_type), filename="f", content_type=content_type
    )
    assert bool(attachment.view_url) is viewable


@pytest.mark.django_db
def test_view_url_asks_storage_to_render_inline(ticket, monkeypatch):
    """The stored object keeps ContentDisposition: attachment; the presigned URL
    overrides it per request, so nothing at rest changes."""
    attachment = Attachment.objects.create(
        ticket=ticket, file=_upload(), filename="shot.png", content_type="image/png"
    )
    captured = {}

    def fake_url(name, parameters=None, **kwargs):
        captured["parameters"] = parameters
        return "https://example.invalid/signed"

    monkeypatch.setattr(attachment.file.storage, "url", fake_url)

    assert attachment.view_url == "https://example.invalid/signed"
    assert captured["parameters"] == {
        "ResponseContentDisposition": "inline",
        "ResponseContentType": "image/png",
    }


@pytest.mark.django_db
def test_viewable_attachment_link_opens_in_a_new_tab(client, staff_user, ticket):
    Attachment.objects.create(ticket=ticket, file=_upload(), filename="shot.png", content_type="image/png")
    Attachment.objects.create(
        ticket=ticket, file=_upload(name="notes.html", content_type="text/html"),
        filename="notes.html", content_type="text/html",
    )
    client.force_login(staff_user)

    html = client.get(f"/tickets/{ticket.reference_number}/").content.decode()

    assert 'target="_blank"' in html
    assert "opens in a new tab" in html
    # One viewable attachment, one that must still download.
    assert html.count('target="_blank" rel="noopener"') == 1


# ── Multi-file picking ───────────────────────────────────────────────────
#
# A native multiple file input replaces its whole selection on every pick, so
# "Up to 5 files" only worked if you ctrl-clicked them all at once. multifile.js
# accumulates instead; every page offering a multiple input must load it.


@pytest.mark.django_db
@pytest.mark.parametrize("surface", ["portal_submit", "portal_detail", "magic_link", "staff"])
def test_multiple_file_inputs_load_the_accumulator(
    surface, client, django_user_model, staff_user, ticket, source_app
):
    host = "testserver"
    if surface == "portal_submit":
        cache.clear()
        url, host = "/submit/", source_app.portal_domain
    elif surface == "portal_detail":
        user = django_user_model.objects.create_user(email="customer@example.com", password="x")
        TicketParticipant.objects.filter(ticket=ticket, email="customer@example.com").update(user=user)
        client.force_login(user)
        url, host = f"/portal/tickets/{ticket.reference_number}/", source_app.portal_domain
    elif surface == "magic_link":
        url = f"/ticket/{generate_ticket_access_token(ticket.pk, 'customer@example.com')}/"
    else:
        client.force_login(staff_user)
        url = f"/tickets/{ticket.reference_number}/"

    html = client.get(url, HTTP_HOST=host).content.decode()

    assert "multiple" in html
    assert "js/multifile.js" in html
