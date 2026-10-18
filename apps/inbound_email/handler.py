import logging
import re

from django.conf import settings
from django.core.files.base import ContentFile

from apps.core.models import BouncedAddress, SourceApplication
from apps.tickets.models import Attachment, Comment, Ticket, TicketParticipant
from apps.tickets.services import TicketService

from .email_parser import find_ticket_reference

logger = logging.getLogger(__name__)

EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


def _save_attachments(ticket, attachments, comment=None):
    """Save parsed email attachments to the Attachment model (backed by S3 in production)."""
    for att in attachments:
        data = att["data"]
        if isinstance(data, str):
            data = data.encode("utf-8")
        attachment = Attachment(
            ticket=ticket,
            comment=comment,
            filename=att["filename"],
            content_type=att.get("content_type", "application/octet-stream"),
            size_bytes=len(data),
        )
        attachment.file.save(att["filename"], ContentFile(data), save=False)
        attachment.save()
        logger.info("Saved attachment %s (%d bytes) for ticket %s", att["filename"], len(data), ticket.reference_number)


def _record_bounces(bounced_recipients):
    for entry in bounced_recipients:
        BouncedAddress.objects.update_or_create(
            email=entry["email"],
            defaults={"status": entry["status"], "diagnostic": entry["diagnostic"]},
        )
        logger.warning("Address %s is bouncing (%s)", entry["email"], entry["status"] or "no status")


def _own_domains():
    domains = set(SourceApplication.objects.exclude(mail_domain="").values_list("mail_domain", flat=True))
    domains.add(getattr(settings, "DEFAULT_EMAIL_DOMAIN", ""))
    domains.discard("")
    return {d.lower() for d in domains}


def _is_own_address(sender_email):
    """True if the sender is this helpdesk — our own notification coming back at us."""
    local, _, domain = sender_email.lower().rpartition("@")
    if not domain:
        return False
    if local.startswith("ticket+"):
        return True
    return domain in _own_domains()


def _add_cc_participants(ticket, recipients, sender_email):
    """Record everyone else the customer copied, so their reply-all appends here.

    Without this, a CC'd colleague replying is an unknown sender and forks a
    duplicate ticket.
    """
    # Our own domains, plus the apex domains that forward into them -- otherwise
    # support@example.com (the mailbox address the customer actually wrote to) is
    # recorded as a participant and every later reply is addressed back into the
    # forward, round-tripping our own mail through SES.
    own = _own_domains() | set(_inbound_domain_map())
    for addr in recipients:
        if addr.lower() == sender_email.lower():
            continue
        if "@" not in addr or addr.split("@", 1)[1].lower() in own:
            continue
        TicketParticipant.objects.get_or_create(
            ticket=ticket,
            email=addr,
            defaults={"role": TicketParticipant.Role.CC},
        )


def _inbound_domain_map():
    """Domain -> active SourceApplication, covering `mail.<domain>` and `<domain>`.

    The apex entry is what makes forwarded mail work. support@example.com is a
    mailbox at a regular mail host forwarding to support@mail.example.com; SES
    receives it, but the forwarded copy still carries `To: support@example.com`
    and many forwarders add no Delivered-To, so matching mail_domain alone left every forwarded ticket
    unattributed -- no product branding, no portal scope, generic reply address.
    """
    mapping = {}
    for app in SourceApplication.objects.exclude(mail_domain="").filter(is_active=True):
        domain = app.mail_domain.lower()
        mapping.setdefault(domain, app)
        if domain.startswith("mail."):
            mapping.setdefault(domain[len("mail.") :], app)
    return mapping


def _resolve_source_application(recipients):
    """Match a recipient's domain (after @) to an active SourceApplication."""
    mapping = _inbound_domain_map()
    for addr in recipients:
        if "@" not in addr:
            continue
        app = mapping.get(addr.split("@", 1)[1].lower())
        if app is not None:
            return app
    return None


