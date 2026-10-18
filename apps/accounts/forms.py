from django.contrib.auth.forms import AuthenticationForm, PasswordResetForm, SetPasswordForm


class EmailAuthenticationForm(AuthenticationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].label = "Email"
        self.fields["username"].widget.attrs.update(
            {
                "placeholder": "you@example.com",
                "autofocus": True,
                "class": "auth-input",
            }
        )
        self.fields["password"].widget.attrs.update(
            {
                "placeholder": "Enter your password",
                "class": "auth-input",
            }
        )


class StyledPasswordResetForm(PasswordResetForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["email"].widget.attrs.update(
            {
                "placeholder": "you@example.com",
                "autofocus": True,
                "class": "auth-input",
            }
        )


class StyledSetPasswordForm(SetPasswordForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["new_password1"].widget.attrs.update(
            {
                "placeholder": "New password",
                "autofocus": True,
                "class": "auth-input",
            }
        )
        self.fields["new_password2"].widget.attrs.update(
            {
                "placeholder": "Confirm new password",
                "class": "auth-input",
            }
        )
