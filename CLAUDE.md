# helpdesk

Django support desk. Multi-tenant by hostname: `SourceApplication.portal_domain` selects
branding and scopes customer-facing views; `ADMIN_DOMAIN` serves the staff UI.

Postgres is the database, the cache (`django_cache`), and the job queue (procrastinate).
There is no Redis. Background work needs a running worker — `just worker`, or `just dev`
which runs server + tailwind + worker together. Without it no email is ever sent.

## Deployment

### Branch model

| Branch | Role | Deploys to | Trigger |
| --- | --- | --- | --- |
| `dev` | Default branch. All work integrates here. | — | CI only |
| `test` | Mirror of `dev` under validation. | test env | push |
| `main` | Production. What is released. | prod | version tag on `main` |

Promotion is strictly one direction: **`dev` → `test` → `main`**.

**Never commit directly to `test` or `main`.** They only ever receive `dev`. Keeping them
fast-forwardable is what makes promotion a no-op instead of a merge negotiation — `test`
previously accumulated its own merge commits, which is why `git push origin dev:test` gets
rejected as non-fast-forward. If that happens again, reset the branch to `dev` rather than
merging your way out of it.

Hotfixes are not an exception. They start on `dev` and promote through `test` like anything
else. A fix committed straight to `main` is a fix `dev` does not have, and the next release
silently reverts it.

### Releasing

```bash
# 1. Land work on dev; CI must be green.
git switch dev && git push origin dev

# 2. Promote to test. This deploys.
git push origin dev:test

# 3. Verify on the test environment before going further.

# 4. Promote to production.
git push origin dev:main

# 5. Cut the release. THIS is what deploys prod.
git tag v1.4.0 && git push origin v1.4.0
```

Merging to `main` does not ship by itself — the version tag does. That split is deliberate:
it keeps `main` mergeable without every merge reaching customers, and the tag names exactly
what is running in production. Roll back by pushing an earlier tag.

Version numbers are semver, `vMAJOR.MINOR.PATCH`. Features bump the minor, fixes the patch.

### Migrations

Deploys run `manage.py migrate` on the server. Two rules make that safe:

**Migrations must be backward-compatible with the code already running.** The container is
replaced and the schema is changed in separate steps, so for a window both old code and new
schema are live. Additive changes (new nullable column, new table) are fine. Destructive
changes are a two-release expand/contract: add the new shape and write to both in release N,
drop the old shape in release N+1.

**Never edit a migration that has been applied anywhere.** Renaming or rewriting one leaves
`django_migrations` pointing at a name that no longer exists, and the next deploy tries to
re-apply a change already in the schema. That is exactly the failure that blocked the test
deploy on 2026-08-02: `tickets_comment.email_message_id` existed while
`tickets.0004_add_comment_email_message_id` was unrecorded, so `migrate` aborted with
`DuplicateColumn`. Recovering means `migrate <app> <number> --fake` on that server after
confirming the live column matches the migration exactly — including its index.

Run `manage.py makemigrations --check --dry-run` before pushing; a model change without its
migration passes tests locally and fails on the server.

### Infrastructure

This repo builds the image; it does not hold any environment's compose files or `.env`.
The deploy workflows SSH in and run whatever `TEST_DEPLOY_COMMAND` / `PROD_DEPLOY_COMMAND`
(repository variables) say. `deploy/README.md` documents those variables and has an example
compose stack. `.env.example` is the only env template and lists every variable the app
reads -- keep it in sync when adding a `config(...)` call.

### Who runs migrations

Exactly one process, and it is not the workflow. The app image runs
`migrate --noinput && exec gunicorn` in its `CMD`, so a container never serves against a
schema it predates; the worker overrides `command:` and does not migrate.
The deploy command should also migrate with the new image *before* the swap, which is
what protects the worker. These are sequential, so the second is a no-op.

**Do not add a `migrate` step to a deploy workflow.** One did exist, and on 2026-08-02 it
raced the container's own boot migration — both processes read `django_migrations`, both
saw `tickets.0004` unapplied, both tried to add the column, and the loser failed the deploy
with `DuplicateColumn`. The schema was fine; the job was not.

### Verifying a deploy

Health is checked from inside the container:

```bash
docker exec helpdesk-<env>-app curl -sf -o /dev/null -w '%{http_code}' \
  -H 'Host: localhost' -H 'X-Forwarded-Proto: https' http://localhost:8000/ht/
```

Both headers are required. `SECURE_SSL_REDIRECT` is on in production settings, so without
`X-Forwarded-Proto` you get a redirect to `https://localhost:8000` and a TLS error against
the plaintext port. `Host: localhost` matches the `ALLOWED_HOSTS` fallback in `base.py`, so
the check works regardless of how the environment's `ALLOWED_HOSTS` is set — check the
public URL too if you want to prove real traffic works.

### ASGI and /mcp

The app image serves ASGI: gunicorn with uvicorn workers on `config/asgi.py`. `/mcp` is the
testbench MCP server, built on the official MCP SDK, which only speaks ASGI; every other path
goes to Django unchanged. `just server` still uses runserver, which does not serve `/mcp` —
`docs/TESTBENCH.md` shows how to run it locally.

### Still outstanding

Nothing. CI enforces `ruff check`, `makemigrations --check`, and the test suite; both
deploys gate on it; `dev` is the default branch; and the pre-swap migrate is live on the
server.

`*/migrations/*` is excluded from ruff — Django writes those, and reformatting them only
obscures whether a migration still matches what `makemigrations` generated.
