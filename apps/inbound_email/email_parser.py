import email
import re
from email import policy
from email.message import EmailMessage
from html import unescape

from django.utils.html import strip_tags

# Where the client stopped writing and started quoting the thread back at us.
# Anything from here down is history we already have on the ticket.
QUOTE_START_PATTERNS = [
    re.compile(r"^--$"),  # signature delimiter
    re.compile(r".*\bwrote:$", re.IGNORECASE),  # Gmail "On <date>, <name> wrote:"
    re.compile(r"^-{2,}\s*Original Message\s*-{2,}", re.IGNORECASE),  # Outlook desktop
    re.compile(r"^_{10,}$"),  # Outlook web reply separator
]

TICKET_REF_PATTERN = re.compile(r"\[HD-(\d+)\]", re.IGNORECASE)
TICKET_ADDR_PATTERN = re.compile(r"ticket\+(hd-\d+)@", re.IGNORECASE)
ADDR_PATTERN = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")

RECIPIENT_HEADERS = ("To", "Cc", "Delivered-To", "X-Original-To")
SPAM_VERDICT_HEADERS = ("X-SES-Spam-Verdict", "X-SES-Virus-Verdict")

# SES's own verdict on whether the message passed DMARC. The SPF and DKIM
# verdicts deliberately are not used here: SPF authenticates the envelope
# sender and DKIM authenticates whatever domain signed, so an attacker sending
# from a domain they control passes both while still forging `From:`. DMARC is
# the only one of the three that requires alignment with the `From:` header,
# which is the field this helpdesk identifies customers by.
DMARC_VERDICT_HEADER = "X-SES-DMARC-Verdict"

# RFC 3834 and the de-facto headers auto-responders set. Mail carrying any of
# these must never generate a ticket or a reply, or two robots talk forever.
AUTO_REPLY_PRECEDENCE = {"bulk", "auto_reply", "junk", "list"}


def parse_email(raw_email):
    if isinstance(raw_email, str):
        raw_email = raw_email.encode("utf-8")
    msg = email.message_from_bytes(raw_email, policy=policy.default)

    from_addr = msg.get("From", "")
    to_addr = msg.get("To", "")
    subject = msg.get("Subject", "")
    reply_to = msg.get("Reply-To", "")

    body = _extract_body(msg)
    attachments = extract_attachments(msg)

    references_header = msg.get("References", "")

    return {
        "from": from_addr,
        "to": to_addr,
        "reply_to": reply_to,
        "subject": subject,
        "body": body,
        "attachments": attachments,
        "message_id": msg.get("Message-ID", ""),
        "in_reply_to": msg.get("In-Reply-To", ""),
        "references": references_header.split() if references_header else [],
        "recipients": _extract_recipients(msg),
        "spam_flagged": _is_spam_flagged(msg),
        "auto_generated": _is_auto_generated(msg),
        "from_authenticated": _is_from_authenticated(msg),
        "bounced_recipients": _extract_bounced_recipients(msg),
    }


def _extract_bounced_recipients(msg):
    """Permanently-dead addresses named by a DSN's per-recipient groups (RFC 3464).

    Only `Action: failed` counts. `delayed` is a transient retry -- a full mailbox or
    greylisting -- and flagging on it would cry wolf about addresses that still work.
    """
    bounced = []
    for part in msg.walk():
        if part.get_content_type() != "message/delivery-status":
            continue
        groups = part.get_payload()
        if not isinstance(groups, list):
            continue
        for group in groups:
            if group.get("Action", "").strip().lower() != "failed":
                continue
            match = ADDR_PATTERN.search(group.get("Final-Recipient") or group.get("Original-Recipient") or "")
            if match:
                bounced.append(
                    {
                        "email": match.group(0).lower(),
                        "status": group.get("Status", "").strip(),
                        "diagnostic": group.get("Diagnostic-Code", "").strip(),
                    }
                )
    return bounced


