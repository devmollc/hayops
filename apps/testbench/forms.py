import json

from django import forms

from .api import SuiteInput
from .models import Case, Run, Section

MAX_IMPORT_BYTES = 2 * 1024 * 1024
MAX_IMPORT_ERRORS = 10


def _child_path(path, key):
    """Append one error key to a path.

    DRF 3.18 reports a nested list serializer's errors as a dict keyed by the
    integer index; before that it was a list. Both land here, and an int key is
    an index either way.
    """
    if isinstance(key, int):
        return f"{path}[{key}]"
    return f"{path}.{key}" if path else str(key)


def _error_lines(errors, path=""):
    """Flatten DRF's nested errors into lines like "sections[2].cases[0].title: This field is required."."""
    if isinstance(errors, dict):
        return [line for key, value in errors.items() for line in _error_lines(value, _child_path(path, key))]
    if isinstance(errors, list) and all(isinstance(error, str) for error in errors):
        return [f"{path}: {error}" if path else str(error) for error in errors]
    if isinstance(errors, list):
        return [line for index, value in enumerate(errors) for line in _error_lines(value, _child_path(path, index))]
    return [f"{path}: {errors}"]


class SuiteImportForm(forms.Form):
    file = forms.FileField(
        label="Suite JSON",
        help_text='The same shape the API returns: {"sections": [{"code", "name", "note", "cases": '
        '[{"key", "title", "priority", "setup", "steps", "expected", "release_focus"}]}]}. '
        "Areas are matched by code and cases by key; see docs/TESTBENCH.md.",
    )
    prune = forms.BooleanField(
        required=False,
        label="Delete cases that are not in the file",
        help_text="Deleted cases keep their history.",
    )

    def clean_file(self):
        upload = self.cleaned_data["file"]
        if upload.size > MAX_IMPORT_BYTES:
            raise forms.ValidationError(f"The file is larger than {MAX_IMPORT_BYTES // (1024 * 1024)} MB.")
        try:
            data = json.load(upload)
        except ValueError:
            raise forms.ValidationError("That file is not valid JSON.") from None
        # Same validation as the API's PUT suite/, so a file that imports here also imports there.
        suite = SuiteInput(data=data)
        if not suite.is_valid():
            lines = _error_lines(suite.errors)
            extra = [f"…and {len(lines) - MAX_IMPORT_ERRORS} more."] if len(lines) > MAX_IMPORT_ERRORS else []
            raise forms.ValidationError(lines[:MAX_IMPORT_ERRORS] + extra)
        self.sections = suite.validated_data["sections"]
        return upload


class SectionForm(forms.ModelForm):
    class Meta:
        model = Section
        fields = ["code", "name", "note", "position"]
        widgets = {"note": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, application, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.source_application = application

    def clean_code(self):
        # The unique constraint includes source_application, which is not a form field,
        # so ModelForm skips it; check here instead of surfacing an IntegrityError.
        code = self.cleaned_data["code"]
        clash = Section.objects.filter(source_application=self.instance.source_application, code=code)
        if clash.exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("Another area already uses this code.")
        return code


class CaseForm(forms.ModelForm):
    steps_text = forms.CharField(
        label="Steps", required=False, widget=forms.Textarea(attrs={"rows": 5}), help_text="One step per line."
    )
    field_order = ["section", "key", "title", "priority", "release_focus", "setup", "steps_text", "expected"]

    class Meta:
        model = Case
        fields = ["section", "key", "title", "priority", "release_focus", "setup", "expected"]
        labels = {"section": "Area"}
        widgets = {"setup": forms.Textarea(attrs={"rows": 2}), "expected": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, application, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.source_application = application
        self.fields["section"].queryset = Section.objects.filter(source_application=application)
        self.initial.setdefault("steps_text", "\n".join(self.instance.steps))

    def clean_key(self):
        key = self.cleaned_data["key"]
        clash = Case.objects.filter(source_application=self.instance.source_application, key=key)
        if clash.exclude(pk=self.instance.pk).exists():
            # Deleted cases keep their key so their history stays attached to it.
            raise forms.ValidationError("Another case (possibly a deleted one) already uses this key.")
        return key

    def save(self, commit=True):
        self.instance.steps = [line.strip() for line in self.cleaned_data["steps_text"].splitlines() if line.strip()]
        return super().save(commit)


class RunForm(forms.ModelForm):
    class Meta:
        model = Run
        fields = ["name", "build", "notes"]
