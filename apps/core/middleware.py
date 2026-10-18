import logging

from django.conf import settings
from django.core.cache import cache
from django.http import Http404

logger = logging.getLogger(__name__)

CACHE_KEY_PREFIX = "source_app_domain:"
CACHE_TIMEOUT = 300  # 5 minutes

# Path prefixes reachable on a branded portal host. "/" is matched exactly, separately.
PORTAL_ALLOWED_PREFIXES = ("/submit", "/portal/", "/kb/", "/ticket/", "/csat/", "/static/", "/media/", "/ht/")


class SourceApplicationMiddleware:
    """Resolve the incoming hostname to a SourceApplication.

    Sets ``request.source_application`` to the matching active
    SourceApplication, or ``None`` for the admin domain.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        hostname = request.get_host().split(":")[0].lower()
        admin_domain = settings.ADMIN_DOMAIN

        if hostname == admin_domain:
            request.source_application = None
        else:
            request.source_application = self._resolve(hostname)

        if request.source_application is not None:
            path = request.path
            if path != "/" and not path.startswith(PORTAL_ALLOWED_PREFIXES):
                raise Http404("Not available on this domain.")

        return self.get_response(request)

    @staticmethod
    def _resolve(hostname):
        cache_key = f"{CACHE_KEY_PREFIX}{hostname}"
        result = cache.get(cache_key)
        if result is not None:
            # We store False as a sentinel for "no match"
            return result if result is not False else None

        from apps.core.models import SourceApplication

        try:
            app = SourceApplication.objects.get(portal_domain=hostname, is_active=True)
            cache.set(cache_key, app, CACHE_TIMEOUT)
            return app
        except SourceApplication.DoesNotExist:
            cache.set(cache_key, False, CACHE_TIMEOUT)
            return None
