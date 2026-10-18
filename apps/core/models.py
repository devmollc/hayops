import secrets

from django.core.files.storage import storages
from django.core.validators import RegexValidator
from django.db import models


def public_media_storage():
    """Branding assets live in the public bucket; everything else stays private.

    A callable keeps the resolved storage out of the migration graph.
    """
    return storages["public_media"]


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class BouncedAddress(TimestampedModel):
    """An address that permanently rejected our mail.

    Keyed on the address rather than the ticket: deliverability is a property of the
    mailbox, so one bounce flags that person everywhere they appear, including on
    tickets opened after the bounce.
    """

    email = models.EmailField(unique=True)
    status = models.CharField(max_length=20, blank=True)  # RFC 3463 code, e.g. 5.1.1
    diagnostic = models.TextField(blank=True)

    class Meta:
        verbose_name_plural = "bounced addresses"

    def __str__(self):
        return self.email


class SourceApplication(TimestampedModel):
    name = models.CharField(max_length=255)
    slug = models.SlugField(unique=True)
    api_key = models.CharField(max_length=64, unique=True, editable=False)
    api_secret = models.CharField(max_length=64, editable=False)
    allowed_domains = models.JSONField(default=list, help_text="List of allowed origin domains")
    portal_domain = models.CharField(
        max_length=255, blank=True, help_text="Customer portal host for this app, e.g. support.example.com"
    )
    mail_domain = models.CharField(
        max_length=255, blank=True, db_index=True, help_text="SES-verified email domain, e.g. mail.example.com"
    )
    is_active = models.BooleanField(default=True)
    github_repo = models.CharField(
        max_length=100,
        blank=True,
        validators=[RegexValidator(r"^[\w.-]+/[\w.-]+$", "Use owner/name, e.g. acme/widgets.")],
        help_text='GitHub "owner/name" that testbench bugs are filed against',
    )

    # Branding
    product_name = models.CharField(max_length=255, blank=True, help_text="Customer-facing name; falls back to name")
    primary_color = models.CharField(max_length=7, default="#2563eb")
    logo = models.ImageField(upload_to="branding/", blank=True, storage=public_media_storage)
    email_signature = models.TextField(blank=True)

    umami_website_id = models.CharField(
        max_length=36, blank=True, help_text="Umami website ID for this portal's KB pages; blank = no analytics"
    )

    @property
    def branding_name(self):
        return self.product_name or self.name

    def save(self, *args, **kwargs):
        if not self.api_key:
            self.api_key = secrets.token_hex(32)
        if not self.api_secret:
            self.api_secret = secrets.token_hex(32)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name
