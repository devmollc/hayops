from django.contrib.auth import login as auth_login
from django.db import transaction

from .models import MagicLoginToken, User

# login() never checks credentials -- it just writes the session keys -- so an
# account with an unusable password is a perfectly valid session subject. We name
# the stock backend explicitly rather than registering a custom one: a backend
# that resolves a user from an email alone would also answer the password form on
# /accounts/login/, and log anyone in without a password.
LOGIN_BACKEND = "django.contrib.auth.backends.ModelBackend"


def link_participants(user):
    """Claim ticket participations recorded against this user's email.

    Portal, email and anonymous widget submissions all leave
    TicketParticipant.user NULL. can_view_ticket() and the portal ticket list
    filter on that FK, so without this a customer logs in to an empty list.
    """
    from apps.tickets.models import TicketParticipant

    return TicketParticipant.objects.filter(email__iexact=user.email, user__isnull=True).update(user=user)


@transaction.atomic
def consume_login_token(request, raw_token):
    """Burn a magic-link token and sign the customer in. Returns (user, token) or (None, None)."""
    token = MagicLoginToken.consume(raw_token)
    if token is None:
        return None, None

    user, created = User.objects.get_or_create(email=token.email, defaults={"is_staff": False})
    if created:
        user.set_unusable_password()
        user.save(update_fields=["password"])

    # login() does not consult ModelBackend.user_can_authenticate, so check here.
    if not user.is_active:
        return None, None

    link_participants(user)
    auth_login(request, user, backend=LOGIN_BACKEND)
    return user, token
