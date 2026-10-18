
from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.db.models import Count, Prefetch, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from apps.core.models import SourceApplication
from apps.tickets.permissions import staff_view
from apps.tickets.views import render_or_partial

from . import services
from .api import _suite_data
from .forms import CaseForm, RunForm, SectionForm, SuiteImportForm
from .models import ApiToken, Case, Result, Run, Section
from .services import BenchError
from .signals import MARKER_KEY, bump_marker

SHOW_FILTERS = {
    "all": "All",
    "todo": "Not run",
    "in_progress": "In progress",
    "fail": "Failed",
    "focus": "Release focus",
    "p0": "P0 only",
}
TALLY_KEYS = ("pass", "fail", "blocked", "in_progress", "todo")


def _app(slug):
    return get_object_or_404(SourceApplication, slug=slug, is_active=True)


def _shows(show, case, status):
    return {
        "all": True,
        "todo": status == "todo",
        "in_progress": status == Result.Status.IN_PROGRESS,
        "fail": status == Result.Status.FAIL,
        "focus": case.release_focus,
        "p0": case.priority == Case.Priority.P0,
    }[show]


@staff_view
def index(request):
    apps = list(
        SourceApplication.objects.filter(is_active=True)
        .annotate(case_count=Count("test_cases", filter=Q(test_cases__is_active=True)))
        .order_by("name")
    )
    for app in apps:
        app.open_run = services.open_run(app)
        latest = services.latest_results(app.open_run).values()
        app.done = sum(result.status != Result.Status.IN_PROGRESS for result in latest)
    return render(request, "testbench/index.html", {"apps": apps})


@staff_view
def board(request, slug):
    app = _app(slug)
    marker = cache.get(MARKER_KEY)
    if marker is None:
        marker = bump_marker()
    if request.htmx and request.GET.get("marker") == str(marker):
        return HttpResponse(status=204)

    runs = list(Run.objects.filter(source_application=app).select_related("started_by")[:12])
    run_id = request.GET.get("run", "")
    run = get_object_or_404(Run, pk=run_id, source_application=app) if run_id.isdigit() else services.open_run(app)
    show = request.GET.get("show") if request.GET.get("show") in SHOW_FILTERS else "all"

    latest = services.latest_results(run)
    # Explicit order: Django drops Meta.ordering from aggregate (GROUP BY) queries.
    cases = Case.objects.filter(is_active=True).annotate(result_count=Count("results")).order_by("position", "key")
    sections = list(Section.objects.filter(source_application=app).prefetch_related(Prefetch("cases", queryset=cases)))
    tally = dict.fromkeys(TALLY_KEYS, 0)
    for section in sections:
        section.tally = dict.fromkeys(TALLY_KEYS, 0)
        section.visible_cases = []
        for case in section.cases.all():
            case.latest = latest.get(case.pk)
            status = case.latest.status if case.latest else "todo"
            tally[status] += 1
            section.tally[status] += 1
            if _shows(show, case, status):
                section.visible_cases.append(case)
        section.total = sum(section.tally.values())
        section.done = section.total - section.tally["todo"] - section.tally["in_progress"]

    context = {
        "app": app,
        "run": run,
        "runs": runs,
        "show": show,
        "show_filters": SHOW_FILTERS.items(),
        "sections": sections,
        "tally": tally,
        "total": sum(tally.values()),
        "visible_count": sum(len(section.visible_cases) for section in sections),
        "editable": run is not None and run.closed_at is None,
        "marker": marker,
    }
    return render_or_partial(request, "testbench/board.html", "testbench/partials/board.html", context)


@staff_view
@require_POST
def case_action(request, slug, key, action):
    """Claim, release or record a case on the open run; answers htmx with the re-rendered card."""
    app = _app(slug)
    case = get_object_or_404(Case, source_application=app, key=key, is_active=True)
    run = services.open_run(app)
    error = ""
    try:
        if action == "claim":
            services.claim(run, case, request.user)
        elif action == "release":
            services.release(run, case)
        else:
            services.record_result(
                run, case, request.user, request.POST.get("status", ""), request.POST.get("notes", "")
            )
    except BenchError as exc:
        error = str(exc)

    if not request.htmx:
        if error:
            messages.error(request, error)
        return redirect(reverse("testbench:board", args=[app.slug]) + f"#case-{case.key}")
    case.latest = run.results.filter(case=case).select_related("tester").first() if run else None
    case.result_count = case.results.count()
    context = {"app": app, "case": case, "run": run, "editable": run is not None, "error": error}
    response = render(request, "testbench/partials/case_card.html", context)
    # Refresh the rest of the board (tallies, area bars) now rather than on the next poll.
    response["HX-Trigger"] = "tb-changed"
    return response


