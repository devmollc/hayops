import ipaddress
import socket
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from django.db import models

from apps.core.models import TimestampedModel

DEFAULT_PORTS = {"http": 80, "https": 443}


def validate_public_destination(url):
    """Reject anything that is not an http(s) URL resolving to a public address.

    The worker POSTs the whole ticket payload to this URL, so an internal
    destination turns the delivery queue into an SSRF proxy against the Docker
    network and the cloud metadata endpoint.

    # ponytail: checked at save and again at delivery, not pinned to the
    # resolved IP for the actual connection -- a DNS rebind between the two
    # still gets through. Pin the socket (custom opener resolving once) if the
    # destination list ever stops being staff-only.
    """
    parts = urlsplit(url)
    if parts.scheme not in DEFAULT_PORTS:
        raise ValidationError("Webhook URL must use http or https.")
    host = parts.hostname
    if not host:
        raise ValidationError("Webhook URL has no host.")
    try:
        infos = socket.getaddrinfo(
            host, parts.port or DEFAULT_PORTS[parts.scheme], proto=socket.IPPROTO_TCP
        )
    except (socket.gaierror, ValueError) as exc:
        raise ValidationError(f"Webhook host {host} does not resolve.") from exc
    for info in infos:
        # is_global is the one check that covers loopback, private ranges,
        # link-local (169.254.169.254) and the reserved blocks in one go.
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise ValidationError(f"Webhook host {host} resolves to the non-public address {ip}.")


class WebhookEndpoint(TimestampedModel):
    source_application = models.ForeignKey(
        "core.SourceApplication",
        on_delete=models.CASCADE,
        related_name="webhook_endpoints",
    )
    url = models.URLField()
    events = models.JSONField(
        default=list, help_text="List of event types to subscribe to"
    )
    secret = models.CharField(max_length=64)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.source_application} -> {self.url}"


class WebhookDelivery(TimestampedModel):
    endpoint = models.ForeignKey(
        WebhookEndpoint, on_delete=models.CASCADE, related_name="deliveries"
    )
    event_type = models.CharField(max_length=30)
    payload = models.JSONField()
    response_status = models.IntegerField(null=True, blank=True)
    response_body = models.TextField(blank=True)
    attempts = models.IntegerField(default=0)
    delivered_at = models.DateTimeField(null=True, blank=True)
    is_success = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "Webhook deliveries"

    def __str__(self):
        return f"{self.event_type} -> {self.endpoint.url} ({'OK' if self.is_success else 'pending'})"
