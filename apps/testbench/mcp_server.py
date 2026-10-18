"""The helpdesk's MCP server, at /mcp: testbench QC suites for Claude.

Claude uses it to download an application's suite, check a draft against the same rules
as the API and the Import JSON page, and save it. Setup and usage: docs/TESTBENCH.md.

The MCP SDK only serves ASGI, which is why config/asgi.py routes /mcp here and the
helpdesk runs under uvicorn workers. Tools are plain functions, so the SDK runs each one
in a worker thread, where the Django ORM is allowed.
"""

import json
from functools import wraps
from pathlib import Path
from typing import Annotated, Any, Literal

from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import close_old_connections, transaction
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field
from starlette.datastructures import Headers
from starlette.responses import JSONResponse

from apps.core.models import SourceApplication

from . import services
from .api import CaseInput, SuiteInput, _case_data, _result_data, _suite_data, applications_data, current_run_data
from .authentication import bearer_token, user_for_token
from .forms import _error_lines
from .models import Case, Section, key_validator
from .signals import bump_marker

GUIDE = Path(__file__).with_name("qc_guide.md")


def _max_length(model, field):
    return model._meta.get_field(field).max_length


# The suite format as the tools' input schema, so Claude sees exactly what is accepted.
# Patterns, choices and lengths come from the models; every write still goes through the
# same DRF serializers as the API and the Import JSON page.
class CaseModel(BaseModel):
    key: Annotated[
        str,
        Field(
            pattern=key_validator.regex.pattern,
            max_length=_max_length(Case, "key"),
            description="Area code plus a number, e.g. ISO-03. Never renumber or reuse a key that has history.",
        ),
    ]
    title: Annotated[
        str,
        Field(
            min_length=1,
            max_length=_max_length(Case, "title"),
            description="The outcome, e.g. 'Wrong password is rejected'.",
        ),
    ]
    priority: Annotated[
        Literal[tuple(Case.Priority.values)],
        Field(description="P0 must work (a failure blocks release), P1 important, P2 lower risk."),
    ] = Case.Priority.P1.value
    setup: Annotated[str, Field(description="State needed before step 1: account, role, data.")] = ""
    steps: Annotated[
        list[str], Field(description="1-6 imperative steps, one action each, using exact URLs and UI labels.")
    ] = []
    expected: Annotated[
        str, Field(description="An observable pass/fail statement, including what must not happen.")
    ] = ""
    release_focus: Annotated[bool, Field(description="True only if the upcoming release changed this.")] = False


class SectionModel(BaseModel):
    code: Annotated[
        str,
        Field(
            pattern=key_validator.regex.pattern,
            max_length=_max_length(Section, "code"),
            description="2-4 uppercase letters, e.g. ISO. It prefixes every key in the area.",
        ),
    ]
    name: Annotated[str, Field(min_length=1, max_length=_max_length(Section, "name"))]
    note: Annotated[str, Field(description="One or two sentences shown under the area heading.")] = ""
    cases: list[CaseModel] = []


class SuiteModel(BaseModel):
    sections: list[SectionModel]


mcp = MCPServer(
    "helpdesk",
    title="HayOps",
    instructions=(
        "Manual QC suites (the testbench) for each application the helpdesk serves. "
        "Read the testbench://guide resource before drafting or changing cases. "
        "Always run validate_suite before put_suite, and show the user the change summary before any write."
    ),
)

READ = ToolAnnotations(read_only_hint=True, open_world_hint=False)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
DELETE = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=False)

AppSlug = Annotated[str, Field(description="Application slug from list_applications, e.g. acme.")]
CaseKey = Annotated[str, Field(description="Case key, e.g. ISO-03.")]
Prune = Annotated[
    bool, Field(description="Also delete active cases missing from the suite. Deleted cases keep their history.")
]


def db_tool(fn):
    """Return JSON-safe data and release the worker thread's database connection.

    Django only recycles connections at the end of its own request cycle. The SDK's tool
    threads are pooled, so without this each one would hold a Postgres connection open.
    """

    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return json.loads(json.dumps(fn(*args, **kwargs), cls=DjangoJSONEncoder))
        finally:
            close_old_connections()

    return wrapper


def _app(slug):
    app = SourceApplication.objects.filter(slug=slug, is_active=True).first()
    if app is None:
        raise ToolError(f"No active application {slug!r}. Call list_applications for the slugs.")
    return app


def _validated(serializer):
    if not serializer.is_valid():
        raise ToolError("; ".join(_error_lines(serializer.errors)))
    return serializer.validated_data


class _DryRunError(Exception):
    """Raised to roll back validate_suite's trial upsert."""


@mcp.tool(annotations=READ)
@db_tool
def list_applications() -> dict[str, Any]:
    """Applications the helpdesk serves, with their active case counts and open test run."""
    return {"applications": applications_data()}


@mcp.tool(annotations=READ)
@db_tool
def get_suite(application: AppSlug) -> dict[str, Any]:
    """Download an application's whole QC suite: its areas in order, each with its active cases.

    The result is exactly the shape put_suite, validate_suite and the Import JSON page accept.
    """
    return _suite_data(_app(application))


@mcp.tool(annotations=READ)
@db_tool
def validate_suite(application: AppSlug, suite: SuiteModel, prune: Prune = False) -> dict[str, Any]:
    """Check a draft suite without saving it, and report what put_suite would add, update and delete."""
    app = _app(application)
    payload = SuiteInput(data=suite.model_dump())
    if not payload.is_valid():
        return {"valid": False, "errors": _error_lines(payload.errors)}
    try:
        with transaction.atomic():
            changes = services.upsert_suite(app, payload.validated_data["sections"], prune=prune)
            raise _DryRunError
    except _DryRunError:
        pass
    return {"valid": True, "errors": [], "would_change": changes}


