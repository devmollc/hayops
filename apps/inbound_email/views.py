import json
import logging
import re
import urllib.request
from urllib.parse import urlsplit

from django.conf import settings
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django_ses.utils import SES_REGEX_CERT_URL, verify_event_message

from .tasks import process_ses_inbound

logger = logging.getLogger(__name__)

# The only hosts that can be an SNS endpoint. SES_REGEX_CERT_URL (django-ses)
# is the same shape plus the .pem path, and covers the signing certificate.
SNS_HOST_PATTERN = re.compile(r"^sns\.[a-z0-9-]+\.amazonaws\.com(\.cn)?$", re.IGNORECASE)


def _is_sns_url(url):
    parts = urlsplit(url or "")
    return parts.scheme == "https" and bool(SNS_HOST_PATTERN.match(parts.hostname or ""))


def _verify_sns_message(body):
    """Cryptographically verify the SNS signature on `body`.

    django-ses already does this for /ses/events/; reusing its verifier means
    one implementation of SHA1withRSA-over-the-signed-fields rather than the
    host-substring check that used to live here (which `https://sns.evil.tld/
    x.amazonaws.com` walked straight through).
    """
    cert_url = body.get("SigningCertURL", "")
    if not SES_REGEX_CERT_URL.match(cert_url):
        logger.warning("Rejecting SNS message with untrusted certificate URL: %s", cert_url)
        return False
    try:
        return bool(verify_event_message(body))
    except Exception:
        logger.exception("Error verifying SNS message signature")
        return False


@method_decorator(csrf_exempt, name="dispatch")
class SESInboundWebhookView(View):
    """Receive SNS notifications for SES inbound emails.

    Architecture: SES -> S3 (incoming/ prefix) -> SNS -> this webhook -> Procrastinate task
    """

    def post(self, request):
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return HttpResponseBadRequest("Invalid JSON")

        # Fail closed. An unset topic ARN used to mean "accept any topic", which
        # made a misconfigured environment silently open rather than broken.
        expected_topic = getattr(settings, "SES_INBOUND_TOPIC_ARN", "")
        if not expected_topic:
            logger.error("SES_INBOUND_TOPIC_ARN is unset; refusing SNS message")
            return HttpResponseForbidden("Inbound email not configured")
        if body.get("TopicArn", "") != expected_topic:
            logger.warning(
                "SNS topic mismatch: expected=%s got=%s", expected_topic, body.get("TopicArn", "")
            )
            return HttpResponseForbidden("Invalid topic")

        if not _verify_sns_message(body):
            return HttpResponseForbidden("Verification failed")

        # Dispatch on the signed Type field, not the x-amz-sns-message-type
        # header -- the header is outside the signature and can disagree with it.
        message_type = body.get("Type", "").lower()

        if message_type == "subscriptionconfirmation":
            return self._handle_subscription(body)
        elif message_type == "notification":
            return self._handle_notification(body)
        else:
            logger.warning("Unknown SNS message type: %s", message_type)
            return HttpResponseBadRequest("Unknown message type")

    def _handle_subscription(self, body):
        """Auto-confirm SNS subscription."""
        subscribe_url = body.get("SubscribeURL")
        if not subscribe_url:
            return HttpResponse("OK", status=200)
        # Signed, so this is already AWS's own URL -- but an outbound fetch of a
        # caller-supplied URL is worth a second lock.
        if not _is_sns_url(subscribe_url):
            logger.warning("Refusing to fetch non-SNS SubscribeURL: %s", subscribe_url)
            return HttpResponseForbidden("Invalid SubscribeURL")
        try:
            urllib.request.urlopen(subscribe_url, timeout=10)  # noqa: S310
            logger.info("Confirmed SNS subscription for topic %s", body.get("TopicArn"))
        except Exception:
            logger.exception("Failed to confirm SNS subscription")
            return HttpResponseBadRequest("Subscription confirmation failed")
        return HttpResponse("OK", status=200)

    def _handle_notification(self, body):
        """Process SES notification — extract S3 info and defer task."""
        try:
            message = json.loads(body.get("Message", "{}"))
        except (json.JSONDecodeError, ValueError):
            logger.error("Invalid Message JSON in SNS notification")
            return HttpResponseBadRequest("Invalid message")

        # SES notifications via S3 action contain receipt info
        receipt = message.get("receipt", {})
        action = receipt.get("action", {})

        bucket = action.get("bucketName", "")
        key = action.get("objectKey", "")

        if not bucket or not key:
            # Try direct S3 event format
            for record in message.get("Records", []):
                s3_info = record.get("s3", {})
                bucket = s3_info.get("bucket", {}).get("name", "")
                key = s3_info.get("object", {}).get("key", "")
                if bucket and key:
                    break

        if not bucket or not key:
            logger.warning("No S3 bucket/key found in SNS notification")
            return HttpResponse("OK", status=200)

        expected_bucket = getattr(settings, "SES_INBOUND_BUCKET", "")
        if not expected_bucket or bucket != expected_bucket or not key.startswith("incoming/"):
            logger.warning("Rejecting SNS notification for s3://%s/%s (bucket/key guard)", bucket, key)
            return HttpResponse("OK", status=200)

        # Defer processing to background task
        process_ses_inbound.defer(bucket=bucket, key=key)
        logger.info("Deferred SES inbound processing for s3://%s/%s", bucket, key)

        return HttpResponse("OK", status=200)
