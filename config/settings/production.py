from decouple import config

from .base import *  # noqa: F401, F403

DEBUG = False

SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# ALLOWED_HOSTS comes from the env via base.py — must list the admin domain
# and every SourceApplication portal_domain (e.g. support.example.com).

CSRF_TRUSTED_ORIGINS = config(
    "CSRF_TRUSTED_ORIGINS", default="",
    cast=lambda v: [s.strip() for s in v.split(",") if s.strip()],
)

STORAGES = {
    "default": {
        "BACKEND": "storages.backends.s3boto3.S3Boto3Storage",
        "OPTIONS": R2_PRIVATE_STORAGE_OPTIONS,  # noqa: F405
    },
    "public_media": {
        "BACKEND": "storages.backends.s3boto3.S3Boto3Storage",
        "OPTIONS": R2_PUBLIC_STORAGE_OPTIONS,  # noqa: F405
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

EMAIL_BACKEND = "django_ses.SESBackend"
