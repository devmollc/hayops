import re
from datetime import datetime, timedelta

from django import template
from django.utils import timezone
from django.utils.html import format_html, urlize
from django.utils.safestring import mark_safe

register = template.Library()

HEX_COLOR = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
FALLBACK_APP_COLOR = "#64748b"

STATUS_COLORS = {
    "new": "blue",
    "open": "indigo",
    "pending": "yellow",
    "resolved": "green",
    "closed": "gray",
    "merged": "purple",
}

PRIORITY_COLORS = {
    "low": "gray",
    "normal": "blue",
    "high": "orange",
    "urgent": "red",
}


@register.simple_tag
def status_badge(status):
    color = STATUS_COLORS.get(status, "gray")
    return mark_safe(
        f'<span class="inline-flex items-center rounded-full bg-{color}-100 '
        f'px-2.5 py-0.5 text-xs font-medium text-{color}-800">{status}</span>'
    )


@register.simple_tag
def priority_badge(priority):
    # low/normal is the overwhelming majority of tickets. Badging every one of
    # them spends attention that high/urgent needs, so those stay plain text.
    if priority in ("low", "normal"):
        return format_html('<span class="text-sm text-gray-500">{}</span>', priority)
    color = PRIORITY_COLORS.get(priority, "gray")
    return mark_safe(
        f'<span class="inline-flex items-center rounded-full bg-{color}-100 '
        f'px-2.5 py-0.5 text-xs font-medium text-{color}-800">{priority}</span>'
    )


@register.simple_tag
def app_chip(app):
    """Application identity as its own brand colour — recognisable without reading."""
    if not app:
        return format_html('<span class="text-gray-300">&mdash;</span>')
    color = app.primary_color if HEX_COLOR.match(app.primary_color or "") else FALLBACK_APP_COLOR
    return format_html(
        '<span class="inline-flex items-center gap-1.5 whitespace-nowrap text-sm text-gray-700">'
        '<span class="h-2 w-2 flex-none rounded-full" style="background:{}"></span>{}</span>',
        color,
        app.branding_name,
    )


@register.simple_tag
def get_source_applications():
    from apps.core.models import SourceApplication

    return SourceApplication.objects.filter(is_active=True)


@register.filter
def agent_name(user):
    """Compact agent label: real name if we have one, else the email's local part."""
    if not user:
        return ""
    full_name = user.get_full_name() if hasattr(user, "get_full_name") else ""
    if full_name.strip():
        return full_name
    return (user.email or "").split("@")[0]


@register.filter
def urlize_blank(value):
    """Like urlize, but the links open in a new tab.

    Test cases are full of addresses to open. Following one in the same tab would take
    the tester off the board they are working from, losing a claim or half-typed notes.
    """
    # nofollow=False so urlize adds no rel of its own: both rels go in one attribute,
    # because a second rel on the same tag is invalid and browsers ignore it.
    linked = urlize(value or "", nofollow=False, autoescape=True)
    return mark_safe(linked.replace("<a href=", '<a target="_blank" rel="noopener nofollow" href='))


@register.filter
def time_since_short(value):
    """Compact age: 45s / 12m / 3h / 5d. Accepts a datetime or a timedelta."""
    if isinstance(value, datetime):
        value = timezone.now() - value
    if not isinstance(value, timedelta):
        return value
    if value.total_seconds() < 0:
        return "now"
    total_seconds = int(value.total_seconds())
    if total_seconds < 60:
        return f"{total_seconds}s"
    minutes = total_seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h"
    days = hours // 24
    return f"{days}d"
