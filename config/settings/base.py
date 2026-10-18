import json
from email.utils import formataddr, parseaddr
from pathlib import Path

from decouple import config

BASE_DIR = Path(__file__).resolve().parent.parent.parent

SECRET_KEY = config("SECRET_KEY")
DEBUG = config("DEBUG", default=False, cast=bool)
ALLOWED_HOSTS = config("ALLOWED_HOSTS", default="localhost,127.0.0.1", cast=lambda v: [s.strip() for s in v.split(",")])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",

    # SES bounce/complaint webhook (django_ses.views.SESEventWebhookView)
    "django_ses",
    "django.contrib.postgres",
    # Third-party
    "django_htmx",
    "django_tailwind_cli",
    "rest_framework",
    "corsheaders",
    "procrastinate.contrib.django",
    # Local apps
    "apps.accounts",
    "apps.core",
    "apps.tickets",
    "apps.widget_api",
    "apps.inbound_email",
    "apps.kb",
    "apps.reporting",
    "apps.webhooks",
    "apps.portal",
    "apps.testbench",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "apps.core.csp.ContentSecurityPolicyMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
    "apps.core.middleware.SourceApplicationMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.source_application",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": config("DATABASE_NAME", default="helpdesk"),
        "USER": config("DATABASE_USER", default="helpdesk"),
        "PASSWORD": config("DATABASE_PASSWORD", default="helpdesk"),
        "HOST": config("DATABASE_HOST", default="localhost"),
        "PORT": config("DATABASE_PORT", default="5432"),
        "CONN_MAX_AGE": 0,
    }
}

# Cache lives in Postgres (see apps/core/migrations/0006_cache_table.py, which builds
# django_cache UNLOGGED). Only used for poll short-circuits, viewer presence and the
# portal rate limit -- all cheap and all disposable, so losing it on crash is fine.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "django_cache",
        # ticket_activity:<id> entries never expire, one per ticket; the 300 default
        # would cull live keys once the ticket count passed it.
        "OPTIONS": {"MAX_ENTRIES": 10000},
    }
}

AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [
    BASE_DIR / "static",
    ("widget", BASE_DIR / "widget" / "dist"),
]
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/accounts/login/"

# Customer portal magic links. Links the customer requests expire fast; links we
# embed in a notification email have to still work when the mail is read the next
# morning, so those pass a longer TTL. Both are single-use either way.
MAGIC_LINK_TTL_MINUTES = config("MAGIC_LINK_TTL_MINUTES", default=30, cast=int)
MAGIC_LINK_EMAIL_TTL_MINUTES = config("MAGIC_LINK_EMAIL_TTL_MINUTES", default=60 * 24 * 7, cast=int)

# Email
EMAIL_BACKEND = config("EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend")
# Attach a display name so inboxes show "HayOps" rather than the bare
# ticket address. parseaddr/formataddr keep this idempotent — the deployed .env
# supplies a bare address, and one that already carries a name is left alone.
DEFAULT_FROM_NAME = config("DEFAULT_FROM_NAME", default="HayOps")
_from_name, _from_addr = parseaddr(
    config("DEFAULT_FROM_EMAIL", default="ticket@mail.example.com")
)
DEFAULT_FROM_EMAIL = formataddr((_from_name or DEFAULT_FROM_NAME, _from_addr))
DEFAULT_EMAIL_DOMAIN = config("DEFAULT_EMAIL_DOMAIN", default="mail.example.com")
# Shared address for new-ticket / customer-reply alerts to the support team (empty = disabled)
SUPPORT_NOTIFY_EMAIL = config("SUPPORT_NOTIFY_EMAIL", default="")

# Multi-domain
ADMIN_DOMAIN = config("ADMIN_DOMAIN", default="localhost")
SITE_URL = config("SITE_URL", default="http://localhost:8000")

# Umami analytics on portal KB pages (blank = off). Website ID is per SourceApplication.
UMAMI_HOST = config("UMAMI_HOST", default="")

# Storage
# Dev writes to the local filesystem; production.py swaps in R2 (see below).
# "public_media" holds world-readable branding assets, "default" holds everything
# else (ticket attachments) and must stay private.
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "public_media": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}

# Media storage: Cloudflare R2, two buckets (R2_* namespace)
#
# These are deliberately NOT assigned to the AWS_* settings names. django-ses
# falls back to settings.AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY, and
# django-storages falls back to the AWS_ACCESS_KEY_ID *env var* — so sharing
# the namespace makes SES try to authenticate with an R2 key (and vice versa).
# R2 config is passed explicitly through STORAGES OPTIONS instead, which takes
# precedence over both fallbacks. Keep it that way.
R2_ENDPOINT_URL = config("R2_ENDPOINT_URL", default="")
R2_ACCESS_KEY_ID = config("R2_ACCESS_KEY_ID", default="")
R2_SECRET_ACCESS_KEY = config("R2_SECRET_ACCESS_KEY", default="")
# Public bucket: branding logos, served straight off the bucket's public host.
R2_MEDIA_BUCKET_NAME = config("R2_MEDIA_BUCKET_NAME", default="")
# Public hostname for that bucket, no scheme, e.g. pub-xxxx.r2.dev
R2_PUBLIC_HOST = config("R2_PUBLIC_HOST", default="")
# Private bucket: ticket contents and attachments. Never made public.
R2_STORAGE_BUCKET_NAME = config("R2_STORAGE_BUCKET_NAME", default="")

