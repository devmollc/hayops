import logging
import uuid

import boto3
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.html import strip_tags
from procrastinate.contrib.django import app

from apps.accounts.models import MagicLoginToken
from apps.tickets.csat_views import generate_csat_token
from apps.tickets.models import Comment, Ticket

from .email_parser import parse_email
from .handler import process_inbound_email

logger = logging.getLogger(__name__)

# Marks our mail as machine-generated (RFC 3834) so vacation responders stay
# quiet and our own inbound loop guard can recognise it.
AUTO_REPLY_HEADERS = {
    "Auto-Submitted": "auto-replied",
    "X-Auto-Response-Suppress": "All",
}

TEAM_NOTIFICATION_TYPES = {"team_new_ticket", "team_reply"}

CUSTOMER_TEMPLATE_MAP = {
    "created": "inbound_email/ticket_created.html",
    "reply": "inbound_email/ticket_reply.html",
    "status_changed": "inbound_email/status_changed.html",
    "csat_survey": "inbound_email/csat_survey.html",
}


def _get_from_email(ticket):
    if ticket.source_application and ticket.source_application.mail_domain:
        domain = ticket.source_application.mail_domain
    else:
        domain = settings.DEFAULT_EMAIL_DOMAIN
    return f"ticket+{ticket.reference_number.lower()}@{domain}"


def _portal_base_url(source_app):
    if source_app and source_app.portal_domain:
        return f"https://{source_app.portal_domain}"
    return getattr(settings, "SITE_URL", "http://localhost:8000")


def _branding_context(source_app):
    return {
        "branding_name": source_app.branding_name if source_app else "HayOps",
        "primary_color": source_app.primary_color if source_app else "#2563eb",
        "logo_url": source_app.logo.url if source_app and source_app.logo else "",
        "email_signature": source_app.email_signature if source_app else "",
    }


def customer_recipients(ticket, exclude_email=""):
    """Customer-side addresses to notify: every participant who isn't one of our agents.

    Two filters, not one: add_comment() records the replying agent as a participant
    with user=<agent>, but participants created before that user existed have
    user=NULL, so the FK check alone would mail agents their own replies.
    """
    from apps.accounts.models import User

    staff_emails = {e.lower() for e in User.objects.filter(is_staff=True).values_list("email", flat=True)}
    skip = staff_emails | ({exclude_email.lower()} if exclude_email else set())
    return [
        p.email
        for p in ticket.participants.select_related("user").all()
        if not (p.user and p.user.is_staff) and p.email.lower() not in skip
    ]


def _thread_headers(ticket, message_id):
    """Message-ID plus the In-Reply-To/References chain for this ticket's mail thread.

    Django generates a random Message-ID when we don't supply one, so outbound mail
    has always had an id -- it was just never recorded, leaving replies that thread
    only by References unmatched, and every notification starting a fresh thread in
    the customer's client.
    """
    headers = dict(AUTO_REPLY_HEADERS, **{"Message-ID": message_id})
    chain = []
    if ticket.email_message_id:
        chain.append(ticket.email_message_id)
    chain += list(
        ticket.comments.exclude(email_message_id="")
        .order_by("created_at")
        .values_list("email_message_id", flat=True)
    )
    if chain:
        headers["In-Reply-To"] = chain[-1]
        headers["References"] = " ".join(chain[-10:])
    return headers


