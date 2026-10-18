from django import forms

from apps.core.models import SourceApplication

from .models import CannedResponse, Comment, Queue, Ticket


class TicketFilterForm(forms.Form):
    status = forms.ChoiceField(choices=[("", "All Statuses")] + list(Ticket.Status.choices), required=False)
    priority = forms.ChoiceField(choices=[("", "All Priorities")] + list(Ticket.Priority.choices), required=False)
    queue = forms.ModelChoiceField(
        queryset=Queue.objects.filter(is_active=True), required=False, empty_label="All Queues"
    )
    source_application = forms.ModelChoiceField(
        queryset=SourceApplication.objects.filter(is_active=True),
        required=False,
        empty_label="All Applications",
    )
    assigned_to = forms.ChoiceField(
        choices=[("", "Anyone"), ("me", "Assigned to me"), ("unassigned", "Unassigned")],
        required=False,
    )
    q = forms.CharField(required=False, widget=forms.TextInput(attrs={"placeholder": "Search..."}))


class TicketCreateForm(forms.Form):
    subject = forms.CharField(max_length=300)
    body = forms.CharField(widget=forms.Textarea(attrs={"rows": 6}))
    submitter_email = forms.EmailField(required=False)
    queue = forms.ModelChoiceField(queryset=Queue.objects.filter(is_active=True), required=False)
    priority = forms.ChoiceField(choices=Ticket.Priority.choices, initial=Ticket.Priority.NORMAL)
    # Required: a ticket with no application is invisible on every branded portal --
    # see _portal_scope in apps.portal.auth_views for what that costs the customer.
    source_application = forms.ModelChoiceField(
        queryset=SourceApplication.objects.filter(is_active=True),
        empty_label="Select an application",
    )


class TicketEditForm(forms.Form):
    subject = forms.CharField(max_length=300)
    body = forms.CharField(widget=forms.Textarea(attrs={"rows": 6}))
    queue = forms.ModelChoiceField(queryset=Queue.objects.filter(is_active=True), required=False)
    source_application = forms.ModelChoiceField(
        queryset=SourceApplication.objects.filter(is_active=True),
        required=False,
        empty_label="No Application",
    )


class CannedResponseForm(forms.ModelForm):
    class Meta:
        model = CannedResponse
        fields = ["title", "body", "queue", "shortcut"]
        widgets = {
            "title": forms.TextInput(attrs={"class": "form-input"}),
            "body": forms.Textarea(attrs={"class": "form-input", "rows": 10}),
            "queue": forms.Select(attrs={"class": "form-input"}),
            "shortcut": forms.TextInput(attrs={"class": "form-input", "placeholder": "e.g. refund"}),
        }
        help_texts = {
            # canned_response_search scopes by the ticket's queue, so a queued response
            # is invisible on every other queue. Say so before it surprises someone.
            "queue": "Leave blank to offer this on every ticket.",
            "shortcut": "Optional short alias; searched alongside the title.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["queue"].queryset = Queue.objects.filter(is_active=True)
        self.fields["queue"].empty_label = "All queues"


class CommentForm(forms.Form):
    body = forms.CharField(widget=forms.Textarea(attrs={"rows": 4, "placeholder": "Write a reply..."}))
    comment_type = forms.ChoiceField(
        choices=Comment.CommentType.choices,
        initial=Comment.CommentType.PUBLIC,
        widget=forms.HiddenInput(),
    )
