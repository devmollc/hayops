"""Shared manual QA: per-application test cases, the runs they are tested in, and results."""

import secrets

from django.conf import settings
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import Q

from apps.accounts.models import _hash_token
from apps.core.models import SourceApplication, TimestampedModel

# Keys and area codes appear in URLs and bug reports ("ISO-03"), so keep them URL-safe.
key_validator = RegexValidator(r"^[A-Za-z0-9._-]+$", "Use letters, digits, dots, dashes or underscores.")


class Section(TimestampedModel):
    """An area of the script (ISO, SUB, ...). Its code prefixes the keys of its cases."""

    source_application = models.ForeignKey(SourceApplication, on_delete=models.CASCADE, related_name="test_sections")
    code = models.CharField(max_length=12, validators=[key_validator], help_text="Short code, e.g. ISO")
    name = models.CharField(max_length=255)
    note = models.TextField(blank=True, help_text="Shown under the area heading")
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["position", "code"]
        constraints = [models.UniqueConstraint(fields=["source_application", "code"], name="testbench_section_code")]

    def __str__(self):
        return f"{self.code} {self.name}"


class Case(TimestampedModel):
    class Priority(models.TextChoices):
        P0 = "P0", "P0 — must work"
        P1 = "P1", "P1 — important"
        P2 = "P2", "P2 — lower risk"

    source_application = models.ForeignKey(SourceApplication, on_delete=models.CASCADE, related_name="test_cases")
    section = models.ForeignKey(Section, on_delete=models.PROTECT, related_name="cases")
    key = models.CharField(max_length=20, validators=[key_validator], help_text="Quoted in bug reports, e.g. ISO-03")
    title = models.CharField(max_length=255)
    priority = models.CharField(max_length=2, choices=Priority.choices, default=Priority.P1)
    setup = models.TextField(blank=True)
    steps = models.JSONField(default=list, blank=True)
    expected = models.TextField(blank=True)
    release_focus = models.BooleanField(default=False, help_text="Covers something this release changed")
    position = models.PositiveIntegerField(default=0)
    # Deleting a case clears this rather than removing the row, so its results stay.
    # ponytail: results show the case's current wording, not the wording as tested.
    # Snapshot title/steps onto Result if audits ever need the latter.
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["position", "key"]
        constraints = [models.UniqueConstraint(fields=["source_application", "key"], name="testbench_case_key")]

    def __str__(self):
        return f"{self.key} {self.title}"


class Run(TimestampedModel):
    """One pass through the script, e.g. "v1.4.0 regression". The board shows the open one."""

    source_application = models.ForeignKey(SourceApplication, on_delete=models.CASCADE, related_name="test_runs")
    name = models.CharField(max_length=255)
    build = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)
    started_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["source_application"], condition=Q(closed_at__isnull=True), name="testbench_one_open_run"
            )
        ]

    def __str__(self):
        return self.name


class Result(TimestampedModel):
    """A tester's go at a case within a run.

    Claiming creates an in-progress row; recording an outcome completes it. A retest
    adds a new row rather than rewriting the old one, which is the case's history.
    """

    class Status(models.TextChoices):
        IN_PROGRESS = "in_progress", "In progress"
        PASS = "pass", "Pass"
        FAIL = "fail", "Fail"
        BLOCKED = "blocked", "Blocked"

    run = models.ForeignKey(Run, on_delete=models.CASCADE, related_name="results")
    case = models.ForeignKey(Case, on_delete=models.PROTECT, related_name="results")
    tester = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="test_results"
    )
    status = models.CharField(max_length=20, choices=Status.choices)
    notes = models.TextField(blank=True)
    github_issue_url = models.URLField(blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            # The database, not the view, decides who wins when two testers claim at once.
            models.UniqueConstraint(
                fields=["run", "case"], condition=Q(status="in_progress"), name="testbench_one_claim"
            )
        ]

    def __str__(self):
        return f"{self.case_id} {self.status}"

    @property
    def github_issue_number(self):
        tail = self.github_issue_url.rstrip("/").rsplit("/", 1)[-1]
        return tail if tail.isdigit() else ""


class ApiToken(models.Model):
    """Personal bearer token for the testbench API. Only the hash is stored.

    Lives here because the testbench API is its only consumer; move it to accounts
    when a second API wants it.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="api_tokens")
    name = models.CharField(max_length=100)
    token_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.user})"

    @classmethod
    def issue(cls, user, name):
        """Create a token and return (instance, raw_token). The raw token is never stored."""
        raw_token = secrets.token_urlsafe(32)
        return cls.objects.create(user=user, name=name, token_hash=_hash_token(raw_token)), raw_token
