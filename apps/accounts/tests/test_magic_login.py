import pytest
from django.utils import timezone

from apps.accounts.models import MagicLoginToken, User

pytestmark = pytest.mark.django_db


def test_raw_token_is_never_stored():
    token, raw = MagicLoginToken.issue(email="customer@example.com")

    assert raw not in (token.token_hash, "")
    assert len(token.token_hash) == 64
    assert not MagicLoginToken.objects.filter(token_hash=raw).exists()


def test_token_can_only_be_consumed_once():
    _, raw = MagicLoginToken.issue(email="customer@example.com")

    assert MagicLoginToken.consume(raw) is not None
    assert MagicLoginToken.consume(raw) is None


def test_expired_token_is_rejected():
    token, raw = MagicLoginToken.issue(email="customer@example.com")
    token.expires_at = timezone.now() - timezone.timedelta(seconds=1)
    token.save(update_fields=["expires_at"])

    assert MagicLoginToken.consume(raw) is None
    assert MagicLoginToken.find_active(raw) is None


def test_unknown_token_is_rejected():
    assert MagicLoginToken.consume("not-a-real-token") is None


def test_find_active_does_not_burn_the_token():
    _, raw = MagicLoginToken.issue(email="customer@example.com")

    assert MagicLoginToken.find_active(raw) is not None
    assert MagicLoginToken.consume(raw) is not None


def test_email_ttl_override_outlives_the_default(settings):
    settings.MAGIC_LINK_TTL_MINUTES = 30
    short, _ = MagicLoginToken.issue(email="customer@example.com")
    long, _ = MagicLoginToken.issue(email="customer@example.com", ttl_minutes=60 * 24 * 7)

    assert long.expires_at > short.expires_at


def test_unusable_password_account_cannot_authenticate_with_a_password():
    """The magic-link flow must not create a back door into the password form."""
    from django.contrib.auth import authenticate

    user = User.objects.create(email="customer@example.com", is_staff=False)
    user.set_unusable_password()
    user.save()

    assert authenticate(username="customer@example.com", password="") is None
    assert authenticate(username="customer@example.com", password="x") is None
