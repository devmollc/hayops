from django.conf import settings
from django.core.cache import cache
from rest_framework.throttling import ScopedRateThrottle


def client_ip(request):
    """The requesting client's IP, for rate limiting.

    Behind a reverse proxy REMOTE_ADDR is the proxy, so every visitor would share
    one limit. CLIENT_IP_HEADER names the header the edge proxy sets to the real
    client address (HTTP_CF_CONNECTING_IP behind Cloudflare, HTTP_X_REAL_IP behind
    nginx). Only set it when the origin is unreachable except through that proxy:
    a client that can reach the app directly can put any value in any header.
    """
    header = settings.CLIENT_IP_HEADER
    return (header and request.META.get(header)) or request.META.get("REMOTE_ADDR", "unknown")


def rate_limited(key, limit, window):
    """Count one hit against `key`; True once `limit` hits land within `window` seconds."""
    count = cache.get_or_set(key, 0, window)
    if count >= limit:
        return True
    cache.incr(key)
    return False


class ClientIPScopedRateThrottle(ScopedRateThrottle):
    """DRF's scoped throttle, keyed on client_ip() instead of raw X-Forwarded-For."""

    def get_ident(self, request):
        return client_ip(request)
