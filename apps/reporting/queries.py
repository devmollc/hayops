from datetime import timedelta

from django.db.models import Avg, Count, F, Q
from django.db.models.functions import TruncDate
from django.utils import timezone

from apps.tickets.models import CSATRating, Ticket

DEFAULT_DAYS = 7

# Everything still on the board — the same definition the queue uses when no
# status filter is applied. Sharing one definition across the unclosed total,
# the unclaimed count and the per-agent counts is what lets those three numbers
# reconcile: unclaimed + sum(per agent) == unclosed.
UNCLOSED_STATUSES = [
    Ticket.Status.NEW,
    Ticket.Status.OPEN,
    Ticket.Status.PENDING,
    Ticket.Status.RESOLVED,
]


def _date_range(days=DEFAULT_DAYS, date_from=None, date_to=None):
    if date_to is None:
        date_to = timezone.now()
    if date_from is None:
        date_from = date_to - timedelta(days=days)
    return date_from, date_to


def tickets_opened_closed_over_time(days=DEFAULT_DAYS, date_from=None, date_to=None):
    date_from, date_to = _date_range(days, date_from, date_to)

    opened = (
        Ticket.objects.filter(created_at__range=(date_from, date_to))
        .annotate(date=TruncDate("created_at"))
        .values("date")
        .annotate(count=Count("id"))
        .order_by("date")
    )

    closed = (
        Ticket.objects.filter(
            resolved_at__range=(date_from, date_to),
            status__in=[Ticket.Status.RESOLVED, Ticket.Status.CLOSED],
        )
        .annotate(date=TruncDate("resolved_at"))
        .values("date")
        .annotate(count=Count("id"))
        .order_by("date")
    )

    opened_dict = {str(row["date"]): row["count"] for row in opened}
    closed_dict = {str(row["date"]): row["count"] for row in closed}

    # Every day in the range, not just the ones with tickets. A quiet Sunday is
    # a zero on the chart, not a missing point that compresses the axis.
    start = timezone.localtime(date_from).date()
    end = timezone.localtime(date_to).date()
    all_dates = [str(start + timedelta(days=i)) for i in range((end - start).days + 1)]

    return {
        "dates": all_dates,
        "opened": [opened_dict.get(d, 0) for d in all_dates],
        "closed": [closed_dict.get(d, 0) for d in all_dates],
    }


def avg_first_response_time(days=DEFAULT_DAYS, date_from=None, date_to=None):
    date_from, date_to = _date_range(days, date_from, date_to)

    result = Ticket.objects.filter(
        created_at__range=(date_from, date_to),
        first_responded_at__isnull=False,
    ).aggregate(avg_response=Avg(F("first_responded_at") - F("created_at")))

    avg = result["avg_response"]
    if avg is None:
        return None
    total_seconds = int(avg.total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    return {"total_seconds": total_seconds, "display": f"{hours}h {minutes}m"}


def sla_compliance_rate(days=DEFAULT_DAYS, date_from=None, date_to=None):
    date_from, date_to = _date_range(days, date_from, date_to)

    tickets_with_sla = Ticket.objects.filter(
        created_at__range=(date_from, date_to),
    ).filter(
        Q(first_response_due_at__isnull=False) | Q(resolution_due_at__isnull=False),
    )

    total = tickets_with_sla.count()
    if total == 0:
        return None

    met_sla = tickets_with_sla.filter(
        Q(first_response_due_at__isnull=True) | Q(first_responded_at__lte=F("first_response_due_at")),
        Q(resolution_due_at__isnull=True) | Q(resolved_at__lte=F("resolution_due_at")),
    ).count()

    return round((met_sla / total) * 100, 1)


def unclosed_counts():
    """Current-state totals. Deliberately not filtered by the report date range —
    a ticket opened last month is still sitting in the queue today."""
    unclosed = Ticket.objects.filter(status__in=UNCLOSED_STATUSES)
    return {
        "total": unclosed.count(),
        "unclaimed": unclosed.filter(assigned_to__isnull=True).count(),
    }


def agent_workload():
    """Unclosed tickets per agent, busiest first. Same current-state basis as
    unclosed_counts()."""
    return list(
        Ticket.objects.filter(
            status__in=UNCLOSED_STATUSES,
            assigned_to__isnull=False,
        )
        .values("assigned_to_id", "assigned_to__email")
        .annotate(count=Count("id"))
        .order_by("-count", "assigned_to__email")
    )


def csat_averages(days=DEFAULT_DAYS, date_from=None, date_to=None):
    date_from, date_to = _date_range(days, date_from, date_to)

    ratings = (
        CSATRating.objects.filter(created_at__range=(date_from, date_to))
        .annotate(date=TruncDate("created_at"))
        .values("date")
        .annotate(avg_rating=Avg("rating"), count=Count("id"))
        .order_by("date")
    )

    overall = CSATRating.objects.filter(created_at__range=(date_from, date_to)).aggregate(
        avg=Avg("rating"), count=Count("id")
    )

    return {
        "dates": [str(row["date"]) for row in ratings],
        "averages": [float(row["avg_rating"]) for row in ratings],
        "counts": [row["count"] for row in ratings],
        "overall_avg": round(float(overall["avg"]), 2) if overall["avg"] else None,
        "overall_count": overall["count"],
    }


def tickets_by_source(days=DEFAULT_DAYS, date_from=None, date_to=None):
    date_from, date_to = _date_range(days, date_from, date_to)

    results = (
        Ticket.objects.filter(created_at__range=(date_from, date_to))
        .values("source_application__name")
        .annotate(count=Count("id"))
        .order_by("-count")
    )

    return {
        "labels": [row["source_application__name"] or "Direct" for row in results],
        "counts": [row["count"] for row in results],
    }