def process_inbound_email(parsed_email):
    subject = parsed_email["subject"]
    body = parsed_email["body"]
    from_addr = parsed_email["from"]
    to_addr = parsed_email["to"]
    reply_to = parsed_email.get("reply_to", "")
    attachments = parsed_email.get("attachments", [])
    message_id = parsed_email.get("message_id", "")
    references = parsed_email.get("references", [])
    in_reply_to = parsed_email.get("in_reply_to", "")
    recipients = parsed_email.get("recipients", [])

    # Extract plain email address from "Name <email>" format
    sender_email = _extract_email(from_addr)

    # Loop guards. Without these, our own auto-ack (or a customer's vacation
    # responder) becomes a ticket, which sends an auto-ack, which becomes a
    # ticket... Applies to replies too: a bot must not append comments either.
    if parsed_email.get("auto_generated"):
        # Drop it, but not before harvesting who it says is unreachable -- otherwise a
        # bounce is silent and an agent keeps replying into a dead mailbox.
        _record_bounces(parsed_email.get("bounced_recipients", []))
        logger.info("Dropping auto-generated inbound email from %s", sender_email)
        return None
    if _is_own_address(sender_email):
        logger.warning("Dropping inbound email sent from our own address %s", sender_email)
        return None

    # Dedupe: we've already recorded a comment for this Message-ID
    if message_id:
        existing = Comment.objects.filter(email_message_id=message_id).select_related("ticket").first()
        if existing:
            logger.info("Duplicate inbound Message-ID %s, skipping", message_id)
            return existing.ticket

    to_addresses = [to_addr]
    if reply_to:
        to_addresses.append(reply_to)

    # In-Reply-To is the most reliable threading header and was previously parsed
    # and thrown away.
    thread_ids = [mid for mid in [*references, in_reply_to] if mid]

    reference = find_ticket_reference(subject, to_addresses)
    ticket = None
    threaded = False
    if reference:
        ticket = Ticket.objects.filter(reference_number=reference).first()
        if not ticket:
            logger.warning("Ticket %s not found for inbound email, creating new ticket", reference)
    elif thread_ids:
        # Threading fallback: this email replies to something we sent, by Message-ID
        parent_comment = Comment.objects.filter(email_message_id__in=thread_ids).select_related("ticket").first()
        if parent_comment:
            ticket, threaded = parent_comment.ticket, True
        else:
            ticket = Ticket.objects.filter(email_message_id__in=thread_ids).first()
            threaded = ticket is not None

    # A reference number or a ticket+hd-NNNN@ address is guessable -- they are
    # sequential -- and `From:` is just a header, so the pair alone lets anyone
    # post as the customer on a ticket they picked. Require DMARC to have proved
    # the From: domain before trusting that combination. Message-ID threading is
    # exempt: we generated that id, so only someone who received the mail has it.
    if ticket and not threaded and not parsed_email.get("from_authenticated"):
        logger.warning(
            "Unauthenticated sender %s matched %s by reference only; creating new ticket instead",
            sender_email,
            ticket.reference_number,
        )
        return _create_new_ticket(subject, body, sender_email, attachments, recipients)

    if ticket:
        if not ticket.participants.filter(email__iexact=sender_email).exists():
            # A subject ref alone proves nothing -- reference numbers are sequential
            # and guessable. But a Message-ID we generated proves this sender was
            # actually on the thread, so a CC'd colleague replying-all belongs here
            # rather than in a brand new ticket.
            if not threaded:
                logger.warning(
                    "Sender %s is not a participant on %s; creating new ticket instead of appending comment",
                    sender_email,
                    ticket.reference_number,
                )
                return _create_new_ticket(subject, body, sender_email, attachments, recipients)
            TicketParticipant.objects.get_or_create(
                ticket=ticket,
                email=sender_email,
                defaults={"role": TicketParticipant.Role.CC},
            )

        comment = TicketService.add_comment(
            ticket=ticket,
            body=body,
            author_email=sender_email,
            comment_type=Comment.CommentType.PUBLIC,
        )
        if message_id:
            comment.email_message_id = message_id
            comment.save(update_fields=["email_message_id"])
        if attachments:
            _save_attachments(ticket, attachments, comment=comment)
        logger.info("Added comment to ticket %s from %s", ticket.reference_number, sender_email)
        return ticket

    return _create_new_ticket(subject, body, sender_email, attachments, recipients)


def _create_new_ticket(subject, body, sender_email, attachments=None, recipients=None):
    # Strip Re:/Fwd: prefixes for cleaner subject
    clean_subject = re.sub(r"^(Re|Fwd|Fw):\s*", "", subject, flags=re.IGNORECASE).strip()
    if not clean_subject:
        clean_subject = "(No Subject)"

    source_application = _resolve_source_application(recipients or [])

    ticket = TicketService.create_ticket(
        subject=clean_subject,
        body=body,
        submitter_email=sender_email,
        source_application=source_application,
    )
    _add_cc_participants(ticket, recipients or [], sender_email)
    if attachments:
        _save_attachments(ticket, attachments)
    logger.info("Created new ticket %s from %s", ticket.reference_number, sender_email)
    return ticket


def _extract_email(from_header):
    match = EMAIL_PATTERN.search(from_header)
    if match:
        return match.group(0)
    return from_header
