"""Testbench rules, shared by the staff pages and the API."""

from urllib.parse import urlencode

from django.conf import settings
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from apps.core.models import SourceApplication
from apps.core.templatetags.core_tags import agent_name

from .models import Case, Result, Run, Section
from .signals import bump_marker

CASE_FIELDS = ("title", "priority", "setup", "steps", "expected", "release_focus")
OUTCOMES = (Result.Status.PASS, Result.Status.FAIL, Result.Status.BLOCKED)
# Very long new-issue URLs are rejected by GitHub; the notes are what gets trimmed.
MAX_ISSUE_URL_CHARS = 7000


class BenchError(Exception):
    """A refusal to show the tester as-is, e.g. a claim someone else holds."""


def open_run(app):
    return Run.objects.filter(source_application=app, closed_at__isnull=True).first()


def start_run(app, user, name, build="", notes=""):
    """Close the application's open run, if any, and open a new one."""
    with transaction.atomic():
        # Serialize concurrent starts on the application row; otherwise both pass the
        # close and the second insert trips the one-open-run constraint.
        SourceApplication.objects.select_for_update().get(pk=app.pk)
        Run.objects.filter(source_application=app, closed_at__isnull=True).update(closed_at=timezone.now())
        return Run.objects.create(source_application=app, started_by=user, name=name, build=build, notes=notes)


def claim(run, case, user):
    _require_open(run)
    try:
        with transaction.atomic():
            return Result.objects.create(run=run, case=case, tester=user, status=Result.Status.IN_PROGRESS)
    except IntegrityError:
        holder = Result.objects.filter(run=run, case=case, status=Result.Status.IN_PROGRESS).first()
        who = agent_name(holder.tester) if holder else ""
        raise BenchError(f"{case.key} is already being worked on by {who or 'someone else'}.") from None


def release(run, case):
    """Drop the in-progress claim. It holds no outcome yet, so nothing is lost."""
    Result.objects.filter(run=run, case=case, status=Result.Status.IN_PROGRESS).delete()


def record_result(run, case, user, status, notes="", issue_url=""):
    """Record an outcome: completes the user's own claim, or adds a row if nobody holds one."""
    _require_open(run)
    if status not in OUTCOMES:
        raise BenchError("Choose pass, fail or blocked.")
    issue_url = clean_issue_url(issue_url)
    with transaction.atomic():
        result = Result.objects.select_for_update().filter(run=run, case=case, status=Result.Status.IN_PROGRESS).first()
        if result and result.tester_id != user.pk:
            who = agent_name(result.tester) or "someone else"
            raise BenchError(f"{case.key} is being worked on by {who}. Release the claim first.")
        result = result or Result(run=run, case=case, tester=user)
        result.status = status
        result.notes = notes
        result.github_issue_url = issue_url
        result.completed_at = timezone.now()
        result.save()
    return result


def update_result(result, notes, issue_url=""):
    """Amend a finished result: correct the notes or link the issue that was filed."""
    if result.status == Result.Status.IN_PROGRESS:
        raise BenchError("Record an outcome before editing the result.")
    result.github_issue_url = clean_issue_url(issue_url)
    result.notes = notes
    result.save()


def clean_issue_url(url):
    url = (url or "").strip()
    if url and (not url.startswith("https://github.com/") or len(url) > 200):
        raise BenchError("The issue link must be a https://github.com/… URL.")
    return url


def _require_open(run):
    if run is None or run.closed_at:
        raise BenchError("Start a run before recording results.")


def latest_results(run):
    """Newest result per case in the run, keyed by case id."""
    if run is None:
        return {}
    rows = (
        Result.objects.filter(run=run)
        .order_by("case_id", "-created_at", "-id")
        .distinct("case_id")
        .select_related("tester")
    )
    return {row.case_id: row for row in rows}


@transaction.atomic
def upsert_suite(app, sections, prune=False):
    """Create or update areas by code and cases by key; list order becomes display order.

    `sections` is validated input: [{code, name, note, cases: [{key, title, ...}]}].
    Cases missing from it are left alone unless `prune`, which deactivates them rather
    than deleting, so their history survives. Returns counts of what changed.
    """
    changes = {"created": 0, "updated": 0, "deactivated": 0}
    keys = []
    for section_position, section_data in enumerate(sections):
        section, _ = Section.objects.update_or_create(
            source_application=app,
            code=section_data["code"],
            defaults={"name": section_data["name"], "note": section_data.get("note", ""), "position": section_position},
        )
        for case_position, case_data in enumerate(section_data.get("cases", [])):
            fields = {name: case_data[name] for name in CASE_FIELDS if name in case_data}
            _, created = Case.objects.update_or_create(
                source_application=app,
                key=case_data["key"],
                defaults={**fields, "section": section, "position": case_position, "is_active": True},
            )
            changes["created" if created else "updated"] += 1
            keys.append(case_data["key"])
    if prune:
        stale = Case.objects.filter(source_application=app, is_active=True).exclude(key__in=keys)
        changes["deactivated"] = stale.update(is_active=False)
        bump_marker()
    return changes


def github_issue_link(result):
    """GitHub new-issue URL prefilled from the result, or "" when the app has no repo set."""
    run, case = result.run, result.case
    app = run.source_application
    if not app.github_repo:
        return ""
    history_url = (
        settings.SITE_URL.rstrip("/")
        + reverse("testbench:case_detail", args=[app.slug, case.key])
        + f"#result-{result.pk}"
    )
    head = [
        f"**Test case:** {case.key} — {case.title} ({case.priority})",
        f"**Run:** {run.name}" + (f" · build `{run.build}`" if run.build else ""),
        f"**Result:** {result.get_status_display()} · {agent_name(result.tester) or 'unknown tester'}",
        "",
        f"**Setup:** {case.setup or '—'}",
        "",
        "**Steps**",
        *[f"{number}. {step}" for number, step in enumerate(case.steps, 1)],
        "",
        f"**Expected:** {case.expected or '—'}",
        "",
        "**What happened**",
    ]
    tail = ["", f"Screenshots and history: {history_url}"]

    def build(notes):
        body = "\n".join([*head, notes, *tail])
        return f"https://github.com/{app.github_repo}/issues/new?" + urlencode(
            {"title": f"[{case.key}] {case.title}", "body": body}
        )

    notes = result.notes or "_No notes recorded._"
    url = build(notes)
    while len(url) > MAX_ISSUE_URL_CHARS and notes:
        notes = notes[: len(notes) * 3 // 4]
        url = build(f"{notes} … (truncated — full notes at the link below)")
    return url
