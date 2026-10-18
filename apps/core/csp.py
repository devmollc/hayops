from urllib.parse import urlsplit

from django.conf import settings

# The script hosts the templates actually load from: htmx and Alpine in
# base.html, Chart.js in reporting/dashboard.html. Keep this in step with them.
SCRIPT_HOSTS = ("https://unpkg.com", "https://cdn.jsdelivr.net")

# base_auth.html pulls Google Fonts.
STYLE_HOSTS = ("https://fonts.googleapis.com",)
FONT_HOSTS = ("https://fonts.gstatic.com",)


def _media_hosts():
    """Hosts a branding logo can be served from.

    django-storages uses R2_PUBLIC_HOST as the public bucket's custom_domain,
    but falls back to building the URL against R2_ENDPOINT_URL when that is
    unset -- as it is on the test environment. Allowing both means the policy
    does not depend on which of the two an environment happens to configure.
    """
    hosts = []
    public_host = getattr(settings, "R2_PUBLIC_HOST", "")
    if public_host:
        hosts.append(f"https://{public_host}")
    endpoint = urlsplit(getattr(settings, "R2_ENDPOINT_URL", "") or "")
    if endpoint.hostname:
        hosts.append(f"https://{endpoint.hostname}")
    return hosts


def build_policy():
    # Branding logos live in the public R2 bucket; attachments are
    # Content-Disposition: attachment and never rendered as <img>.
    img_src = ["'self'", "data:", *_media_hosts()]
    # Umami tracker on portal KB pages: loads p.js and posts to /api/p.
    umami = [settings.UMAMI_HOST] if getattr(settings, "UMAMI_HOST", "") else []

    directives = {
        "default-src": ["'self'"],
        # No 'unsafe-inline': every script is a file under static/, so a
        # reflected <script> or an injected onclick= does not run. 'unsafe-eval'
        # is Alpine, which compiles its x-* expressions with new Function().
        "script-src": ["'self'", "'unsafe-eval'", *SCRIPT_HOSTS, *umami],
        "style-src": ["'self'", "'unsafe-inline'", *STYLE_HOSTS],
        "font-src": ["'self'", *FONT_HOSTS],
        "img-src": img_src,
        "connect-src": ["'self'", *umami],
        "frame-src": ["'none'"],
        "frame-ancestors": ["'none'"],
        "object-src": ["'none'"],
        "base-uri": ["'self'"],
        "form-action": ["'self'"],
    }
    return "; ".join(f"{name} {' '.join(values)}" for name, values in directives.items())


class ContentSecurityPolicyMiddleware:
    """Send a Content-Security-Policy on every response.

    Inline scripts are what this blocks, so a new one has to go in a file under
    ``static/js/`` -- there is deliberately no nonce to reach for.

    # ponytail: style-src still allows 'unsafe-inline' (27 style="..."
    # attributes) and script-src still allows 'unsafe-eval' (Alpine). This
    # raises the cost of an XSS rather than closing it. Moving those attributes
    # to classes and switching to Alpine's CSP build is what makes it strict.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.policy = build_policy()
        self.header = (
            "Content-Security-Policy-Report-Only"
            if getattr(settings, "CSP_REPORT_ONLY", False)
            else "Content-Security-Policy"
        )

    def __call__(self, request):
        response = self.get_response(request)
        # setdefault: a view that sets its own policy wins.
        response.setdefault(self.header, self.policy)
        return response
