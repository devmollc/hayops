import re

from django.contrib.auth.views import (
    LoginView,
    LogoutView,
    PasswordResetCompleteView,
    PasswordResetConfirmView,
    PasswordResetDoneView,
    PasswordResetView,
)
from django.urls import reverse, reverse_lazy

from .forms import EmailAuthenticationForm, StyledPasswordResetForm, StyledSetPasswordForm


class AccountLoginView(LoginView):
    template_name = "accounts/login.html"
    authentication_form = EmailAuthenticationForm

    def get_context_data(self, **kwargs):
        """Offer customers a way out that keeps the ticket they were heading for.

        Staff links (`/tickets/HD-0038/`) reach customers -- forwarded team
        notifications, shared bookmarks -- and @login_required lands them on this
        password form, which they have no password for. Carry the reference across
        to the portal instead of dumping them at a generic sign-in.
        """
        context = super().get_context_data(**kwargs)
        portal_login = reverse("portal:login")
        # Only a literal HD-reference is ever reflected back, so this cannot be
        # used to bounce someone to an arbitrary destination.
        match = re.fullmatch(r"/tickets/(HD-\d+)/", self.request.GET.get("next", ""))
        if match:
            portal_next = reverse("portal:ticket_detail", kwargs={"reference": match.group(1)})
            context["portal_login_url"] = f"{portal_login}?next={portal_next}"
        else:
            context["portal_login_url"] = portal_login
        return context


class AccountLogoutView(LogoutView):
    pass


class AccountPasswordResetView(PasswordResetView):
    template_name = "accounts/password_reset.html"
    form_class = StyledPasswordResetForm
    email_template_name = "accounts/password_reset_email.html"
    success_url = reverse_lazy("accounts:password_reset_done")


class AccountPasswordResetDoneView(PasswordResetDoneView):
    template_name = "accounts/password_reset_done.html"


class AccountPasswordResetConfirmView(PasswordResetConfirmView):
    template_name = "accounts/password_reset_confirm.html"
    form_class = StyledSetPasswordForm
    success_url = reverse_lazy("accounts:password_reset_complete")


class AccountPasswordResetCompleteView(PasswordResetCompleteView):
    template_name = "accounts/password_reset_complete.html"
