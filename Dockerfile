# Stage 1: Build the widget
FROM node:20-slim AS widget-builder

WORKDIR /widget
COPY widget/package.json widget/package-lock.json ./
RUN npm ci
COPY widget/ ./
RUN npm run build

# Stage 2: Build the Python app
FROM python:3.12-slim-bookworm AS python-builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_SYSTEM_PYTHON=1

RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential libpq-dev && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies first for layer caching
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy all source code
COPY . .

# Copy widget dist from stage 1
COPY --from=widget-builder /widget/dist/helpdesk-widget.js widget/dist/helpdesk-widget.js
COPY --from=widget-builder /widget/dist/vendor/ widget/dist/vendor/

# Install the project itself
RUN uv sync --frozen --no-dev

# Build Tailwind CSS
RUN DJANGO_SETTINGS_MODULE=config.settings.production \
    SECRET_KEY=build-only \
    DATABASE_NAME=x \
    DATABASE_USER=x \
    DATABASE_PASSWORD=x \
    DATABASE_HOST=x \
    uv run python manage.py tailwind build

# Collect static files with production backend (WhiteNoise hashing)
RUN DJANGO_SETTINGS_MODULE=config.settings.production \
    SECRET_KEY=build-only \
    DATABASE_NAME=x \
    DATABASE_USER=x \
    DATABASE_PASSWORD=x \
    DATABASE_HOST=x \
    uv run python manage.py collectstatic --noinput

# Stage 3: Production image
FROM python:3.12-slim-bookworm AS production

RUN apt-get update && \
    apt-get install -y --no-install-recommends libpq5 curl && \
    rm -rf /var/lib/apt/lists/*

RUN useradd --create-home appuser

WORKDIR /app

# Copy app + virtualenv from builder (.venv is inside /app)
COPY --from=python-builder /app .

# Activate the virtualenv so gunicorn, python, etc. resolve correctly
ENV VIRTUAL_ENV=/app/.venv
ENV PATH="/app/.venv/bin:$PATH"
# manage.py, asgi.py and wsgi.py default to development settings for local work.
# Pin production here so an env file that omits DJANGO_SETTINGS_MODULE cannot
# start the image with DEBUG on and the HTTPS/cookie hardening off.
ENV DJANGO_SETTINGS_MODULE=config.settings.production

USER appuser

EXPOSE 8000

# Migrate before serving, so a new image never takes traffic against a schema it
# predates. Deliberately in CMD, not ENTRYPOINT: the worker container overrides
# `command:` in compose, so exactly one container migrates and they can't race.
# ASGI via uvicorn workers: /mcp is the MCP SDK's Starlette app, which only speaks ASGI
# (config/asgi.py routes it; every other path is Django as before).
CMD ["sh", "-c", "python manage.py migrate --noinput && exec gunicorn config.asgi:application \
     --worker-class uvicorn_worker.UvicornWorker \
     --bind 0.0.0.0:8000 \
     --workers 3 \
     --max-requests 1000 \
     --forwarded-allow-ips '*'"]