@app.task
def send_ticket_notification(ticket_id, notification_type, comment_id=None):
    try:
        ticket = Ticket.objects.select_related("queue", "assigned_to", "source_application").get(pk=ticket_id)
    except Ticket.DoesNotExist:
        logger.error("Ticket %s not found for notification", ticket_id)
        return

    if notification_type in TEAM_NOTIFICATION_TYPES:
        _send_team_notification(ticket, notification_type)
        return

    template_name = CUSTOMER_TEMPLATE_MAP.get(notification_type)
    if not template_name:
        logger.error("Unknown notification type: %s", notification_type)
        return

    comment = Comment.objects.filter(pk=comment_id).select_related("author").first() if comment_id else None

    if notification_type == "csat_survey":
        # CSATRating holds a single rated_by_email, so the survey stays submitter-only.
        submitter = ticket.submitter
        recipients = [submitter.email] if submitter else []
    else:
        recipients = customer_recipients(ticket, exclude_email=comment.author_email if comment else "")

    if not recipients:
        logger.warning("No customer recipients for ticket %s (%s)", ticket.reference_number, notification_type)
        return

    ref = ticket.reference_number
    source_app = ticket.source_application
    from_email = _get_from_email(ticket)
    base_url = _portal_base_url(source_app)
    subject_map = {
        "created": f"[{ref}] We received your request: {ticket.subject}",
        "reply": f"[{ref}] New reply on: {ticket.subject}",
        "status_changed": f"[{ref}] Status updated: {ticket.subject}",
        "csat_survey": f"[{ref}] How did we do? Rate your experience",
    }
    ticket_path = reverse("portal:ticket_detail", kwargs={"reference": ref})
    message_id = f"<{ref.lower()}.{uuid.uuid4().hex}@{from_email.split('@', 1)[1]}>"
    headers = _thread_headers(ticket, message_id)

    for recipient_email in recipients:
        context = {"ticket": ticket, "ref": ref, "comment": comment, **_branding_context(source_app)}

        if notification_type in ("created", "reply"):
            # One-time sign-in link straight to this ticket. Long-lived because the
            # mail may not be read for days; single-use is what bounds the exposure.
            _, raw_token = MagicLoginToken.issue(
                email=recipient_email,
                source_application=source_app,
                next_url=ticket_path,
                ttl_minutes=getattr(settings, "MAGIC_LINK_EMAIL_TTL_MINUTES", 60 * 24 * 7),
            )
            context["login_url"] = f"{base_url}{reverse('portal:login_verify', kwargs={'token': raw_token})}"

        if notification_type == "csat_survey":
            token = generate_csat_token(ticket.pk, recipient_email)
            context["rating_links"] = {
                rating: f"{base_url}{reverse('csat_rate_direct', kwargs={'token': token, 'rating': rating})}"
                for rating in range(1, 6)
            }

        html_body = render_to_string(template_name, context)

        email = EmailMultiAlternatives(
            subject=subject_map[notification_type],
            body=strip_tags(html_body),
            from_email=from_email,
            to=[recipient_email],
            headers=headers,
        )
        email.attach_alternative(html_body, "text/html")
        email.send()
        logger.info("Sent %s notification for %s to %s", notification_type, ref, recipient_email)

    # Record the id we threaded under so inbound replies carrying only
    # In-Reply-To/References can be matched back to this ticket.
    if notification_type == "created" and not ticket.email_message_id:
        ticket.email_message_id = message_id
        ticket.save(update_fields=["email_message_id"])
    elif comment is not None and not comment.email_message_id:
        comment.email_message_id = message_id
        comment.save(update_fields=["email_message_id"])


def _send_team_notification(ticket, notification_type):
    recipient = getattr(settings, "SUPPORT_NOTIFY_EMAIL", "")
    if not recipient:
        return

    ref = ticket.reference_number
    detail_url = f"https://{settings.ADMIN_DOMAIN}{reverse('tickets:detail', kwargs={'reference': ref})}"
    subject_map = {
        "team_new_ticket": f"[{ref}] New ticket: {ticket.subject}",
        "team_reply": f"[{ref}] Customer replied: {ticket.subject}",
    }
    body = render_to_string(
        f"inbound_email/{notification_type}.txt",
        {
            "ticket": ticket,
            "ref": ref,
            "detail_url": detail_url,
        },
    )

    team_email = EmailMultiAlternatives(
        subject=subject_map[notification_type],
        body=body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[recipient],
        headers=AUTO_REPLY_HEADERS,
    )
    team_email.send()
    logger.info("Sent %s notification for %s to %s", notification_type, ref, recipient)


