from datetime import datetime, timedelta

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import render
from django.utils import timezone

from . import queries


def _parse_date(value, end_of_day=False):
    """A YYYY-MM-DD query param as an aware datetime, or None if absent/malformed.

    Malformed is None rather than an exception: these arrive from the URL, and a
    hand-edited date should not 500 the dashboard.
    """
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d")
    except (TypeError, ValueError):
        return None
    if end_of_day:
        parsed = parsed.replace(hour=23, minute=59, second=59)
    return timezone.make_aware(parsed)


@login_required
def dashboard(request):
    if not request.user.is_staff:
        return HttpResponseForbidden("Staff access required.")

    # Default to the last week, inclusive of today.
    date_to = _parse_date(request.GET.get("date_to"), end_of_day=True) or timezone.localtime()
    date_from = _parse_date(request.GET.get("date_from")) or (
        date_to - timedelta(days=queries.DEFAULT_DAYS - 1)
    ).replace(hour=0, minute=0, second=0, microsecond=0)

    volume = queries.tickets_opened_closed_over_time(date_from=date_from, date_to=date_to)
    avg_response = queries.avg_first_response_time(date_from=date_from, date_to=date_to)
    sla_rate = queries.sla_compliance_rate(date_from=date_from, date_to=date_to)
    csat = queries.csat_averages(date_from=date_from, date_to=date_to)
    sources = queries.tickets_by_source(date_from=date_from, date_to=date_to)

    workload = queries.agent_workload()
    counts = queries.unclosed_counts()

    context = {
        "volume_data": volume,
        "avg_response": avg_response,
        "sla_rate": sla_rate,
        "workload": workload,
        "csat_data": csat,
        "csat_overall": csat,
        "source_data": sources,
        "unclosed_count": counts["total"],
        "unclaimed_count": counts["unclaimed"],
        "date_from": timezone.localtime(date_from).strftime("%Y-%m-%d"),
        "date_to": timezone.localtime(date_to).strftime("%Y-%m-%d"),
    }
    return render(request, "reporting/dashboard.html", context)
