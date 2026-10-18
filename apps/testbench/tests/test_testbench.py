"""Testbench: claims, results and history, suite upserts, token auth, and the board's poll."""

import json
from urllib.parse import unquote_plus

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.testbench import services
from apps.testbench.models import ApiToken, Case, Run
from apps.testbench.services import BenchError

SUITE = [
    {
        "code": "ISO",
        "name": "Tenant isolation",
        "cases": [
            {
                "key": "ISO-01",
                "title": "Another organization's subdomain is unreadable",
                "priority": "P0",
                "steps": ["Sign in as A", "Open B's subdomain"],
                "expected": "Refused",
            },
            {"key": "ISO-02", "title": "Direct link is refused"},
        ],
    }
]


@pytest.fixture
def other_staff(db):
    return User.objects.create_user(email="tester2@devmosolutions.com", password="x", is_staff=True)


@pytest.fixture
def case(source_app):
    services.upsert_suite(source_app, SUITE)
    return Case.objects.get(source_application=source_app, key="ISO-01")


@pytest.fixture
def run(source_app, staff_user):
    return services.start_run(source_app, staff_user, "v1.4.0 regression")


def token_client(user):
    _, raw_token = ApiToken.issue(user, "claude")
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw_token}")
    return client


# ── claims, results, history ─────────────────────────────────────────────


@pytest.mark.django_db
def test_second_claim_is_refused(run, case, staff_user, other_staff):
    services.claim(run, case, staff_user)
    with pytest.raises(BenchError, match="already being worked on"):
        services.claim(run, case, other_staff)


@pytest.mark.django_db
def test_record_completes_own_claim_and_refuses_someone_elses(run, case, staff_user, other_staff):
    claimed = services.claim(run, case, staff_user)
    with pytest.raises(BenchError, match="Release the claim"):
        services.record_result(run, case, other_staff, "pass")

    result = services.record_result(run, case, staff_user, "fail", notes="500 on submit")

    assert result.pk == claimed.pk
    assert result.status == "fail" and result.completed_at is not None


@pytest.mark.django_db
def test_retest_adds_a_row_and_the_newest_is_current(run, case, staff_user):
    first = services.record_result(run, case, staff_user, "fail")
    second = services.record_result(run, case, staff_user, "pass")

    assert first.pk != second.pk
    assert services.latest_results(run)[case.pk].pk == second.pk
    assert case.results.count() == 2


@pytest.mark.django_db
def test_nothing_is_recorded_without_an_open_run(source_app, case, staff_user):
    with pytest.raises(BenchError, match="Start a run"):
        services.claim(services.open_run(source_app), case, staff_user)


@pytest.mark.django_db
def test_starting_a_run_closes_the_open_one(source_app, staff_user):
    first = services.start_run(source_app, staff_user, "one")
    second = services.start_run(source_app, staff_user, "two")

    first.refresh_from_db()
    assert first.closed_at is not None
    assert services.open_run(source_app) == second
    with pytest.raises(IntegrityError), transaction.atomic():
        Run.objects.create(source_application=source_app, name="a second open run")


@pytest.mark.django_db
def test_issue_link_is_prefilled_and_capped(source_app, run, case, staff_user):
    source_app.github_repo = "wshayes/appsfolio"
    source_app.save()
    result = services.record_result(run, case, staff_user, "fail", notes="x" * 20000)

    link = services.github_issue_link(result)

    assert link.startswith("https://github.com/wshayes/appsfolio/issues/new?")
    assert len(link) <= services.MAX_ISSUE_URL_CHARS
    assert "[ISO-01]" in unquote_plus(link) and "Open B's subdomain" in unquote_plus(link)


@pytest.mark.django_db
def test_issue_url_must_point_at_github(run, case, staff_user):
    result = services.record_result(run, case, staff_user, "fail")
    with pytest.raises(BenchError):
        services.update_result(result, "", issue_url="https://evil.example/issues/1")

    services.update_result(result, "filed", issue_url="https://github.com/wshayes/appsfolio/issues/212")
    result.refresh_from_db()
    assert result.github_issue_number == "212"


# ── suites ───────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_upsert_updates_prunes_and_keeps_history(source_app, run, case, staff_user):
    services.record_result(run, case, staff_user, "pass")

    changes = services.upsert_suite(
        source_app, [{"code": "ISO", "name": "Isolation", "cases": [{"key": "ISO-02", "title": "Renamed"}]}], prune=True
    )

    assert changes == {"created": 0, "updated": 1, "deactivated": 1}
    case.refresh_from_db()
    assert not case.is_active and case.results.count() == 1
    assert Case.objects.get(source_application=source_app, key="ISO-02").title == "Renamed"


# ── API ──────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_api_suite_round_trips(source_app, staff_user):
    api = token_client(staff_user)
    url = reverse("testbench-api-suite", args=[source_app.slug])

    created = api.put(url, {"sections": SUITE}, format="json")
    assert created.status_code == 200, created.data
    assert created.data["changes"]["created"] == 2

    fetched = api.get(url).data
    assert [c["key"] for c in fetched["sections"][0]["cases"]] == ["ISO-01", "ISO-02"]
    again = api.put(url, fetched, format="json")
    assert again.data["changes"] == {"created": 0, "updated": 2, "deactivated": 0}


