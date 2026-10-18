"""Token-authenticated testbench API, so Claude or a script can manage suites.

Everything is addressed by application slug and case key — the identifiers already
quoted in bug reports — never by database id. See docs/TESTBENCH.md.
"""

from django.db import IntegrityError, transaction
from django.db.models import Count, Prefetch, Q
from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from apps.core.models import SourceApplication

from . import services
from .authentication import BearerTokenAuthentication
from .models import Case, Run, Section, key_validator


class CaseInput(serializers.Serializer):
    key = serializers.CharField(max_length=20, validators=[key_validator])
    title = serializers.CharField(max_length=255)
    priority = serializers.ChoiceField(choices=Case.Priority.choices, default=Case.Priority.P1)
    setup = serializers.CharField(allow_blank=True, default="")
    steps = serializers.ListField(child=serializers.CharField(), default=list)
    expected = serializers.CharField(allow_blank=True, default="")
    release_focus = serializers.BooleanField(default=False)


class CaseWrite(CaseInput):
    section = serializers.CharField(max_length=12, help_text="Area code, e.g. ISO")


class SectionInput(serializers.Serializer):
    code = serializers.CharField(max_length=12, validators=[key_validator])
    name = serializers.CharField(max_length=255)
    note = serializers.CharField(allow_blank=True, default="")
    cases = CaseInput(many=True, default=list)


class SuiteInput(serializers.Serializer):
    sections = SectionInput(many=True)


class RunInput(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    build = serializers.CharField(max_length=100, allow_blank=True, default="")
    notes = serializers.CharField(allow_blank=True, default="")


def endpoint(*methods):
    """A DRF view for staff users presenting a personal API token."""

    def decorate(view):
        view = permission_classes([IsAdminUser])(view)
        view = authentication_classes([BearerTokenAuthentication])(view)
        return api_view(list(methods))(view)

    return decorate


def _app(slug):
    return get_object_or_404(SourceApplication, slug=slug, is_active=True)


def _case_data(case):
    return {
        "section": case.section.code,
        "key": case.key,
        **{name: getattr(case, name) for name in services.CASE_FIELDS},
    }


def _suite_data(app):
    active = Case.objects.filter(is_active=True).select_related("section")
    sections = Section.objects.filter(source_application=app).prefetch_related(Prefetch("cases", queryset=active))
    return {
        "application": app.slug,
        "sections": [
            {"code": s.code, "name": s.name, "note": s.note, "cases": [_case_data(c) for c in s.cases.all()]}
            for s in sections
        ],
    }


def _run_data(run):
    if run is None:
        return None
    return {
        "id": run.pk,
        "name": run.name,
        "build": run.build,
        "notes": run.notes,
        "started_at": run.created_at,
        "closed_at": run.closed_at,
    }


def _result_data(result):
    return {
        "id": result.pk,
        "run": result.run.name,
        "status": result.status,
        "tester": result.tester.email if result.tester else None,
        "notes": result.notes,
        "github_issue_url": result.github_issue_url,
        "started_at": result.created_at,
        "completed_at": result.completed_at,
    }


def applications_data():
    apps = (
        SourceApplication.objects.filter(is_active=True)
        .annotate(case_count=Count("test_cases", filter=Q(test_cases__is_active=True)))
        .order_by("slug")
    )
    return [
        {"slug": a.slug, "name": a.name, "case_count": a.case_count, "open_run": _run_data(services.open_run(a))}
        for a in apps
    ]


def current_run_data(app):
    """Latest status of every active case in the open run, or None when no run is open."""
    run = services.open_run(app)
    if run is None:
        return None
    latest = services.latest_results(run)
    cases = (
        Case.objects.filter(source_application=app, is_active=True)
        .select_related("section")
        .order_by("section__position", "section__code", "position", "key")
    )
    tally, rows = {}, []
    for case in cases:
        result = latest.get(case.pk)
        state = result.status if result else "todo"
        tally[state] = tally.get(state, 0) + 1
        if result:
            result.run = run
        rows.append(
            {
                "key": case.key,
                "title": case.title,
                "priority": case.priority,
                "section": case.section.code,
                "status": state,
                "result": _result_data(result) if result else None,
            }
        )
    return {"run": _run_data(run), "tally": tally, "cases": rows}


@endpoint("GET")
def application_list(request):
    return Response(applications_data())


@endpoint("GET", "PUT")
def suite(request, slug):
    """The whole script as nested JSON. PUT the same shape back to create or update it."""
    app = _app(slug)
    changes = None
    if request.method == "PUT":
        payload = SuiteInput(data=request.data)
        payload.is_valid(raise_exception=True)
        prune = request.query_params.get("prune") == "1"
        changes = services.upsert_suite(app, payload.validated_data["sections"], prune=prune)
    data = _suite_data(app)
    if changes is not None:
        data["changes"] = changes
    return Response(data)


@endpoint("POST")
def case_create(request, slug):
    app = _app(slug)
    payload = CaseWrite(data=request.data)
    payload.is_valid(raise_exception=True)
    fields = dict(payload.validated_data)
    section = Section.objects.filter(source_application=app, code=fields.pop("section")).first()
    if section is None:
        return Response({"section": ["No area with that code. PUT the suite to add areas."]}, status=400)
    if Case.objects.filter(source_application=app, key=fields["key"]).exists():
        return Response({"key": ["A case with this key already exists; PATCH it instead."]}, status=400)
    case = Case.objects.create(source_application=app, section=section, position=section.cases.count(), **fields)
    return Response(_case_data(case), status=status.HTTP_201_CREATED)


@endpoint("GET", "PATCH", "DELETE")
def case_detail(request, slug, key):
    app = _app(slug)
    case = get_object_or_404(Case.objects.select_related("section"), source_application=app, key=key)
    if request.method == "DELETE":
        # Deactivate rather than delete: the case's results are its history.
        case.is_active = False
        case.save(update_fields=["is_active", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)
    if request.method == "PATCH":
        payload = CaseWrite(data=request.data, partial=True)
        payload.is_valid(raise_exception=True)
        fields = dict(payload.validated_data)
        if "section" in fields:
            section = Section.objects.filter(source_application=app, code=fields.pop("section")).first()
            if section is None:
                return Response({"section": ["No area with that code."]}, status=400)
            case.section = section
        for name, value in fields.items():
            setattr(case, name, value)
        try:
            with transaction.atomic():
                case.save()
        except IntegrityError:
            return Response({"key": ["Another case already uses this key."]}, status=400)
    return Response(_case_data(case))


@endpoint("GET")
def case_history(request, slug, key):
    app = _app(slug)
    case = get_object_or_404(Case.objects.select_related("section"), source_application=app, key=key)
    results = case.results.select_related("run", "tester")
    return Response({"case": _case_data(case), "results": [_result_data(r) for r in results]})


@endpoint("GET", "POST")
def runs(request, slug):
    app = _app(slug)
    if request.method == "POST":
        payload = RunInput(data=request.data)
        payload.is_valid(raise_exception=True)
        run = services.start_run(app, request.user, **payload.validated_data)
        return Response(_run_data(run), status=status.HTTP_201_CREATED)
    return Response([_run_data(run) for run in Run.objects.filter(source_application=app)])


@endpoint("GET")
def current_run(request, slug):
    """Latest status of every active case in the open run — enough to summarise or draft issues."""
    data = current_run_data(_app(slug))
    if data is None:
        return Response({"detail": "No run is open."}, status=status.HTTP_404_NOT_FOUND)
    return Response(data)
