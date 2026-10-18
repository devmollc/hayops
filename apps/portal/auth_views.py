"""Customer-facing portal: passwordless login and the customer's own ticket list.

Deliberately separate from apps.tickets.views, which is the staff UI. These views
render templates/portal/*, which contain no staff controls to leak, and they never
use @login_required -- settings.LOGIN_URL points at /accounts/login/, which
SourceApplicationMiddleware 404s on every branded portal host.
"""

import functools
import hashlib
import logging

from django.contrib.auth import logout as auth_logout
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from apps.accounts.models import MagicLoginToken, User
from apps.accounts.services import consume_login_token
from apps.core.ratelimit import client_ip, rate_limited
from apps.tickets.models import Comment, Ticket, TicketParticipant
from apps.tickets.services import TicketService, customer_scope, save_attachments

from .forms import MagicLoginForm, PortalReplyForm

logger = logging.getLogger(__name__)

LOGIN_IP_LIMIT = 5
LOGIN_EMAIL_LIMIT = 3
LOGIN_RATE_WINDOW = 3600  # seconds


def customer_required(view):
    """Like @login_required, but bounces to the portal login instead of /accounts/."""

    @functools.wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"{reverse('portal:login')}?next={request.path}")
        if request.user.is_staff:
            return redirect("tickets:queue")
        return view(request, *args, **kwargs)

    return wrapper


def _known_address(email, source_application):
    """True if this address has ever been a participant or has an account.

    We never say so in the response -- this only decides whether an email is worth
    sending, so we don't mail strangers whose address someone typed in.
    """
    participants = TicketParticipant.objects.filter(email__iexact=email)
    if source_application is not None:
        participants = participants.filter(customer_scope("ticket__source_application", source_application))
    if participants.exists():
        return True
    return User.objects.filter(email__iexact=email, is_staff=False).exists()


def login_request(request):
    """Ask for an email and mail a one-time login link."""
    if request.user.is_authenticated and not request.user.is_staff:
        return redirect("portal:ticket_list")

    next_url = request.GET.get("next", "")
    if request.method == "POST":
        ip = client_ip(request)
        # Hash the email in the cache key so the cache table isn't a plaintext
        # address list, and so one victim can't be mail-bombed from rotating IPs.
        form = MagicLoginForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data["email"].strip().lower()
            email_key = hashlib.sha256(email.encode()).hexdigest()[:32]
            if rate_limited(f"portal_login_ip:{ip}", LOGIN_IP_LIMIT, LOGIN_RATE_WINDOW) or rate_limited(
                f"portal_login_email:{email_key}", LOGIN_EMAIL_LIMIT, LOGIN_RATE_WINDOW
            ):
                return render(request, "portal/rate_limited.html", status=429)

            if form.cleaned_data["website"]:
                logger.warning("Portal login honeypot triggered from %s", ip)
            elif _known_address(email, request.source_application):
                from apps.inbound_email.tasks import send_magic_login

                app_id = request.source_application.pk if request.source_application else None
                posted_next = request.POST.get("next", "")
                safe_next = (
                    posted_next
                    if url_has_allowed_host_and_scheme(posted_next, allowed_hosts={request.get_host()})
                    else ""
                )
                transaction.on_commit(
                    lambda: send_magic_login.defer(
                        email=email,
                        source_application_id=app_id,
                        next_url=safe_next,
                        requested_ip=ip,
                    )
                )

            # Identical response either way: never confirm whether an address is known.
            return render(request, "portal/login_sent.html", {"email": email})
    else:
        form = MagicLoginForm()

    return render(request, "portal/login.html", {"form": form, "next": next_url})


def login_verify(request, token):
    """Confirm-then-consume.

    GET only validates and renders a button. Outlook SafeLinks, Proofpoint and
    Gmail's prefetcher all issue a GET on every link in a delivered message; if GET
    consumed the token, the scanner would burn it before the human ever clicked.
    Bots don't POST.
    """
    if request.method == "POST":
        user, consumed = consume_login_token(request, token)
        if user is None:
            return render(request, "portal/login_invalid.html", status=410)

        destination = consumed.next_url
        if destination and url_has_allowed_host_and_scheme(destination, allowed_hosts={request.get_host()}):
            return redirect(destination)
        return redirect("portal:ticket_list")

    if MagicLoginToken.find_active(token) is None:
        return render(request, "portal/login_invalid.html", status=410)
    return render(request, "portal/login_confirm.html", {"token": token})


@require_POST
def logout_view(request):
    auth_logout(request)
    return redirect("portal:login")


def _customer_tickets(request):
    tickets = (
        Ticket.objects.filter(participants__user=request.user)
        .exclude(status=Ticket.Status.MERGED)
        .select_related("source_application", "queue")
        .distinct()
    )
    # On a branded host a customer sees only that product's tickets. On the admin
    # domain there is no source_application to scope by and they see all of theirs.
    if request.source_application is not None:
        tickets = tickets.filter(customer_scope("source_application", request.source_application))
    return tickets.order_by("-updated_at")


@customer_required
def ticket_list(request):
    tickets = list(_customer_tickets(request))
    return render(
        request,
        "portal/ticket_list.html",
        {
            "open_tickets": [t for t in tickets if t.is_open],
            "closed_tickets": [t for t in tickets if not t.is_open],
        },
    )


@customer_required
def ticket_detail(request, reference):
    ticket = get_object_or_404(_customer_tickets(request), reference_number=reference)
    can_reply = ticket.status not in (Ticket.Status.CLOSED, Ticket.Status.MERGED)
    form = PortalReplyForm()
    attachment_errors = []

    if request.method == "POST":
        if not can_reply:
            raise Http404("This ticket is closed.")
        form = PortalReplyForm(request.POST, request.FILES)
        if form.is_valid():
            comment = TicketService.add_comment(
                ticket=ticket,
                body=form.cleaned_data["body"],
                author=request.user,
                author_email=request.user.email,
                comment_type=Comment.CommentType.PUBLIC,
            )
            _, attachment_errors = save_attachments(ticket, request.FILES.getlist("attachments"), comment=comment)
            # Redirect on the clean path so a refresh cannot repost the reply. When a
            # file was rejected we fall through instead, because the reply is already
            # saved and the redirect would drop the only notice the customer gets.
            if not attachment_errors:
                return redirect("portal:ticket_detail", reference=reference)
            form = PortalReplyForm()

    comments = list(ticket.comments.select_related("author").filter(comment_type=Comment.CommentType.PUBLIC))
    events = list(ticket.events.select_related("actor").customer_visible())

    return render(
        request,
        "portal/ticket_detail.html",
        {
            "ticket": ticket,
            "timeline": sorted(comments + events, key=lambda x: x.created_at),
            "can_reply": can_reply,
            "form": form,
            "attachment_errors": attachment_errors,
        },
    )
