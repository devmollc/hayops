from django import forms


class MagicLoginForm(forms.Form):
    email = forms.EmailField()
    # Honeypot: real users never see or fill this field (hidden via CSS in the template).
    website = forms.CharField(required=False, widget=forms.HiddenInput)


class PortalReplyForm(forms.Form):
    body = forms.CharField(widget=forms.Textarea, max_length=10000)


class TicketSubmitForm(forms.Form):
    name = forms.CharField(max_length=200, required=False)
    email = forms.EmailField()
    subject = forms.CharField(max_length=300)
    body = forms.CharField(widget=forms.Textarea)
    # Honeypot: real users never see or fill this field (hidden via CSS in the template).
    website = forms.CharField(required=False, widget=forms.HiddenInput)
