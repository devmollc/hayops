import hashlib
import hmac
import json
import logging
import urllib.error
import urllib.request

from django.core.exceptions import ValidationError
from django.utils import timezone
from procrastinate.contrib.django import app

from apps.tickets.models import TicketEvent

from .models import WebhookDelivery, WebhookEndpoint, validate_public_destination

logger = logging.getLogger(__name__)

RETRY_DELAYS = [60, 300, 900, 3600, 7200]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse redirects. validate_public_destination checks only the configured
    URL, so following a 3xx would let a public host bounce the delivery to an
    internal address. The 3xx surfaces as an HTTPError and counts as a failure."""

    def redirect_request(self, *args, **kwargs):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def _build_payload(event):
    return {
        "event_type": event.event_type,
        "ticket": {
            "id": event.ticket_id,
            "reference_number": event.ticket.reference_number,
            "subject": event.ticket.subject,
            "status": event.ticket.status,
            "priority": event.ticket.priority,
        },
        "actor": event.actor.email if event.actor else None,
        "old_value": event.old_value,
        "new_value": event.new_value,
        "metadata": event.metadata,
        "timestamp": event.created_at.isoformat(),
    }


@app.task
def dispatch_webhook(event_id):
    try:
        event = TicketEvent.objects.select_related("ticket", "actor").get(pk=event_id)
    except TicketEvent.DoesNotExist:
        logger.warning("TicketEvent %s not found for webhook dispatch", event_id)
        return

    endpoints = WebhookEndpoint.objects.filter(
        is_active=True,
        source_application=event.ticket.source_application,
    )

    payload = _build_payload(event)

    for endpoint in endpoints:
        if event.event_type in endpoint.events:
            delivery = WebhookDelivery.objects.create(
                endpoint=endpoint,
                event_type=event.event_type,
                payload=payload,
            )
            deliver_webhook.defer(delivery_id=delivery.pk)


@app.task(retry=5)
def deliver_webhook(delivery_id):
    try:
        delivery = WebhookDelivery.objects.select_related("endpoint").get(
            pk=delivery_id
        )
    except WebhookDelivery.DoesNotExist:
        logger.warning("WebhookDelivery %s not found", delivery_id)
        return

    endpoint = delivery.endpoint

    # Re-checked here, not just in the form: rows predating that validation, and
    # any written straight to the DB, must not be allowed to reach an internal host.
    try:
        validate_public_destination(endpoint.url)
    except ValidationError as exc:
        delivery.attempts += 1
        delivery.response_body = "; ".join(exc.messages)[:2000]
        delivery.save()
        logger.error("Refusing webhook delivery %s to %s: %s", delivery_id, endpoint.url, delivery.response_body)
        return

    payload_json = json.dumps(delivery.payload, default=str)

    signature = hmac.new(
        endpoint.secret.encode("utf-8"),
        payload_json.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    delivery.attempts += 1

    try:
        req = urllib.request.Request(
            endpoint.url,
            data=payload_json.encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-Helpdesk-Signature": signature,
            },
            method="POST",
        )
        with _opener.open(req, timeout=10) as response:
            delivery.response_status = response.status
            delivery.response_body = response.read().decode("utf-8", errors="replace")[:2000]

        if 200 <= delivery.response_status < 300:
            delivery.is_success = True
            delivery.delivered_at = timezone.now()
        else:
            _handle_failure(delivery)

    except urllib.error.HTTPError as exc:
        delivery.response_status = exc.code
        delivery.response_body = str(exc)[:2000]
        _handle_failure(delivery)
    except (urllib.error.URLError, OSError) as exc:
        delivery.response_body = str(exc)[:2000]
        _handle_failure(delivery)

    delivery.save()


def _handle_failure(delivery):
    attempt_index = delivery.attempts - 1
    if attempt_index < len(RETRY_DELAYS):
        delay = RETRY_DELAYS[attempt_index]
        deliver_webhook.configure(schedule_in={"seconds": delay}).defer(
            delivery_id=delivery.pk
        )
