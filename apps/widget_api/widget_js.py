from pathlib import Path

from django.conf import settings
from django.http import Http404, HttpResponse

# Libraries the widget loads on demand (screenshot capture, annotation), served
# from this origin so host pages need not allow a CDN in their CSP. Pinned in
# widget/package.json and copied into dist/vendor/ by `npm run build`.
VENDOR_FILES = ("html2canvas-pro.min.js", "markerjs2.js")

# ponytail: one release of grace so hosts pinned to the previous version keep
# working until they move their HELPDESK_WIDGET_VERSION; drop it next release.
PREVIOUS_VERSIONS = ("1.2.3", "1.2.4")


def _serve(version, *parts):
    if version != settings.WIDGET_VERSION and version not in PREVIOUS_VERSIONS:
        raise Http404
    try:
        content = Path(settings.BASE_DIR, "widget", "dist", *parts).read_bytes()
    except FileNotFoundError:
        raise Http404
    response = HttpResponse(content, content_type="application/javascript; charset=utf-8")
    response["Cache-Control"] = "public, max-age=31536000, immutable"
    return response


def widget_js(request, version):
    """Serve the widget JS bundle at a versioned URL for long-term caching.

    URL: /widget/v<version>/helpdesk-widget.js
    Returns 404 for unknown versions so consumers know when to update.
    """
    return _serve(version, "helpdesk-widget.js")


def widget_vendor_js(request, version, name):
    """Serve a pinned library the widget loads: /widget/v<version>/vendor/<name>."""
    if name not in VENDOR_FILES:
        raise Http404
    return _serve(version, "vendor", name)