def _is_auto_generated(msg):
    """True for vacation replies, bounces, and our own notifications coming back."""
    auto_submitted = msg.get("Auto-Submitted", "")
    if auto_submitted and auto_submitted.split(";")[0].strip().lower() != "no":
        return True
    if msg.get("X-Auto-Response-Suppress") or msg.get("X-Autoreply") or msg.get("X-Autorespond"):
        return True
    precedence = msg.get("Precedence", "").strip().lower()
    if precedence in AUTO_REPLY_PRECEDENCE:
        return True
    # Bounces (DSNs). SES's MAILER-DAEMON sets neither Auto-Submitted nor
    # Precedence, so these two are the only reliable signals:
    #   - RFC 3464 gives every DSN a multipart/report; report-type=delivery-status body
    #   - RFC 5321 requires a DSN's envelope sender be null, so it can never itself bounce
    if msg.get_content_type() == "multipart/report" and msg.get_param("report-type") == "delivery-status":
        return True
    if msg.get("Return-Path", "").strip() == "<>":
        return True
    return False


def _extract_recipients(msg):
    """Bare email addresses from To/Cc/Delivered-To/X-Original-To, deduped and lowercased."""
    seen = set()
    recipients = []
    for header in RECIPIENT_HEADERS:
        for value in msg.get_all(header, []):
            for addr in ADDR_PATTERN.findall(value):
                addr = addr.lower()
                if addr not in seen:
                    seen.add(addr)
                    recipients.append(addr)
    return recipients


def _is_spam_flagged(msg):
    for header in SPAM_VERDICT_HEADERS:
        value = msg.get(header)
        if value and value.strip().upper() != "PASS":
            return True
    return False


def _is_from_authenticated(msg):
    """True when DMARC passed, i.e. the `From:` domain really did send this.

    Anything else -- FAIL, GRAY (the domain publishes no DMARC policy),
    PROCESSING_FAILED, or a missing header -- means we cannot prove it, so
    callers must treat the sender as unverified.
    """
    return (msg.get(DMARC_VERDICT_HEADER) or "").strip().upper() == "PASS"


def _extract_body(msg):
    body = msg.get_body(preferencelist=("plain",))
    if body:
        content = body.get_content()
        return extract_reply_body(content)

    body = msg.get_body(preferencelist=("html",))
    if body:
        # HTML-only senders (Outlook web, most mobile clients) used to land here
        # with the raw markup of the entire quoted thread stored as the comment.
        return extract_reply_body(html_to_text(body.get_content()))

    return ""


def html_to_text(html):
    """Flatten an HTML mail part to plain text so quote stripping can run on it."""
    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", "", html)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|tr|li|h[1-6]|blockquote)>", "\n", text)
    text = strip_tags(text)
    text = unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


def extract_reply_body(text):
    lines = text.split("\n")
    result = []
    for line in lines:
        stripped = line.strip()
        # Stop at the start of the quoted thread the client appended
        if any(pattern.match(stripped) for pattern in QUOTE_START_PATTERNS):
            break
        # Skip quoted lines (some clients indent the ">")
        if stripped.startswith(">"):
            continue
        result.append(line)

    return "\n".join(result).strip()


def extract_attachments(msg):
    attachments = []
    if not isinstance(msg, EmailMessage):
        return attachments

    for part in msg.iter_attachments():
        filename = part.get_filename()
        if filename:
            attachments.append(
                {
                    "filename": filename,
                    "content_type": part.get_content_type(),
                    "data": part.get_content(),
                }
            )

    return attachments


def find_ticket_reference(subject, to_addresses):
    # Check subject for [HD-XXXX]
    match = TICKET_REF_PATTERN.search(subject)
    if match:
        num = int(match.group(1))
        return f"HD-{num:04d}"

    # Check to/reply-to addresses for helpdesk+hd-xxxx@
    if isinstance(to_addresses, str):
        to_addresses = [to_addresses]

    for addr in to_addresses:
        match = TICKET_ADDR_PATTERN.search(addr.lower())
        if match:
            return match.group(1).upper()

    return None