@mcp.tool(annotations=WRITE)
@db_tool
def put_suite(application: AppSlug, suite: SuiteModel, prune: Prune = False) -> dict[str, Any]:
    """Save a suite: add or update areas by code and cases by key, in the order given.

    Run validate_suite and confirm its change summary with the user first.
    """
    app = _app(application)
    sections = _validated(SuiteInput(data=suite.model_dump()))["sections"]
    return {"changes": services.upsert_suite(app, sections, prune=prune)}


@mcp.tool(annotations=WRITE)
@db_tool
def save_case(
    application: AppSlug,
    section: Annotated[str, Field(description="Code of an existing area, e.g. ISO. Add areas with put_suite.")],
    case: CaseModel,
) -> dict[str, Any]:
    """Add one case to an area, or replace the case that already has this key."""
    app = _app(application)
    area = Section.objects.filter(source_application=app, code=section).first()
    if area is None:
        raise ToolError(f"No area {section!r} in {application}. Add areas with put_suite.")
    fields = _validated(CaseInput(data=case.model_dump()))
    existing = Case.objects.filter(source_application=app, key=fields["key"]).first()
    position = existing.position if existing and existing.section_id == area.pk else area.cases.count()
    saved, created = Case.objects.update_or_create(
        source_application=app,
        key=fields["key"],
        defaults={
            **{name: fields[name] for name in services.CASE_FIELDS},
            "section": area,
            "position": position,
            "is_active": True,
        },
    )
    return {"created": created, "case": _case_data(saved)}


@mcp.tool(annotations=DELETE)
@db_tool
def delete_case(application: AppSlug, key: CaseKey) -> dict[str, Any]:
    """Remove a case from the bench. Its result history is kept."""
    if not Case.objects.filter(source_application=_app(application), key=key, is_active=True).update(is_active=False):
        raise ToolError(f"No active case {key!r} in {application}.")
    bump_marker()  # update() skips the signals that refresh open boards
    return {"deleted": key}


@mcp.tool(annotations=READ)
@db_tool
def get_case_history(application: AppSlug, key: CaseKey) -> dict[str, Any]:
    """Every result recorded for a case across all runs: outcome, tester, notes, GitHub issue."""
    case = Case.objects.select_related("section").filter(source_application=_app(application), key=key).first()
    if case is None:
        raise ToolError(f"No case {key!r} in {application}.")
    results = case.results.select_related("run", "tester")
    return {"case": _case_data(case), "active": case.is_active, "results": [_result_data(r) for r in results]}


@mcp.tool(annotations=READ)
@db_tool
def get_current_run(application: AppSlug) -> dict[str, Any]:
    """The open test run: its tally, and the latest status of every active case."""
    data = current_run_data(_app(application))
    if data is None:
        raise ToolError(f"No test run is open for {application}.")
    return data


@mcp.resource("testbench://guide", title="Writing a testbench QC suite", mime_type="text/markdown")
def guide() -> str:
    """Best practice for QC suites: workflow, areas, case wording, priorities and coverage."""
    return GUIDE.read_text()


@mcp.resource("testbench://suite-schema", title="Suite JSON schema", mime_type="application/json")
def suite_schema() -> str:
    """JSON Schema for a suite, as get_suite returns it and put_suite, the API and Import JSON accept it."""
    return json.dumps(SuiteModel.model_json_schema(), indent=2)


@mcp.prompt(title="Draft or update a QC suite")
def draft_qc_suite(
    application: AppSlug,
    focus: Annotated[str, Field(description="What the upcoming release changed, or areas to concentrate on.")] = "",
) -> str:
    """Compare an application's testbench suite with its current functionality and propose updates."""
    lines = [
        f"Update the helpdesk testbench QC suite for the application `{application}`.",
        f"Focus on: {focus}" if focus else "",
        "",
        "1. Read the testbench://guide resource and follow it.",
        "2. Download the current suite with get_suite.",
        "3. Work out what the application does today from this repository: its pages and routes, roles "
        "and permissions, integrations, and recent changes. List features with no cases, stale cases, and "
        "steps that no longer match the UI.",
        "4. Draft the complete updated suite, keeping every existing key that still applies, and run "
        "validate_suite until it is valid.",
        "5. Show me the proposed additions, edits and deletions with the would_change summary, and wait for "
        "my approval before calling put_suite.",
    ]
    return "\n".join(lines)


class BearerTokenMiddleware:
    """Only active staff presenting a personal API token reach the MCP server."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            raw_token = bearer_token(Headers(scope=scope).get("authorization"))
            if not raw_token or not await sync_to_async(_is_staff_token)(raw_token):
                response = JSONResponse(
                    {
                        "error": "Send 'Authorization: Bearer <token>' for an active staff account. "
                        "Create a token at /testbench/tokens/."
                    },
                    status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def _is_staff_token(raw_token):
    try:
        user = user_for_token(raw_token)
        return bool(user and user.is_staff)
    finally:
        close_old_connections()


def _allowed_hosts():
    # DNS-rebinding protection: answer only on the admin domain (plus localhost in development).
    hosts = [settings.ADMIN_DOMAIN, *(["localhost", "127.0.0.1"] if settings.DEBUG else [])]
    return [value for host in hosts for value in (host, f"{host}:*")]


http_app = BearerTokenMiddleware(
    mcp.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(allowed_hosts=_allowed_hosts()),
    )
)
