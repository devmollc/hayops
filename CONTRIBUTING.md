# Contributing

Thanks for helping. Bug reports, fixes and focused features are all welcome. For
anything large, open an issue first so we can agree on the shape before you build it.

## Setup

You need [uv](https://docs.astral.sh/uv/), [just](https://just.systems/), Docker, and
Node 20+ for the widget.

```bash
cp .env.example .env
just setup      # deps, Postgres on port 5532, migrations, CSS
just seed       # example data: admin@helpdesk.local / admin123, Acme portal
just dev        # server + Tailwind watcher + background worker
```

`just --list` shows every recipe.

## Making a change

- Branch from `dev` and open your pull request against `dev`. `test` and `main` only
  ever receive `dev` and never take direct commits.
- Add or update tests for behaviour you change. A bug fix should come with a test that
  fails without it.
- Keep the diff focused. Don't reformat files you didn't otherwise touch.
- Update `.env.example` when you add a setting, and the docs when you change
  behaviour they describe.

## Checks

CI runs these, and your pull request needs them green:

```bash
uv run ruff check <files you touched>
uv run python manage.py makemigrations --check --dry-run
uv run pytest
```

`just ci` runs the whole suite, but parts of the codebase predate the current
ruff rules, so check the files you touched rather than reformatting everything.

## Migrations

Production deploys run old code against the new schema for a short window, so:

- Migrations must be backward-compatible with the code already running. Adding a
  table or a nullable column is fine. Dropping or renaming something takes two
  releases: add the new shape first, and remove the old one in a later release.
- Never edit or rename a migration that has been released. Add a new one instead.
- Commit the migration `makemigrations` generates as-is. `*/migrations/*` is
  excluded from ruff on purpose.

## Architecture in brief

- **Hostname is the tenant.** `ADMIN_DOMAIN` serves the staff UI. Any other host is
  matched against `SourceApplication.portal_domain` to serve that product's portal
  (`apps/core/middleware.py`). Customer-facing queries must stay scoped to the
  current application.
- **Postgres does everything:** data, cache, and the job queue
  ([Procrastinate](https://procrastinate.readthedocs.io/)). Email is only sent
  while a worker is running.
- **Frontend:** server-rendered Django templates with HTMX, Alpine and Tailwind.
  The embeddable widget in `widget/` is a separate Vite bundle.

## Security issues

Do not open a public issue. See [SECURITY.md](SECURITY.md).

## License

By contributing, you agree that your contributions are licensed under the
[MIT License](LICENSE).
