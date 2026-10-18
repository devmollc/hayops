from urllib.parse import urlparse

from corsheaders.signals import check_request_enabled

from apps.core.models import SourceApplication


def domain_matches(domain, allowed):
    """True if `domain` equals `allowed`, or `allowed` is a "*.example.com"
    wildcard covering it."""
    if domain == allowed:
        return True
    if allowed.startswith("*.") and domain.endswith(allowed[1:]):
        return True
    return False


def check_cors_origin(sender, request, **kwargs):
    origin = request.META.get("HTTP_ORIGIN", "")
    parsed = urlparse(origin)
    domain = parsed.hostname
    if not domain:
        return False

    domain_lists = SourceApplication.objects.filter(is_active=True).values_list("allowed_domains", flat=True)
    for allowed_domains in domain_lists:
        if any(domain_matches(domain, allowed) for allowed in allowed_domains):
            return True

    return False


check_request_enabled.connect(check_cors_origin)
