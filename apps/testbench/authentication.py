from django.utils import timezone
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from apps.accounts.models import _hash_token

from .models import ApiToken


def bearer_token(header):
    """The raw token from an `Authorization: Bearer <token>` header value, or ""."""
    scheme, _, raw_token = (header or "").partition(" ")
    return raw_token.strip() if scheme.lower() == "bearer" else ""


def user_for_token(raw_token):
    """The active user who owns this API token, or None. Records the use."""
    token = ApiToken.objects.select_related("user").filter(token_hash=_hash_token(raw_token)).first()
    # A deactivated user's tokens die with the account.
    if token is None or not token.user.is_active:
        return None
    ApiToken.objects.filter(pk=token.pk).update(last_used_at=timezone.now())
    return token.user


class BearerTokenAuthentication(BaseAuthentication):
    """`Authorization: Bearer <token>` for a personal ApiToken; acts as the token's user."""

    def authenticate(self, request):
        raw_token = bearer_token(request.META.get("HTTP_AUTHORIZATION"))
        if not raw_token:
            return None
        user = user_for_token(raw_token)
        if user is None:
            raise AuthenticationFailed("Invalid token.")
        return user, None

    def authenticate_header(self, request):
        # Makes unauthenticated requests a 401 rather than a 403.
        return "Bearer"
