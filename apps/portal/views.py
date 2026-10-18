import hashlib
import logging

from django.shortcuts import redirect, render

from apps.core.ratelimit import client_ip, rate_limited
from apps.tickets.services import TicketService, save_attachments

from .forms import TicketSubmitForm

logger = logging.getLogger(__name__)

RATE_LIMIT = 5
RATE_LIMIT_WINDOW = 3600  # seconds


def home(request):
    """Site root: portal submit form on a branded host, existing queue redirect otherwise."""
    if request.user.is_authenticated and not request.user.is_staff:
        return redirect("portal:ticket_list")
    if not request.source_application:
        return redirect("tickets:queue")
    return submit(request)


def submit(request):
    if not request.source_application:
        return redirect("tickets:queue")

    if request.method == "POST":
        ip = client_ip(request)
        if rate_limited(f"portal_submit:{ip}", RATE_LIMIT, RATE_LIMIT_WINDOW):
            return render(request, "portal/rate_limited.html", status=429)

        form = TicketSubmitForm(request.POST, request.FILES)
        if form.is_valid():
            if form.cleaned_data["website"]:
                logger.warning("Portal honeypot triggered from %s", ip)
                return render(request, "portal/success.html")
            # Every ticket mails a confirmation to the address given, so cap it per
            # address too -- otherwise rotating IPs could mail-bomb a third party.
            email_key = hashlib.sha256(form.cleaned_data["email"].strip().lower().encode()).hexdigest()[:32]
            if rate_limited(f"portal_submit_email:{email_key}", RATE_LIMIT, RATE_LIMIT_WINDOW):
                return render(request, "portal/rate_limited.html", status=429)

            custom_fields = {"submitted_via": "portal"}
            if form.cleaned_data["name"]:
                custom_fields["submitter_name"] = form.cleaned_data["name"]

            ticket = TicketService.create_ticket(
                subject=form.cleaned_data["subject"],
                body=form.cleaned_data["body"],
                submitter_email=form.cleaned_data["email"],
                source_application=request.source_application,
                custom_fields=custom_fields,
            )
            _, errors = save_attachments(ticket, request.FILES.getlist("attachments"))
            return render(request, "portal/success.html", {"attachment_errors": errors})
    else:
        form = TicketSubmitForm()

    return render(request, "portal/submit.html", {"form": form})
