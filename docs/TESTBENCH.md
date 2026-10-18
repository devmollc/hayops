# Testbench

A shared manual QA script for each application, at `/testbench/` on the admin domain.
Each application has areas (e.g. `ISO`) holding test cases (e.g. `ISO-03`): setup,
numbered steps, an expected result, a priority, and a release-focus flag.

## For testers

1. Open **Testbench → your application**. Staff accounts only; you are recorded as the tester.
2. Someone starts a **run** ("v1.4.0 regression", with the build under test). Starting a
   new run closes the previous one; its results stay in each case's history.
3. **Claim** a case before you start so nobody else picks it up. Everyone's board shows
   the claim within about five seconds. **Release** hands it back; anyone can release a
   claim that was abandoned.
4. Record **Pass / Fail / Blocked**, with notes on what you saw. Recording completes your
   claim; recording again later is a retest and adds a new entry rather than replacing the
   old one.
5. For a failure, open the case's **History**, click **File GitHub issue** (it opens a
   prefilled issue in a new tab), add any screenshots to that issue, then **Edit result**
   and paste the issue URL back. Screenshots live on the issue, not in the testbench.

The issue button needs the application's repo: Django admin → Source applications →
Testbench → `github_repo`, as `owner/name`.

Deleting a case hides it from the board but keeps its history. A deleted case's key
cannot be reused from the UI; importing a file that contains it brings it back.

To load many cases at once, use **Import JSON** on the board's Manage panel. It takes the
suite JSON described below — the same file the API's `GET suite/` returns — and adds or
updates areas by code and cases by key. Tick "Delete cases that are not in the file" to
make the file the complete script. A file with any error imports nothing and lists what
to fix.

## MCP server — for Claude

The helpdesk is an MCP server at `https://helpdesk.example.com/mcp` (test environment:
`https://helpdesk-test.example.com/mcp`). It gives Claude the suite format as a schema,
a best-practice guide, and tools to download, check and save suites. That lets Claude, working
in an application's repo, compare the app's suite with what the app does today.

Connect Claude Code once per machine, using a personal token from `/testbench/tokens/`:

```bash
claude mcp add --scope user --transport http helpdesk https://helpdesk.example.com/mcp \
  --header "Authorization: Bearer <token>"
```

| Kind | Name | What it does |
|---|---|---|
| Tool | `list_applications` | Application slugs, case counts, open run |
| Tool | `get_suite` | Download an application's suite as JSON |
| Tool | `validate_suite` | Check a draft without saving it; reports what would be added, updated and deleted |
| Tool | `put_suite` | Save a suite, optionally deleting cases that are not in it |
| Tool | `save_case` / `delete_case` | Add or replace one case; delete one case (history is kept) |
| Tool | `get_case_history` | Every result for a case, with notes and issue links |
| Tool | `get_current_run` | Latest status of every case in the open run |
| Resource | `testbench://guide` | How to write a good QC suite ([`apps/testbench/qc_guide.md`](../apps/testbench/qc_guide.md)) |
| Resource | `testbench://suite-schema` | JSON Schema for the suite format |
| Prompt | `draft_qc_suite` | Compare an application's suite with its current code and propose changes |

From an application's repo, run the `draft_qc_suite` prompt from Claude Code's `/` menu, or
just ask Claude to update that application's QC suite. Writes act as the token's owner, and
Claude is told to validate and show you the changes before saving.

Locally, `just server` (runserver) does not serve `/mcp`. Run the ASGI app instead —
`uv run uvicorn config.asgi:application --port 8000` — and connect to
`http://localhost:8000/mcp`. Pages served that way lack CSS, because static files are only
collected in the Docker build; use runserver for the UI.

## REST API — for scripts

Base URL: `https://helpdesk.example.com/api/testbench/`
(test environment: `https://helpdesk-test.example.com/api/testbench/`).

Create a personal token at `/testbench/tokens/` and send it as
`Authorization: Bearer <token>`. It acts as you and only works for active staff accounts.
Everything is addressed by application slug and case key.

| Method | Path | What it does |
|---|---|---|
| GET | `` | Applications, their case counts and open run |
| GET | `<app>/suite/` | The whole script as nested JSON |
| PUT | `<app>/suite/` | Create or update areas and cases from that JSON. Add `?prune=1` to delete cases missing from it |
| POST | `<app>/cases/` | Add one case (`section` is the area code; the area must exist) |
| GET / PATCH / DELETE | `<app>/cases/<key>/` | Read, edit (any subset of fields), delete one case |
| GET | `<app>/cases/<key>/history/` | Every result for the case: outcome, tester, notes, issue link |
| GET / POST | `<app>/runs/` | List runs; start a run (`name`, optional `build`, `notes`) |
| GET | `<app>/runs/current/` | Every active case with its latest status in the open run |

### Suite JSON

`GET suite/` returns exactly what `PUT suite/` accepts, so the usual edit is fetch, change,
put back. List order is display order.

```json
{
  "sections": [
    {
      "code": "ISO",
      "name": "Tenant isolation",
      "note": "A failure here is a data leak, not a bug.",
      "cases": [
        {
          "key": "ISO-01",
          "title": "Another organization's subdomain is not readable",
          "priority": "P0",
          "release_focus": true,
          "setup": "Two organizations, A and B, and an account that belongs only to A.",
          "steps": ["Sign in as the A user.", "Change the address bar to B's subdomain."],
          "expected": "You are redirected back to your own organization, or refused."
        }
      ]
    }
  ]
}
```

`priority` is `P0` (must work; a failure blocks release), `P1` (important) or `P2`
(lower risk). `release_focus` marks cases covering what this release changed. Only `key`
and `title` are required.

### Recipes

```bash
export HD=https://helpdesk.example.com/api/testbench
auth=(-H "Authorization: Bearer $HELPDESK_TOKEN")

# Pull the suite, edit suite.json, push it back
curl -s "${auth[@]}" "$HD/acme/suite/" > suite.json
curl -s -X PUT "${auth[@]}" -H 'Content-Type: application/json' --data @suite.json "$HD/acme/suite/"

# Add, edit, delete one case
curl -s -X POST "${auth[@]}" -H 'Content-Type: application/json' "$HD/acme/cases/" \
  -d '{"section": "ISO", "key": "ISO-07", "title": "Exports never include another organization", "priority": "P0",
       "steps": ["Export as the A user."], "expected": "Only A rows."}'
curl -s -X PATCH "${auth[@]}" -H 'Content-Type: application/json' "$HD/acme/cases/ISO-07/" -d '{"priority": "P1"}'
curl -s -X DELETE "${auth[@]}" "$HD/acme/cases/ISO-07/"

# What failed in the current run?
curl -s "${auth[@]}" "$HD/acme/runs/current/" | jq '.cases[] | select(.status == "fail")'
```

`PUT suite/` without `?prune=1` never removes anything. With it, cases missing from the
payload are deleted, but their history is kept.

## Writing cases

How to structure, word and prioritise a suite is in
[`apps/testbench/qc_guide.md`](../apps/testbench/qc_guide.md) — the same text the MCP server
serves as `testbench://guide`.

To point Claude at the testbench from an application's own repo, add to that repo's `CLAUDE.md`:

> Manual QA cases for this app live in the helpdesk testbench (application slug `<slug>`).
> Use the `helpdesk` MCP server to read and update them, following its `testbench://guide`.
