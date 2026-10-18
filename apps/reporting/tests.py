from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.reporting import queries
from apps.tickets.models import Ticket

pytestmark = pytest.mark.django_db


@pytest.fixture
def second_agent(db):
    return User.objects.create_user(email="agent2@devmosolutions.com", password="x", is_staff=True)


def _make(status, assigned_to=None, created_days_ago=0, **extra):
    ticket = Ticket.objects.create(
        subject="t", body="b", status=status, assigned_to=assigned_to, **extra
    )
    if created_days_ago:
        created = timezone.now() - timedelta(days=created_days_ago)
        Ticket.objects.filter(pk=ticket.pk).update(created_at=created)
        ticket.refresh_from_db()
    return ticket


# --- current-state counts ---------------------------------------------------


def test_unclosed_counts_excludes_closed_and_merged(staff_user):
    _make(Ticket.Status.NEW)
    _make(Ticket.Status.OPEN, assigned_to=staff_user)
    _make(Ticket.Status.PENDING, assigned_to=staff_user)
    _make(Ticket.Status.RESOLVED)
    _make(Ticket.Status.CLOSED)
    _make(Ticket.Status.MERGED)

    counts = queries.unclosed_counts()
    assert counts["total"] == 4
    assert counts["unclaimed"] == 2


def test_unclaimed_plus_agent_totals_reconcile_with_unclosed(staff_user, second_agent):
    _make(Ticket.Status.NEW)
    _make(Ticket.Status.OPEN, assigned_to=staff_user)
    _make(Ticket.Status.RESOLVED, assigned_to=second_agent)
    _make(Ticket.Status.CLOSED, assigned_to=second_agent)

    counts = queries.unclosed_counts()
    assigned = sum(row["count"] for row in queries.agent_workload())
    assert counts["unclaimed"] + assigned == counts["total"]


def test_agent_workload_ignores_the_date_range(staff_user):
    """A ticket opened months ago is still sitting with its agent today."""
    _make(Ticket.Status.OPEN, assigned_to=staff_user, created_days_ago=90)

    workload = queries.agent_workload()
    assert workload == [
        {"assigned_to_id": staff_user.pk, "assigned_to__email": staff_user.email, "count": 1}
    ]


def test_agent_workload_ordered_busiest_first(staff_user, second_agent):
    _make(Ticket.Status.OPEN, assigned_to=staff_user)
    _make(Ticket.Status.OPEN, assigned_to=second_agent)
    _make(Ticket.Status.NEW, assigned_to=second_agent)

    assert [row["assigned_to__email"] for row in queries.agent_workload()] == [
        second_agent.email,
        staff_user.email,
    ]


# --- default date range -----------------------------------------------------


def test_volume_series_covers_every_day_in_the_range():
    """Gaps are zeroes, not missing points — a sparse week must not compress the axis."""
    now = timezone.now()
    volume = queries.tickets_opened_closed_over_time(date_from=now - timedelta(days=6), date_to=now)

    assert len(volume["dates"]) == 7
    assert len(volume["opened"]) == 7
    assert len(volume["closed"]) == 7


def test_dashboard_defaults_to_the_last_week(client, staff_user):
    client.force_login(staff_user)
    resp = client.get(reverse("reporting:dashboard"))

    assert resp.status_code == 200
    today = timezone.localtime().date()
    assert resp.context["date_to"] == today.isoformat()
    assert resp.context["date_from"] == (today - timedelta(days=6)).isoformat()
    assert len(resp.context["volume_data"]["dates"]) == 7


def test_dashboard_honours_an_explicit_range(client, staff_user):
    client.force_login(staff_user)
    resp = client.get(reverse("reporting:dashboard"), {"date_from": "2026-01-01", "date_to": "2026-01-03"})

    assert resp.context["date_from"] == "2026-01-01"
    assert resp.context["volume_data"]["dates"] == ["2026-01-01", "2026-01-02", "2026-01-03"]


def test_dashboard_falls_back_on_a_malformed_date(client, staff_user):
    """These arrive from the URL; a hand-edited date should not 500 the page."""
    client.force_login(staff_user)
    resp = client.get(reverse("reporting:dashboard"), {"date_from": "not-a-date"})

    assert resp.status_code == 200
    assert resp.context["date_from"] == (timezone.localtime().date() - timedelta(days=6)).isoformat()


def test_dashboard_reports_unclosed_and_unclaimed(client, staff_user):
    _make(Ticket.Status.OPEN, assigned_to=staff_user)
    _make(Ticket.Status.NEW)
    _make(Ticket.Status.CLOSED)

    client.force_login(staff_user)
    resp = client.get(reverse("reporting:dashboard"))

    assert resp.context["unclosed_count"] == 2
    assert resp.context["unclaimed_count"] == 1


def test_dashboard_denied_to_non_staff(client, db):
    user = User.objects.create_user(email="customer@example.com", password="x", is_staff=False)
    client.force_login(user)

    assert client.get(reverse("reporting:dashboard")).status_code == 403
