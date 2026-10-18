"""Log SES bounce and complaint events arriving on the SNS webhook.

SES's own feedback-forwarding mail and third-party ARF reports redact the
recipient, so a complaint arrives with no way to tell who reported it. The
SNS event carries the address, which is the whole reason this endpoint
exists — without a receiver the webhook would return 200 and drop it.

Suppression itself is handled account-side: SES's account-level suppression
list is enabled for BOUNCE and COMPLAINT, so a bounced or complaining
address is already blocked from future sends. These handlers are for
visibility, not enforcement.
"""

import logging

from django.dispatch import receiver
from django_ses.signals import bounce_received, complaint_received

log = logging.getLogger(__name__)


def _recipients(mail_obj, event_obj):
    """Recipient addresses, preferring the per-event list over the envelope."""
    for key in ("bouncedRecipients", "complainedRecipients"):
        entries = event_obj.get(key) or []
        if entries:
            return [e.get("emailAddress", "?") for e in entries]
    return (mail_obj or {}).get("destination", []) or ["?"]


@receiver(bounce_received)
def log_bounce(sender, mail_obj, bounce_obj, raw_message, *args, **kwargs):
    log.warning(
        "ses_bounce recipients=%s type=%s subtype=%s message_id=%s",
        ",".join(_recipients(mail_obj, bounce_obj)),
        bounce_obj.get("bounceType"),
        bounce_obj.get("bounceSubType"),
        (mail_obj or {}).get("messageId"),
    )


@receiver(complaint_received)
def log_complaint(sender, mail_obj, complaint_obj, raw_message, *args, **kwargs):
    log.warning(
        "ses_complaint recipients=%s feedback_type=%s message_id=%s",
        ",".join(_recipients(mail_obj, complaint_obj)),
        complaint_obj.get("complaintFeedbackType"),
        (mail_obj or {}).get("messageId"),
    )
