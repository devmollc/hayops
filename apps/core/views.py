from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import render

from .models import SourceApplication

# Inbound routing matches the domain only (see inbound_email.handler), so these are
# the addresses we publish, not an allowlist -- any local part reaches the same app.
INBOUND_ALIASES = ("support", "help", "bugs", "hello")


@login_required
def help_page(request):
    if not request.user.is_staff:
        return HttpResponseForbidden("Staff only.")
    return render(
        request,
        "core/help.html",
        {
            "mail_apps": SourceApplication.objects.filter(is_active=True).order_by("name"),
            "inbound_aliases": INBOUND_ALIASES,
            "default_email_domain": settings.DEFAULT_EMAIL_DOMAIN,
        },
    )
