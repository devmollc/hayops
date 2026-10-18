import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models, transaction
from django.utils import timezone


class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("The Email field must be set")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")

        return self.create_user(email, password, **extra_fields)


class User(AbstractUser):
    username = None
    email = models.EmailField("email address", unique=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    def __str__(self):
        return self.email


def _hash_token(raw_token):
    return hashlib.sha256(raw_token.encode()).hexdigest()


class MagicLoginToken(models.Model):
    """Single-use, short-lived credential that signs a customer into the portal.

    Server-side state rather than a JWT: these must be revocable and usable once.
    The Postgres cache table is UNLOGGED (see core/0006_cache_table) and is
    truncated on crash recovery, so it can't be the only record of an issued
    credential either.
    """

    token_hash = models.CharField(max_length=64, unique=True, db_index=True)
    email = models.EmailField(db_index=True)
    source_application = models.ForeignKey(
        "core.SourceApplication", on_delete=models.CASCADE, null=True, blank=True
    )
    next_url = models.CharField(max_length=255, blank=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    requested_ip = models.CharField(max_length=45, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"magic login for {self.email}"

    @classmethod
    def issue(cls, email, source_application=None, next_url="", requested_ip="", ttl_minutes=None):
        """Create a token and return (instance, raw_token). The raw token is never stored.

        Links the customer requests from the portal are short-lived. Links we embed
        in a notification email need to survive until the mail is actually read, so
        callers there pass a longer ttl_minutes; single-use consumption is what
        limits the exposure, not the clock.
        """
        raw_token = secrets.token_urlsafe(32)
        ttl = ttl_minutes or getattr(settings, "MAGIC_LINK_TTL_MINUTES", 30)
        token = cls.objects.create(
            token_hash=_hash_token(raw_token),
            email=email,
            source_application=source_application,
            next_url=next_url,
            expires_at=timezone.now() + timedelta(minutes=ttl),
            requested_ip=requested_ip,
        )
        return token, raw_token

    @classmethod
    def find_active(cls, raw_token):
        """Look up an unused, unexpired token without burning it."""
        return (
            cls.objects.filter(
                token_hash=_hash_token(raw_token),
                used_at__isnull=True,
                expires_at__gt=timezone.now(),
            )
            .select_related("source_application")
            .first()
        )

    @classmethod
    def consume(cls, raw_token):
        """Burn the token and return it, or None if unknown, expired or already used."""
        with transaction.atomic():
            token = (
                cls.objects.select_for_update()
                .filter(token_hash=_hash_token(raw_token), used_at__isnull=True)
                .first()
            )
            if token is None or token.expires_at <= timezone.now():
                return None
            token.used_at = timezone.now()
            token.save(update_fields=["used_at"])
            return token