@staff_view
def case_detail(request, slug, key):
    app = _app(slug)
    case = get_object_or_404(Case.objects.select_related("section"), source_application=app, key=key)
    results = list(case.results.select_related("run__source_application", "tester"))
    for result in results:
        result.case = case
        if result.status in (Result.Status.FAIL, Result.Status.BLOCKED):
            result.issue_link = services.github_issue_link(result)
    return render(request, "testbench/case_detail.html", {"app": app, "case": case, "results": results})


@staff_view
def case_form(request, slug, key=None):
    app = _app(slug)
    case = get_object_or_404(Case, source_application=app, key=key) if key else None
    form = CaseForm(request.POST or None, instance=case, application=app)
    if request.method == "POST" and form.is_valid():
        case = form.save()
        messages.success(request, f"Saved {case.key}.")
        return redirect("testbench:case_detail", slug=app.slug, key=case.key)
    context = {
        "app": app,
        "form": form,
        "title": f"Edit {case.key}" if case else "Add a test case",
        "hint": "" if app.test_sections.exists() else "Add an area first — every case belongs to one.",
        "cancel_url": reverse("testbench:case_detail", args=[app.slug, case.key])
        if case
        else reverse("testbench:board", args=[app.slug]),
    }
    return render(request, "testbench/form.html", context)


@staff_view
@require_POST
def case_delete(request, slug, key):
    app = _app(slug)
    case = get_object_or_404(Case, source_application=app, key=key, is_active=True)
    case.is_active = False
    case.save(update_fields=["is_active", "updated_at"])
    messages.success(request, f"Deleted {case.key}. Its history is kept.")
    return redirect("testbench:board", slug=app.slug)


@staff_view
def section_form(request, slug, code=None):
    app = _app(slug)
    section = get_object_or_404(Section, source_application=app, code=code) if code else None
    form = SectionForm(request.POST or None, instance=section, application=app)
    if request.method == "POST" and form.is_valid():
        section = form.save()
        messages.success(request, f"Saved {section.name}.")
        return redirect(reverse("testbench:board", args=[app.slug]) + f"#s-{section.code}")
    context = {
        "app": app,
        "form": form,
        "title": f"Edit {section.name}" if section else "Add an area",
        "cancel_url": reverse("testbench:board", args=[app.slug]),
    }
    return render(request, "testbench/form.html", context)


@staff_view
def suite_import(request, slug):
    app = _app(slug)
    form = SuiteImportForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        changes = services.upsert_suite(app, form.sections, prune=form.cleaned_data["prune"])
        messages.success(
            request,
            f"Imported: {changes['created']} added, {changes['updated']} updated, {changes['deactivated']} deleted.",
        )
        return redirect("testbench:board", slug=app.slug)
    context = {
        "app": app,
        "form": form,
        "title": "Import test cases",
        "submit_label": "Import",
        "cancel_url": reverse("testbench:board", args=[app.slug]),
    }
    return render(request, "testbench/form.html", context)


@staff_view
def suite_export(request, slug):
    app = _app(slug)
    response = JsonResponse(_suite_data(app), json_dumps_params={"indent": 2})
    response["Content-Disposition"] = f'attachment; filename="{app.slug}-testbench.json"'
    return response


@staff_view
@require_POST
def run_start(request, slug):
    app = _app(slug)
    form = RunForm(request.POST)
    if form.is_valid():
        run = services.start_run(app, request.user, **form.cleaned_data)
        messages.success(request, f"Started {run.name}.")
    else:
        messages.error(request, "A run needs a name.")
    return redirect("testbench:board", slug=app.slug)


@staff_view
@require_POST
def result_update(request, slug, pk):
    app = _app(slug)
    result = get_object_or_404(Result.objects.select_related("case"), pk=pk, run__source_application=app)
    try:
        services.update_result(result, request.POST.get("notes", ""), request.POST.get("github_issue_url", ""))
    except BenchError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Result updated.")
    return redirect(reverse("testbench:case_detail", args=[app.slug, result.case.key]) + f"#result-{result.pk}")


@never_cache
@staff_view
def tokens(request):
    # The raw token is rendered straight into this POST response: a redirect would
    # have to carry it through the session or the messages cookie.
    raw_token = ""
    if request.method == "POST":
        _, raw_token = ApiToken.issue(request.user, request.POST.get("name", "").strip()[:100] or "API token")
    context = {
        "tokens": request.user.api_tokens.all(),
        "raw_token": raw_token,
        "api_root": settings.SITE_URL.rstrip("/") + reverse("testbench-api-apps"),
    }
    return render(request, "testbench/tokens.html", context)


@staff_view
@require_POST
def token_delete(request, pk):
    get_object_or_404(ApiToken, pk=pk, user=request.user).delete()
    messages.success(request, "Token deleted. Anything using it is now refused.")
    return redirect("testbench:tokens")