_R2_COMMON_OPTIONS = {
    "endpoint_url": R2_ENDPOINT_URL,
    "access_key": R2_ACCESS_KEY_ID,
    "secret_key": R2_SECRET_ACCESS_KEY,
    "region_name": "auto",
    "default_acl": None,  # R2 has no ACLs
    "location": "media",
    "object_parameters": {"CacheControl": "max-age=86400"},
}

# Attachments: presigned, expiring URLs so bucket contents stay unreadable
# without a signature.
#
# ContentDisposition forces every attachment to download rather than render.
# Uploads are attacker-controlled (the portal submit form is unauthenticated), so
# an .html or .svg would otherwise be a live page served off the bucket origin.
# That origin is not the helpdesk's, so it could not touch a session cookie, but
# it is still a page an agent reaches by clicking a link on a real ticket. Doing
# it here rather than by extension blocklist covers the whole class at once --
# .html/.svg/.xhtml/.mhtml and whatever renders next -- and refusing a customer's
# file by extension loses bug reports.
R2_PRIVATE_STORAGE_OPTIONS = {
    **_R2_COMMON_OPTIONS,
    "bucket_name": R2_STORAGE_BUCKET_NAME,
    "querystring_auth": True,
    "querystring_expire": 3600,
    "object_parameters": {**_R2_COMMON_OPTIONS["object_parameters"], "ContentDisposition": "attachment"},
}

# Branding: stable URLs, because presigned ones expire within the hour and would
# dead-link logos in already-delivered email.
R2_PUBLIC_STORAGE_OPTIONS = {
    **_R2_COMMON_OPTIONS,
    "bucket_name": R2_MEDIA_BUCKET_NAME,
    "querystring_auth": False,
    "custom_domain": R2_PUBLIC_HOST,
}

# AWS (SES outbound + SES inbound S3) — AWS_* namespace.
# AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY are intentionally left unset here so
# django-ses and boto3 resolve the real AWS credentials from the environment.
SES_INBOUND_BUCKET = config("SES_INBOUND_BUCKET", default="")
SES_INBOUND_TOPIC_ARN = config("SES_INBOUND_TOPIC_ARN", default="")
SES_AWS_REGION = config("SES_AWS_REGION", default="us-east-1")

# Tag outbound mail with an SES configuration set so bounces, complaints and
# deliveries are recorded. Without one SES accepts a message and reports nothing
# afterwards, which is why a sign-in link that vanished between SES and the
# recipient's mailbox could not be traced on 2026-08-07. django-ses reads this
# setting name; empty means no configuration set, i.e. no event tracking.
# Outbound SES region for django-ses. Distinct from SES_AWS_REGION above, which
# is the inbound-mail S3 client's region.
AWS_SES_REGION_NAME = config("AWS_SES_REGION_NAME", default="us-east-1")
AWS_SES_CONFIGURATION_SET = config("AWS_SES_CONFIGURATION_SET", default="")

# DRF
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": [],
    "DEFAULT_THROTTLE_CLASSES": [
        "apps.core.ratelimit.ClientIPScopedRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "widget": "60/minute",
    },
}

# Header carrying the real client IP, as a request.META key, for rate limits.
# Empty = REMOTE_ADDR. Behind Cloudflare: HTTP_CF_CONNECTING_IP. Set it only if
# the app is unreachable except through that proxy -- see apps/core/ratelimit.py.
CLIENT_IP_HEADER = config("CLIENT_IP_HEADER", default="")

# CORS — dynamic allow via signal in widget_api
CORS_ALLOW_ALL_ORIGINS = False
from corsheaders.defaults import default_headers  # noqa: E402

CORS_ALLOW_HEADERS = (*default_headers, "x-app-key", "x-user-email", "x-user-name", "x-user-hash")

# Tailwind CLI
TAILWIND_CLI_SRC_CSS = "theme/src/styles.css"
TAILWIND_CLI_DIST_CSS = "theme/css/styles.css"

# Logging
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "skip_noisy_requests": {
            "()": "django.utils.log.CallbackFilter",
            "callback": lambda record: "/heartbeat/" not in record.getMessage()
            and "/__debug__/" not in record.getMessage(),
        },
    },
    "handlers": {
        "django.server": {
            "level": "INFO",
            "class": "logging.StreamHandler",
            "filters": ["skip_noisy_requests"],
        },
    },
    "loggers": {
        "django.server": {
            "handlers": ["django.server"],
            "level": "INFO",
            "propagate": False,
        },
    },
}

# Widget
HELPDESK_WIDGET_SLUG = config("HELPDESK_WIDGET_SLUG", default="helpdesk")
WIDGET_VERSION = json.loads((BASE_DIR / "widget" / "package.json").read_text())["version"]

# Procrastinate — no special on_app_ready hook needed
