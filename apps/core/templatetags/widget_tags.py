import hashlib
import hmac

from django import template
from django.conf import settings
from django.urls import reverse

from apps.core.models import SourceApplication

register = template.Library()


@register.inclusion_tag("core/widget_script.html", takes_context=True)
def helpdesk_widget(context):
    """Render the helpdesk widget script tag.

    For authenticated users, includes HMAC-signed identity so the widget
    can make authenticated API calls. Anonymous visitors get the widget
    in anonymous mode (no user hash).
    """
    request = context.get("request")

    try:
        app = SourceApplication.objects.get(slug=settings.HELPDESK_WIDGET_SLUG)
    except SourceApplication.DoesNotExist:
        return {"widget_enabled": False}

    user = getattr(request, "user", None)
    user_email = ""
    user_name = ""
    user_hash = ""

    if user is not None and user.is_authenticated:
        user_email = user.email
        user_name = getattr(user, "get_full_name", lambda: "")() or user_email
        user_hash = hmac.new(
            app.api_secret.encode(),
            user_email.encode(),
            digestmod=hashlib.sha256,
        ).hexdigest()

    widget_js_path = reverse("widget-js", args=[settings.WIDGET_VERSION])

    return {
        "widget_enabled": True,
        "api_key": app.api_key,
        "user_email": user_email,
        "user_name": user_name,
        "user_hash": user_hash,
        "api_url": request.build_absolute_uri("/api") if request else "",
        "widget_js_url": request.build_absolute_uri(widget_js_path) if request else widget_js_path,
    }
