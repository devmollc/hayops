"""The MCP server at /mcp: its tools, guide and prompt, and the token gate in front of it."""

import pytest
from asgiref.sync import sync_to_async
from mcp import Client

from apps.accounts.models import User
from apps.testbench import services
from apps.testbench.api import CaseInput, SectionInput
from apps.testbench.mcp_server import BearerTokenMiddleware, CaseModel, SectionModel, mcp
from apps.testbench.models import ApiToken, Case

SUITE = {
    "sections": [
        {
            "code": "ISO",
            "name": "Tenant isolation",
            "cases": [
                {"key": "ISO-01", "title": "Another organization's subdomain is unreadable", "priority": "P0"},
                {"key": "ISO-02", "title": "Direct link is refused"},
            ],
        }
    ]
}


def mcp_test(test):
    # Tools run in the SDK's worker threads on their own database connections, so these
    # tests commit their data (transaction=True) rather than hide it in the test's transaction.
    return pytest.mark.anyio(pytest.mark.django_db(transaction=True)(test))


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def call(name, **arguments):
    async with Client(mcp) as client:
        return await client.call_tool(name, arguments)


def test_tool_schema_has_the_serializer_fields():
    assert set(CaseModel.model_fields) == set(CaseInput().fields)
    assert set(SectionModel.model_fields) == set(SectionInput().fields)


@mcp_test
async def test_validate_is_a_dry_run_and_put_saves(source_app):
    listed = await call("list_applications")
    assert source_app.slug in [app["slug"] for app in listed.structured_content["applications"]]

    checked = await call("validate_suite", application=source_app.slug, suite=SUITE)
    assert not checked.is_error, checked.content
    assert checked.structured_content["would_change"] == {"created": 2, "updated": 0, "deactivated": 0}
    assert not await sync_to_async(Case.objects.filter(source_application=source_app).exists)()

    saved = await call("put_suite", application=source_app.slug, suite=SUITE)
    assert saved.structured_content["changes"]["created"] == 2
    suite = await call("get_suite", application=source_app.slug)
    assert [c["key"] for c in suite.structured_content["sections"][0]["cases"]] == ["ISO-01", "ISO-02"]


@mcp_test
async def test_malformed_drafts_and_unknown_apps_are_tool_errors(source_app):
    bad = {"sections": [{"code": "ISO", "name": "Isolation", "cases": [{"key": "ISO 01", "title": "Space in key"}]}]}

    rejected = await call("put_suite", application=source_app.slug, suite=bad)
    unknown = await call("get_suite", application="no-such-app")

    assert rejected.is_error
    assert not await sync_to_async(Case.objects.filter(source_application=source_app).exists)()
    assert unknown.is_error and "list_applications" in unknown.content[0].text


@mcp_test
async def test_save_and_delete_case_keep_history(source_app, staff_user):
    await sync_to_async(services.upsert_suite)(source_app, SUITE["sections"])
    run = await sync_to_async(services.start_run)(source_app, staff_user, "v1.4.0 regression")
    case = await sync_to_async(Case.objects.get)(source_application=source_app, key="ISO-02")
    await sync_to_async(services.record_result)(run, case, staff_user, "fail", notes="Link opened B's record")

    added = await call(
        "save_case", application=source_app.slug, section="ISO", case={"key": "ISO-03", "title": "Exports stay in org"}
    )
    deleted = await call("delete_case", application=source_app.slug, key="ISO-02")
    history = await call("get_case_history", application=source_app.slug, key="ISO-02")

    assert added.structured_content["created"] is True
    assert not deleted.is_error
    assert history.structured_content["active"] is False
    assert history.structured_content["results"][0]["notes"] == "Link opened B's record"


@mcp_test
async def test_guide_schema_and_prompt_are_served():
    async with Client(mcp) as client:
        guide = await client.read_resource("testbench://guide")
        schema = await client.read_resource("testbench://suite-schema")
        prompt = await client.get_prompt("draft_qc_suite", {"application": "appsfolio"})

    assert "Writing a testbench QC suite" in guide.contents[0].text
    assert '"sections"' in schema.contents[0].text
    assert "`appsfolio`" in prompt.messages[0].content.text


@mcp_test
async def test_only_active_staff_tokens_reach_the_server(staff_user):
    customer = await sync_to_async(User.objects.create_user)(email="customer@example.com", password="x")
    _, staff_token = await sync_to_async(ApiToken.issue)(staff_user, "claude")
    _, customer_token = await sync_to_async(ApiToken.issue)(customer, "claude")

    async def server(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    gate = BearerTokenMiddleware(server)

    async def status(authorization=None):
        sent = []
        headers = [(b"authorization", authorization.encode())] if authorization else []

        async def receive():
            return {"type": "http.request", "body": b""}

        async def send(message):
            sent.append(message)

        await gate({"type": "http", "method": "POST", "path": "/mcp", "headers": headers}, receive, send)
        return sent[0]["status"]

    assert await status() == 401
    assert await status("Bearer not-a-token") == 401
    assert await status(f"Bearer {customer_token}") == 401
    assert await status(f"Bearer {staff_token}") == 200
