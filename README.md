# HayOps

A self-hosted support desk for teams that run several products. One shared agent
dashboard; per-product branding, customer portals, email addresses and an
embeddable widget. Django + Postgres, with no Redis or other moving parts.

Built and run in production by [Devmo](https://devmo.llc), released under the
[MIT license](LICENSE).

HayOps was previously named Helpdesk. The widget's `window.HelpdeskWidget` API, its
`helpdesk-widget.js` bundle and the `X-Helpdesk-Signature` webhook header keep that
name so existing integrations don't break.

## Features

**Unified agent dashboard** (staff-only, on the admin domain)
- Single queue across all applications with filters (status, priority, queue,
  application, assignee), free-text search, sorting, and per-user saved views.
- Live updates: the queue and ticket timelines poll every 10 seconds (HTMX,
  204-short-circuited against a cached marker, so idle polls skip the query).
- Sound alerts: the open tab beeps on new tickets and customer replies
  (WebAudio, no assets); mute toggle in the nav persists per browser.
- Ticket lifecycle: claim (with row locking), assign, statuses
  (new/open/pending/resolved/closed/merged), priorities, tags, queues, merge,
  internal notes vs public replies, canned responses with usage counts,
  attachments, full audit timeline, agent-collision banner.
- Reporting dashboard: volume, first-response times, SLA compliance, agent
  workload, CSAT, per-application breakdown.

**Multiple applications, one install**
- `SourceApplication` model holds per-app API credentials, allowed widget
  origins, branding (product name, primary color, logo, email signature),
  `portal_domain` (e.g. `support.example.com`) and `mail_domain`
  (e.g. `mail.example.com`).
- Admins create new applications entirely from Django admin; DNS/SES
  provisioning is one command (`just ses-setup <domain>`).
- **Branding edits are cached for 5 minutes.** `SourceApplicationMiddleware`
  caches the resolved `SourceApplication` per portal hostname for 300s
  (`apps/core/middleware.py`), so a changed product name, color, logo or
  signature keeps serving the old value on the portal and in outgoing email
  until the entry expires. To pick a change up immediately, delete the key:
  `cache.delete("source_app_domain:support.<domain>")`.

**Branded customer portals** — each `portal_domain` serves:
- a public submit-a-ticket form (honeypot + per-IP rate limit),
- the knowledge base filtered to that app's articles plus global ones,
- magic-link ticket views where customers can read and reply,
- CSAT rating pages.
Agent/admin routes 404 on portal hosts.

**Email, both directions (AWS SES)**
- Inbound: SES receipt rule → S3 → SNS → webhook → background task. The
  recipient's domain selects the application. Reply threading via `[HD-xxxx]`
  subject tags, `ticket+hd-xxxx@` plus-addresses, and a Message-ID/References
  fallback; Message-ID dedupe; SES spam/virus verdicts quarantine to `failed/`;
  senders must be ticket participants to append to an existing ticket. A
  5-minute S3 sweep catches missed SNS notifications.
- Outbound: branded HTML notifications from `ticket+<ref>@<mail_domain>` —
  auto-ack with magic link on creation, agent replies, status changes, CSAT
  survey on resolve — plus plain-text team alerts to `SUPPORT_NOTIFY_EMAIL`
  on new tickets and customer replies.

**Embeddable JavaScript widget** (versioned at `/widget/v<x>/helpdesk-widget.js`)
- Shadow-DOM panel: list tickets, view, create, reply. HMAC user auth or
  anonymous app-key mode.
- Themes itself from the application's branding via a config endpoint.
- Captures debugging context on submit: page URL, user agent, viewport,
  screen, timezone, referrer, platform, a rolling buffer of recent console
  errors, optional annotated screenshot, and host-app state provided through
  `window.HelpdeskWidget.setContext({...})`. Agents see it all in the ticket
  sidebar. Integration guide: [docs/INTEGRATE.md](docs/INTEGRATE.md).

**Also**: knowledge base (global or per-app articles), outbound webhooks
(HMAC-signed, retried) per application, SLA policies with due-date stamping,
CSAT collection and reporting, custom field definitions per application.

**QC testbench** (`/testbench/`) — a shared manual regression script per application:
areas of test cases, named test runs, live claims ("working on it"), pass/fail/blocked
results with notes and screenshots kept as each case's history, and prefilled GitHub
issues for failures. Cases are edited in the UI, imported and exported as JSON, or managed
through a token-authenticated REST API (`/api/testbench/`).

**MCP server** (`/mcp`) — lets Claude download, validate and update an application's QC
suite, with a best-practice guide and the suite's JSON schema. It is built on the official
MCP SDK, which only speaks ASGI, so the app runs as ASGI. Setup and usage:
[docs/TESTBENCH.md](docs/TESTBENCH.md).

## Stack

Django 5.2 · PostgreSQL (data, cache, and Procrastinate background jobs) ·
HTMX 2 + Alpine 3 + Tailwind (django-tailwind-cli) ·
DRF (widget and testbench APIs) · MCP Python SDK (`/mcp`) · gunicorn with uvicorn
workers (ASGI) · AWS SES/S3/SNS (email) · Cloudflare R2 (media) ·
Vite (widget bundle) · Docker + Traefik + GitHub Actions (deploy).

## Local development

Requires [uv](https://docs.astral.sh/uv/), [just](https://just.systems/), Docker,
and Node 20+ (only for building the widget).

```bash
cp .env.example .env
just setup          # install deps, start Postgres, migrate, build CSS
just seed           # example data: admin login, an "Acme" app with its own portal
just dev            # dev server + Tailwind watcher + background worker
```

Then open <http://localhost:8000/> for the staff UI (`admin@helpdesk.local` /
`admin123`) and <http://acme.localhost:8000/> for Acme's customer portal. The
hostname picks the portal: `ADMIN_DOMAIN` serves the staff UI, and any other host
is matched against an application's `portal_domain`.

Notes:
- Docker maps Postgres to host port **5532**; `.env` must contain
  `DATABASE_PORT=5532` (settings read discrete `DATABASE_*` vars).
- Email prints to the console in dev (`EMAIL_BACKEND` in `.env`). Nothing is sent
  at all without the worker (`just worker`, or `just dev` which includes it).
- `just seed` refuses to run unless `DEBUG=True`; its admin password is public.
- `manage.py seed_helpdesk_app` creates the application the helpdesk's own
  built-in widget uses.

## Testing

```bash
just test           # pytest; fixtures live in the root conftest.py
just ci             # lint + format check + django check + tests
```

If you run suites in parallel (e.g. multiple agents/worktrees), isolate the
test database per run: `DATABASE_NAME=helpdesk_<name> uv run pytest ...`.

## Deployment

The app ships as one Docker image (`Dockerfile`) that serves the web app; run a
second container from the same image as the background worker. See
[deploy/README.md](deploy/README.md) for an example compose file, the required
environment, and how the GitHub Actions deploy workflows are configured.

- `just ses-setup <domain>` provisions a new app's domains: Cloudflare CNAME
  for `support.<domain>`, MX/SPF/DMARC/DKIM for `mail.<domain>`, SES identity,
  and the SES receipt rule (S3 + SNS actions, rule set auto-activated).
  Requires `CLOUDFLARE_API_TOKEN` (the justfile loads `.env`) and AWS
  credentials. Status check: `just ses-status <domain>`.
- Required prod env: `SECRET_KEY`, `ALLOWED_HOSTS` (the admin domain and every
  portal domain, plus `localhost` for the container health check),
  `CSRF_TRUSTED_ORIGINS`, `ADMIN_DOMAIN`, `SITE_URL`, `DEFAULT_FROM_EMAIL`,
  `DEFAULT_EMAIL_DOMAIN`, SES/R2 credentials and buckets. `.env.example` lists
  every variable the app reads.
- Behind a CDN or reverse proxy, set `CLIENT_IP_HEADER` (e.g.
  `HTTP_CF_CONNECTING_IP`) so rate limits see real client addresses -- but only
  if the app is unreachable except through that proxy.

### Storage: three buckets, two clouds, two credential namespaces

| Bucket | Cloud | Holds | Access |
| --- | --- | --- | --- |
| `R2_MEDIA_BUCKET_NAME` | R2 | branding logos | **public**, stable URLs |
| `R2_STORAGE_BUCKET_NAME` | R2 | ticket attachments | private, presigned |
| `SES_INBOUND_BUCKET` | **AWS S3** | raw inbound email | private, IAM |

SES writes inbound mail to S3 directly and cannot target R2, so that bucket has
to stay on AWS.

Credentials must not share setting names: django-ses falls back to
`settings.AWS_ACCESS_KEY_ID`, and django-storages falls back to the
`AWS_ACCESS_KEY_ID` *env var*. An R2 key on an `AWS_*` name therefore makes one
service authenticate with the other's credentials — SES stops sending mail.

| Purpose | Env vars |
| --- | --- |
| R2 (media) | `R2_ENDPOINT_URL`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_MEDIA_BUCKET_NAME`, `R2_STORAGE_BUCKET_NAME`, `R2_PUBLIC_HOST` |
| AWS (email in + out) | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `SES_INBOUND_BUCKET`, `SES_AWS_REGION` |

R2 config reaches django-storages through `STORAGES[...]["OPTIONS"]`
(`config/settings/base.py`), which takes precedence over both fallbacks. Never
reintroduce `AWS_STORAGE_BUCKET_NAME` / `AWS_S3_ENDPOINT_URL` / `AWS_*_KEY`
aliases for R2 — `apps/core/tests/test_storage_settings.py` fails if you do.

Django sees two storages. `default` is the private bucket and takes everything
by default, including ticket attachments (`apps/tickets/models.py`); its URLs are
presigned and expire after an hour. `public_media` is the public bucket and is
opt-in per field via `storage=public_media_storage` — currently only
`SourceApplication.logo`, because presigned logo URLs would dead-link in email
that has already been delivered. `R2_PUBLIC_HOST` is that bucket's public
hostname without a scheme, e.g. `pub-<hash>.r2.dev`,
serving `https://<R2_PUBLIC_HOST>/media/<upload_to>/<file>`. Anything you point
at `public_media` is readable by anyone holding the URL.

If the product apps share a Traefik proxy with the helpdesk, give the helpdesk
router a higher `priority`; the products' own routers would otherwise capture the
`support.*` hosts.

## Adding a new application

1. Django admin → Source Applications → Add: name, slug, portal domain
   (`support.<domain>`), mail domain (`mail.<domain>`), branding, allowed
   widget origins.
2. `just ses-setup <domain>` for DNS + SES.
3. Route `support.<domain>` to the helpdesk in your reverse proxy, and add it to
   `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` in the prod env; deploy.
4. Embed the widget in the app using the credentials from step 1
   ([docs/INTEGRATE.md](docs/INTEGRATE.md)).

In-app documentation for the support team lives at `/help/` (linked from the
dashboard nav), including how to run the QC testbench and connect Claude to the MCP server.

## Contributing and security

See [CONTRIBUTING.md](CONTRIBUTING.md). Report vulnerabilities privately as
described in [SECURITY.md](SECURITY.md), not in public issues.

## License

[MIT](LICENSE).

The widget's screenshot annotation uses [marker.js 2](https://markerjs.com/) under
its linkware license, which requires the attribution link it shows while annotating
to stay visible. Buy a marker.js license if you want to remove it.
