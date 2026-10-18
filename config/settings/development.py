from .base import *  # noqa: F401, F403

DEBUG = True

INSTALLED_APPS += [  # noqa: F405
    "debug_toolbar",
]

MIDDLEWARE.insert(0, "debug_toolbar.middleware.DebugToolbarMiddleware")  # noqa: F405

INTERNAL_IPS = ["127.0.0.1"]

ALLOWED_HOSTS = ["localhost", "127.0.0.1", ".localhost"]

CSRF_TRUSTED_ORIGINS = ["https://helpdesk.localhost"]

# Trust X-Forwarded-Proto from the portless HTTPS proxy
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Use console email backend for development
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
