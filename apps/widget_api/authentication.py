import hashlib
import hmac

from django.contrib.auth.models import AnonymousUser
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from apps.accounts.models import User
from apps.core.models import SourceApplication


class HMACAuthentication(BaseAuthentication):
    def authenticate(self, request):
        api_key = request.META.get("HTTP_X_APP_KEY")
        user_email = request.META.get("HTTP_X_USER_EMAIL")
        user_hash = request.META.get("HTTP_X_USER_HASH")

        if not api_key or not user_email or not user_hash:
            return None

        try:
            source_app = SourceApplication.objects.get(api_key=api_key, is_active=True)
        except SourceApplication.DoesNotExist:
            raise AuthenticationFailed("Invalid API key.")

        expected_hash = hmac.new(
            source_app.api_secret.encode("utf-8"),
            user_email.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(user_hash, expected_hash):
            raise AuthenticationFailed("Invalid user hash.")

        user, _ = User.objects.get_or_create(
            email=user_email,
            defaults={"is_staff": False},
        )

        return (user, source_app)


class AppKeyAuthentication(BaseAuthentication):
    """Authenticates requests using only the app key (no user identity).

    Activates when X-App-Key is present but X-User-Email/X-User-Hash are absent.
    Returns (AnonymousUser, source_app) so the view can handle anonymous submissions.
    """

    def authenticate(self, request):
        api_key = request.META.get("HTTP_X_APP_KEY")
        user_email = request.META.get("HTTP_X_USER_EMAIL")
        user_hash = request.META.get("HTTP_X_USER_HASH")

        # Only activate when app key is present but user credentials are not
        if not api_key or (user_email and user_hash):
            return None

        try:
            source_app = SourceApplication.objects.get(api_key=api_key, is_active=True)
        except SourceApplication.DoesNotExist:
            raise AuthenticationFailed("Invalid API key.")

        return (AnonymousUser(), source_app)
