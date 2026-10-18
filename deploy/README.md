# Deployment

## Running it

The `Dockerfile` builds one image for both roles:

- **app**: the default `CMD` runs `migrate`, then serves ASGI with gunicorn and
  uvicorn workers on port 8000. ASGI is required because `/mcp` is built on the
  MCP SDK, which only speaks ASGI. The image sets
  `DJANGO_SETTINGS_MODULE=config.settings.production`.
- **worker**: the same image with the command
  `python manage.py procrastinate worker`. It sends all email, processes inbound
  mail, delivers webhooks and runs the 5-minute inbound S3 sweep. Without it no
  email is ever sent.

Postgres is the only other service. It holds the data, the cache and the job queue,
so there is no Redis.

[`compose.example.yml`](compose.example.yml) wires the three together. Put a
TLS-terminating reverse proxy in front of the app, routing your `ADMIN_DOMAIN` and
every application's `portal_domain` to it. Fill in `.env` from
[`../.env.example`](../.env.example), which lists every variable the app reads.

Health check: `GET /ht/` with `Host: localhost` and `X-Forwarded-Proto: https`.
Both headers are needed. Without the second, `SECURE_SSL_REDIRECT` answers with a
redirect to https.

## Migrations

Exactly one process migrates: the app container, in its `CMD`, before it serves.
The worker does not migrate. If your deploy replaces the worker before the app,
run `manage.py migrate` with the new image first so the worker never runs against
an old schema. Do not add a separate migrate step that runs while the app
container boots. Two concurrent `migrate`s race, and the loser fails with
`DuplicateColumn`.

Migrations must stay backward-compatible with the code already running. During a
deploy the new schema and the old code are both live for a while. Destructive
changes take two releases (expand, then contract).

## GitHub Actions deploy

`.github/workflows/deploy-test.yml` (on push to `test`) and `deploy-prod.yml` (on a
`v*` tag that is on `main`) both work the same way:

1. Run CI.
2. Build the image and push it to `ghcr.io/<owner>/helpdesk`.
3. SSH to the server and run the deploy command.
4. Poll the health check.

They read their target from repository settings:

| Kind | Name | Used by |
| --- | --- | --- |
| variable | `TEST_SERVER_HOST`, `SERVER_HOST` | test, prod |
| variable | `SERVER_USER` | both |
| variable | `TEST_DEPLOY_COMMAND`, `PROD_DEPLOY_COMMAND` | test, prod |
| secret | `TEST_SERVER_SSH_KEY`, `SERVER_SSH_KEY` | test, prod |

The deploy command runs on the server after `docker login ghcr.io`. It should pull
the new image and recreate the containers, for example
`cd /srv/helpdesk && docker compose pull && docker compose up -d`. For prod,
`APP_VERSION` is exported as the release tag, so compose can pin the image to it.
A workflow fails at its first step if its command variable is unset.

The health check step expects the app container to be named
`helpdesk-test-app` / `helpdesk-prod-app`.
