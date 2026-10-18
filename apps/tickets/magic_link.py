from datetime import datetime, timedelta, timezone

import jwt
from django.conf import settings
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .models import Comment, Ticket
from .services import TicketService, save_attachments

REPLY_MAX_LENGTH = 10000


def generate_ticket_access_token(ticket_id, email):
    payload = {
        "ticket_id": ticket_id,
        "email": email,
        "exp": datetime.now(timezone.utc) + timedelta(days=30),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")


def build_magic_link_url(request, ticket, email):
    token = generate_ticket_access_token(ticket.pk, email)
    path = reverse("ticket_magic_link", kwargs={"token": token})
    return request.build_absolute_uri(path)


def ticket_magic_link_view(request, token):
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        return render(
            request,
            "tickets/magic_link_detail.html",
            {
                "error": "This access link has expired.",
            },
        )
    except jwt.InvalidTokenError:
        return render(
            request,
            "tickets/magic_link_detail.html",
            {
                "error": "Invalid access link.",
            },
        )

    ticket = get_object_or_404(Ticket, pk=payload["ticket_id"])
    email = payload["email"]

    # Verify the email is a participant
    if not ticket.participants.filter(email=email).exists():
        return render(
            request,
            "tickets/magic_link_detail.html",
            {
                "error": "You are not a participant on this ticket.",
            },
        )

    # Already signed in as a participant? Use the real portal view. (Not
    # tickets:detail -- that lives under /tickets/, which the portal host 404s.)
    if request.user.is_authenticated and ticket.participants.filter(user=request.user).exists():
        target = "tickets:detail" if request.user.is_staff else "portal:ticket_detail"
        return redirect(target, reference=ticket.reference_number)

    can_reply = ticket.status not in (Ticket.Status.CLOSED, Ticket.Status.MERGED)
    reply_error = None

    if request.method == "POST":
        if not can_reply:
            return redirect("ticket_magic_link", token=token)
        body = request.POST.get("body", "").strip()
        if not body:
            reply_error = "Reply cannot be empty."
        elif len(body) > REPLY_MAX_LENGTH:
            reply_error = f"Reply is too long ({REPLY_MAX_LENGTH:,} characters max)."
        else:
            comment = TicketService.add_comment(ticket=ticket, body=body, author=None, author_email=email)
            _, attachment_errors = save_attachments(ticket, request.FILES.getlist("attachments"), comment=comment)
            if not attachment_errors:
                return redirect("ticket_magic_link", token=token)
            # Reply is saved; fall through so the rejected-file notice survives.
            reply_error = " ".join(attachment_errors)

    # Public view: public comments only, no internal notes, no staff controls
    comments = list(ticket.comments.select_related("author").filter(comment_type=Comment.CommentType.PUBLIC))
    events = list(ticket.events.select_related("actor").customer_visible())
    timeline = sorted(comments + events, key=lambda x: x.created_at)
    participants = ticket.participants.select_related("user").all()

    return render(
        request,
        "tickets/magic_link_detail.html",
        {
            "ticket": ticket,
            "timeline": timeline,
            "participants": participants,
            "can_reply": can_reply,
            "reply_error": reply_error,
        },
    )