@pytest.mark.django_db
def test_api_edits_and_deletes_a_case(source_app, case, staff_user):
    api = token_client(staff_user)
    url = reverse("testbench-api-case", args=[source_app.slug, case.key])

    assert api.patch(url, {"title": "New title"}, format="json").data["title"] == "New title"
    assert api.patch(url, {"section": "NOPE"}, format="json").status_code == 400
    assert api.delete(url).status_code == 204
    case.refresh_from_db()
    assert not case.is_active


@pytest.mark.django_db
@pytest.mark.parametrize("header", ["", "Bearer not-a-token", "Token abc"])
def test_api_refuses_missing_or_bad_tokens(header):
    res = APIClient().get(reverse("testbench-api-apps"), HTTP_AUTHORIZATION=header)
    assert res.status_code == 401


@pytest.mark.django_db
def test_api_refuses_non_staff_and_deactivated_users():
    customer = User.objects.create_user(email="customer@example.com", password="x")
    assert token_client(customer).get(reverse("testbench-api-apps")).status_code == 403

    gone = User.objects.create_user(email="gone@devmosolutions.com", password="x", is_staff=True, is_active=False)
    assert token_client(gone).get(reverse("testbench-api-apps")).status_code == 401


# ── staff pages ──────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_board_is_staff_only(client, source_app):
    client.force_login(User.objects.create_user(email="customer@example.com", password="x"))
    assert client.get(reverse("testbench:board", args=[source_app.slug])).status_code == 403


@pytest.mark.django_db
def test_board_poll_is_204_until_someone_else_changes_something(client, staff_user, other_staff, run, case):
    client.force_login(staff_user)
    url = reverse("testbench:board", args=[run.source_application.slug])
    marker = client.get(url).context["marker"]

    def poll():
        return client.get(url, {"marker": marker}, HTTP_HX_REQUEST="true")

    assert poll().status_code == 204
    services.claim(run, case, other_staff)
    res = poll()
    assert res.status_code == 200
    assert b"working on it" in res.content


def json_upload(payload):
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return SimpleUploadedFile("suite.json", body, content_type="application/json")


@pytest.mark.django_db
def test_import_json_adds_updates_and_prunes(client, source_app, staff_user, case):
    client.force_login(staff_user)
    suite = {"sections": [{"code": "ISO", "name": "Isolation", "cases": [{"key": "ISO-03", "title": "New case"}]}]}

    res = client.post(
        reverse("testbench:suite_import", args=[source_app.slug]), {"file": json_upload(suite), "prune": "on"}
    )

    assert res.status_code == 302
    assert Case.objects.get(source_application=source_app, key="ISO-03").title == "New case"
    case.refresh_from_db()
    assert not case.is_active


@pytest.mark.django_db
def test_import_rejects_bad_files_and_changes_nothing(client, source_app, staff_user):
    client.force_login(staff_user)
    url = reverse("testbench:suite_import", args=[source_app.slug])
    missing_title = {"sections": [{"code": "ISO", "name": "Isolation", "cases": [{"key": "ISO-09"}]}]}

    assert b"not valid JSON" in client.post(url, {"file": json_upload(b"{nope")}).content
    assert b"sections[0].cases[0].title" in client.post(url, {"file": json_upload(missing_title)}).content
    assert not Case.objects.filter(source_application=source_app).exists()


@pytest.mark.django_db
def test_addresses_in_cases_and_notes_become_new_tab_links(client, source_app, staff_user, run, case):
    """A tester follows these mid-run: a same-tab link would cost them the board and their claim."""
    case.setup = "Signed out at https://test.appsfol.io/"
    case.steps = ["Open https://test.appsfol.io/app/ht/"]
    case.save()
    services.record_result(run, case, staff_user, "fail", notes="Traceback at https://sentry.io/issues/42/")
    client.force_login(staff_user)

    board = client.get(reverse("testbench:board", args=[source_app.slug])).content.decode()
    history = client.get(reverse("testbench:case_detail", args=[source_app.slug, case.key])).content.decode()

    step_url = "https://test.appsfol.io/app/ht/"
    link = f'<a target="_blank" rel="noopener nofollow" href="{step_url}">{step_url}</a>'
    assert link in board and link in history
    note_url = "https://sentry.io/issues/42/"
    assert f'<a target="_blank" rel="noopener nofollow" href="{note_url}">{note_url}</a>' in history
    # One rel per tag: a second one is invalid and silently ignored by browsers.
    assert 'rel="nofollow"' not in board and 'rel="nofollow"' not in history


@pytest.mark.django_db
def test_recording_from_the_board_returns_the_updated_card(client, staff_user, run, case):
    client.force_login(staff_user)
    url = reverse("testbench:case_action", args=[run.source_application.slug, case.key, "record"])

    res = client.post(url, {"status": "fail", "notes": "500 on save"}, HTTP_HX_REQUEST="true")

    assert res.status_code == 200
    assert res.headers["HX-Trigger"] == "tb-changed"
    assert b"st-fail" in res.content and b"500 on save" in res.content
    result = case.results.get()
    assert result.tester == staff_user and result.notes == "500 on save"