@app.task
def send_magic_login(email, source_application_id=None, next_url="", requested_ip=""):
    """Mail a one-time portal sign-in link.

    The token is minted here rather than in the view so the raw secret never lands
    in the procrastinate jobs table.
    """
    from apps.core.models import SourceApplication

    source_app = SourceApplication.objects.filter(pk=source_application_id).first() if source_application_id else None
    _, raw_token = MagicLoginToken.issue(
        email=email,
        source_application=source_app,
        next_url=next_url,
        requested_ip=requested_ip,
    )

    base_url = _portal_base_url(source_app)
    context = {
        "login_url": f"{base_url}{reverse('portal:login_verify', kwargs={'token': raw_token})}",
        "ttl_minutes": getattr(settings, "MAGIC_LINK_TTL_MINUTES", 30),
        **_branding_context(source_app),
    }
    html_body = render_to_string("inbound_email/magic_login.html", context)

    message = EmailMultiAlternatives(
        subject=f"Sign in to {context['branding_name']} support",
        body=strip_tags(html_body),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[email],
        headers=AUTO_REPLY_HEADERS,
    )
    message.attach_alternative(html_body, "text/html")
    message.send()
    logger.info("Sent magic login link to %s", email)


def _get_s3_client():
    """Get boto3 S3 client for SES inbound email (AWS S3, not R2).

    Uses the default boto3 credential chain (AWS_ACCESS_KEY_ID env var, IAM role, etc.)
    rather than Django settings, which point to R2.
    """
    return boto3.client("s3", region_name=getattr(settings, "SES_AWS_REGION", "us-east-1"))


def _move_s3_object(s3_client, bucket, src_key, dst_prefix):
    """Move an S3 object from src_key to dst_prefix, preserving the filename."""
    filename = src_key.rsplit("/", 1)[-1]
    dst_key = f"{dst_prefix}{filename}"
    s3_client.copy_object(Bucket=bucket, CopySource={"Bucket": bucket, "Key": src_key}, Key=dst_key)
    s3_client.delete_object(Bucket=bucket, Key=src_key)
    return dst_key


@app.task
def process_ses_inbound(bucket, key):
    """Fetch raw email from S3 and process it through the inbound pipeline."""
    s3 = _get_s3_client()
    try:
        response = s3.get_object(Bucket=bucket, Key=key)
        raw_email = response["Body"].read()
    except Exception:
        logger.exception("Failed to fetch email from s3://%s/%s", bucket, key)
        return

    try:
        parsed = parse_email(raw_email)
        if parsed.get("spam_flagged"):
            logger.warning("Inbound email flagged as spam/virus, skipping: s3://%s/%s", bucket, key)
            _move_s3_object(s3, bucket, key, "failed/")
            return
        process_inbound_email(parsed)
        _move_s3_object(s3, bucket, key, "processed/")
        logger.info("Processed inbound email from s3://%s/%s", bucket, key)
    except Exception:
        logger.exception("Failed to process inbound email from s3://%s/%s", bucket, key)
        try:
            _move_s3_object(s3, bucket, key, "failed/")
        except Exception:
            logger.exception("Failed to move email to failed/ prefix")


@app.periodic(cron="*/5 * * * *")
@app.task
def sweep_ses_inbound(timestamp=None):
    """Scan incoming/ for objects older than 5 minutes (missed SNS notifications)."""
    bucket = getattr(settings, "SES_INBOUND_BUCKET", "")
    if not bucket:
        return

    s3 = _get_s3_client()
    cutoff = timezone.now() - timezone.timedelta(minutes=5)

    try:
        response = s3.list_objects_v2(Bucket=bucket, Prefix="incoming/")
    except Exception:
        logger.exception("Failed to list incoming/ objects in bucket %s", bucket)
        return

    for obj in response.get("Contents", []):
        key = obj["Key"]
        last_modified = obj["LastModified"]

        # Skip the prefix itself
        if key == "incoming/":
            continue

        if last_modified.replace(tzinfo=None) < cutoff.replace(tzinfo=None):
            logger.info("Sweeping missed inbound email: s3://%s/%s", bucket, key)
            process_ses_inbound.defer(bucket=bucket, key=key)
