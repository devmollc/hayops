from django import forms

from .models import WebhookEndpoint, validate_public_destination

INPUT_CLASS = (
    "block w-full rounded-md border-gray-300 shadow-sm "
    "focus:border-blue-500 focus:ring-blue-500 sm:text-sm"
)


class WebhookEndpointForm(forms.ModelForm):
    class Meta:
        model = WebhookEndpoint
        fields = ["source_application", "url", "events", "secret", "is_active"]
        widgets = {
            "source_application": forms.Select(attrs={"class": INPUT_CLASS}),
            "url": forms.URLInput(attrs={"class": INPUT_CLASS}),
            # Write-only: the signing secret is never rendered back, so reading
            # the edit page is not a way to harvest it.
            "secret": forms.PasswordInput(attrs={"class": INPUT_CLASS}, render_value=False),
            "is_active": forms.CheckboxInput(
                attrs={"class": "rounded border-gray-300 text-blue-600 focus:ring-blue-500"}
            ),
        }
        help_texts = {
            "events": 'Enter as JSON list, e.g. ["created", "status_changed", "comment_added"]',
            "secret": "Shown only once. Leave blank when editing to keep the current secret.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Blank means "unchanged" on an existing endpoint, so it cannot be required there.
        if self.instance.pk:
            self.fields["secret"].required = False

    def clean_url(self):
        url = self.cleaned_data["url"]
        validate_public_destination(url)
        return url

    def clean_secret(self):
        return self.cleaned_data.get("secret") or self.instance.secret
