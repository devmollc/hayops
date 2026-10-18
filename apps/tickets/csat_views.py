from datetime import datetime, timedelta, timezone

import jwt
from django.conf import settings
from django.shortcuts import get_object_or_404, render

from .models import CSATRating, Ticket


def generate_csat_token(ticket_id, email):
    payload = {
        "ticket_id": ticket_id,
        "email": email,
        "exp": datetime.now(timezone.utc) + timedelta(days=30),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")


def rate_ticket(request, token, rating=None):
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        return render(request, "tickets/csat_rate.html", {"error": "This rating link has expired."})
    except jwt.InvalidTokenError:
        return render(request, "tickets/csat_rate.html", {"error": "Invalid rating link."})

    ticket = get_object_or_404(Ticket, pk=payload["ticket_id"])
    email = payload["email"]
    base_template = "portal/base.html" if ticket.source_application else "base.html"

    existing = CSATRating.objects.filter(ticket=ticket).first()
    if existing:
        return render(
            request,
            "tickets/csat_rate.html",
            {
                "ticket": ticket,
                "existing_rating": existing,
                "base_template": base_template,
            },
        )

    if request.method == "POST" or rating is not None:
        score = rating or request.POST.get("rating")
        try:
            score = int(score)
            if score < 1 or score > 5:
                raise ValueError
        except (TypeError, ValueError):
            return render(
                request,
                "tickets/csat_rate.html",
                {
                    "ticket": ticket,
                    "email": email,
                    "token": token,
                    "error": "Please select a rating between 1 and 5.",
                    "base_template": base_template,
                },
            )

        comment = request.POST.get("comment", "") if request.method == "POST" else ""

        csat = CSATRating.objects.create(
            ticket=ticket,
            rating=score,
            comment=comment,
            rated_by_email=email,
        )
        return render(
            request,
            "tickets/csat_rate.html",
            {
                "ticket": ticket,
                "existing_rating": csat,
                "just_submitted": True,
                "base_template": base_template,
            },
        )

    return render(
        request,
        "tickets/csat_rate.html",
        {
            "ticket": ticket,
            "email": email,
            "token": token,
            "base_template": base_template,
        },
    )
