from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied


def can_view_ticket(user, ticket):
    """Staff can view any ticket. Non-staff only if they're a participant."""
    if user.is_staff:
        return True
    return ticket.participants.filter(user=user).exists()


def is_staff_user(user):
    return user.is_staff


def staff_view(view):
    """Signed-in staff only. A signed-in customer gets a 403 rather than a login loop."""

    @wraps(view)
    @login_required
    def wrapped(request, *args, **kwargs):
        if not is_staff_user(request.user):
            raise PermissionDenied
        return view(request, *args, **kwargs)

    return wrapped
