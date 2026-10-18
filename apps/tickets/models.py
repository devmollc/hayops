from django.conf import settings
from django.contrib.postgres.search import SearchVectorField
from django.db import connection, models

from apps.core.models import TimestampedModel


class Queue(TimestampedModel):
    name = models.CharField(max_length=100)
    slug = models.SlugField(unique=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Tag(TimestampedModel):
    name = models.CharField(max_length=50, unique=True)
    slug = models.SlugField(unique=True)
    color = models.CharField(max_length=7, default="#6B7280", help_text="Hex color code")

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Ticket(TimestampedModel):
    class Status(models.TextChoices):
        NEW = "new", "New"
        OPEN = "open", "Open"
        PENDING = "pending", "Pending"
        RESOLVED = "resolved", "Resolved"
        CLOSED = "closed", "Closed"
        MERGED = "merged", "Merged"

    class Priority(models.TextChoices):
        LOW = "low", "Low"
        NORMAL = "normal", "Normal"
        HIGH = "high", "High"
        URGENT = "urgent", "Urgent"

    reference_number = models.CharField(max_length=20, unique=True, editable=False)
    subject = models.CharField(max_length=300)
    body = models.TextField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NEW, db_index=True)
    priority = models.CharField(max_length=20, choices=Priority.choices, default=Priority.NORMAL, db_index=True)

    queue = models.ForeignKey(Queue, on_delete=models.PROTECT, related_name="tickets", null=True, blank=True)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="assigned_tickets"
    )
    source_application = models.ForeignKey(
        "core.SourceApplication", on_delete=models.SET_NULL, null=True, blank=True, related_name="tickets"
    )
    merged_into = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="merged_from"
    )
    tags = models.ManyToManyField(Tag, blank=True, related_name="tickets")
    custom_fields = models.JSONField(default=dict, blank=True)

    # SLA fields
    first_response_due_at = models.DateTimeField(null=True, blank=True)
    resolution_due_at = models.DateTimeField(null=True, blank=True)
    first_responded_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    search_vector = SearchVectorField(null=True, blank=True)

    # Message-ID of the acknowledgement we sent for this ticket. Roots the mail
    # thread so replies carrying only In-Reply-To/References can still be matched.
    email_message_id = models.CharField(max_length=255, blank=True, default="", db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "priority"]),
            models.Index(fields=["assigned_to", "status"]),
            models.Index(fields=["reference_number"]),
        ]

    def __str__(self):
        return f"{self.reference_number}: {self.subject}"

    def save(self, *args, **kwargs):
        if not self.reference_number:
            self.reference_number = self._generate_reference()
        super().save(*args, **kwargs)

    @staticmethod
    def _generate_reference():
        with connection.cursor() as cursor:
            cursor.execute("SELECT nextval('ticket_ref_seq')")
            val = cursor.fetchone()[0]
        return f"HD-{val:04d}"

    @property
    def is_open(self):
        return self.status in (self.Status.NEW, self.Status.OPEN, self.Status.PENDING)

    @property
    def submitter(self):
        participant = self.participants.filter(role=TicketParticipant.Role.SUBMITTER).first()
        return participant

    @property
    def initial_attachments(self):
        """Files that arrived with the ticket itself, not with a later reply.

        Reply attachments render against their comment in the timeline; without this
        split the submission's own files either duplicate there or show nowhere.
        """
        return self.attachments.filter(comment__isnull=True)


class TicketParticipant(TimestampedModel):
    class Role(models.TextChoices):
        SUBMITTER = "submitter", "Submitter"
        COLLABORATOR = "collaborator", "Collaborator"
        CC = "cc", "CC"

    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="participants")
    email = models.EmailField()
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="ticket_participations"
    )
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.SUBMITTER)

    class Meta:
        unique_together = ("ticket", "email")

    def __str__(self):
        return f"{self.email} ({self.role}) on {self.ticket.reference_number}"


class Comment(TimestampedModel):
    class CommentType(models.TextChoices):
        PUBLIC = "public", "Public"
        INTERNAL = "internal", "Internal Note"

    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="comments")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="comments"
    )
    author_email = models.EmailField(blank=True)
    body = models.TextField()
    comment_type = models.CharField(max_length=20, choices=CommentType.choices, default=CommentType.PUBLIC)
    is_first_response = models.BooleanField(default=False)
    email_message_id = models.CharField(max_length=255, blank=True, default="", db_index=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"Comment on {self.ticket.reference_number} by {self.author_email or self.author}"


# Types we are willing to render in a browser tab instead of downloading.
# Attachment.content_type is whatever the uploading client declared and the
# portal submit form is unauthenticated, so this is an allowlist, not a
# blocklist: an .html file claiming to be image/png renders as a broken image,
# never as a page. text/html and image/svg+xml are excluded for exactly that
# reason -- both execute script in whichever origin serves them.
INLINE_CONTENT_TYPES = frozenset(
    {
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
        "application/pdf",
        "text/plain",
    }
)


def inline_view_url(file, content_type):
    """URL that opens `file` in a browser tab; empty for anything not renderable.

    Stored objects keep `ContentDisposition: attachment` (see
    R2_PRIVATE_STORAGE_OPTIONS). This overrides the disposition and the
    content type on the presigned URL instead, so nothing at rest changes,
    the download default still applies to every other link, and files
    uploaded before this existed become viewable too.
    """
    declared = (content_type or "").split(";")[0].strip().lower()
    if declared not in INLINE_CONTENT_TYPES or not file:
        return ""
    try:
        return file.storage.url(
            file.name,
            parameters={
                "ResponseContentDisposition": "inline",
                "ResponseContentType": declared,
            },
        )
    except TypeError:
        # FileSystemStorage (development) takes no parameters and already
        # serves inline, so the plain URL is the right answer there.
        return file.url


class Attachment(TimestampedModel):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="attachments")
    comment = models.ForeignKey(Comment, on_delete=models.SET_NULL, null=True, blank=True, related_name="attachments")
    file = models.FileField(upload_to="attachments/%Y/%m/")
    filename = models.CharField(max_length=255)
    content_type = models.CharField(max_length=100, blank=True)
    size_bytes = models.PositiveIntegerField(default=0)

    def __str__(self):
        return self.filename

    @property
    def viewable_content_type(self):
        """The declared type, but only when it is one we will render inline."""
        declared = self.content_type.split(";")[0].strip().lower()
        return declared if declared in INLINE_CONTENT_TYPES else ""

    @property
    def view_url(self):
        return inline_view_url(self.file, self.content_type)


class TicketEventQuerySet(models.QuerySet):
    def customer_visible(self):
        """Drop events that would leak an internal note's existence to a customer."""
        return self.exclude(
            event_type=TicketEvent.EventType.COMMENT_ADDED,
            new_value__comment_type=Comment.CommentType.INTERNAL,
        )


class TicketEvent(TimestampedModel):
    class EventType(models.TextChoices):
        CREATED = "created", "Created"
        STATUS_CHANGED = "status_changed", "Status Changed"
        ASSIGNED = "assigned", "Assigned"
        PRIORITY_CHANGED = "priority_changed", "Priority Changed"
        QUEUE_CHANGED = "queue_changed", "Queue Changed"
        TAG_ADDED = "tag_added", "Tag Added"
        TAG_REMOVED = "tag_removed", "Tag Removed"
        MERGED = "merged", "Merged"
        SLA_BREACHED = "sla_breached", "SLA Breached"
        COMMENT_ADDED = "comment_added", "Comment Added"
        EDITED = "edited", "Edited"

    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="events")
    event_type = models.CharField(max_length=30, choices=EventType.choices)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="ticket_events"
    )
    old_value = models.JSONField(null=True, blank=True)
    new_value = models.JSONField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    objects = TicketEventQuerySet.as_manager()

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.event_type} on {self.ticket.reference_number}"


class SLAPolicy(TimestampedModel):
    name = models.CharField(max_length=100)
    queue = models.ForeignKey(Queue, on_delete=models.CASCADE, null=True, blank=True, related_name="sla_policies")
    source_application = models.ForeignKey(
        "core.SourceApplication", on_delete=models.CASCADE, null=True, blank=True, related_name="sla_policies"
    )
    first_response_minutes = models.PositiveIntegerField(default=60)
    resolution_minutes = models.PositiveIntegerField(default=480)
    priority_overrides = models.JSONField(
        default=dict,
        blank=True,
        help_text='e.g. {"urgent": {"first_response_minutes": 15, "resolution_minutes": 60}}',
    )

    class Meta:
        verbose_name = "SLA Policy"
        verbose_name_plural = "SLA Policies"

    def __str__(self):
        return self.name


class CannedResponse(TimestampedModel):
    title = models.CharField(max_length=200)
    body = models.TextField()
    queue = models.ForeignKey(Queue, on_delete=models.CASCADE, null=True, blank=True, related_name="canned_responses")
    shortcut = models.CharField(max_length=50, blank=True)
    usage_count = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="canned_responses"
    )

    def __str__(self):
        return self.title


class SavedView(TimestampedModel):
    name = models.CharField(max_length=100)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True, related_name="saved_views"
    )
    is_shared = models.BooleanField(default=False)
    filters = models.JSONField(default=dict)

    def __str__(self):
        return self.name


class CustomFieldDefinition(TimestampedModel):
    class FieldType(models.TextChoices):
        TEXT = "text", "Text"
        NUMBER = "number", "Number"
        BOOLEAN = "boolean", "Boolean"
        SELECT = "select", "Select"
        URL = "url", "URL"

    source_application = models.ForeignKey(
        "core.SourceApplication", on_delete=models.CASCADE, related_name="custom_field_definitions"
    )
    name = models.CharField(max_length=100)
    slug = models.SlugField()
    field_type = models.CharField(max_length=20, choices=FieldType.choices)
    options = models.JSONField(default=list, blank=True, help_text="Options for select fields")
    is_required = models.BooleanField(default=False)

    class Meta:
        unique_together = ("source_application", "slug")

    def __str__(self):
        return f"{self.name} ({self.source_application})"


class CSATRating(TimestampedModel):
    ticket = models.OneToOneField(Ticket, on_delete=models.CASCADE, related_name="csat_rating")
    rating = models.PositiveSmallIntegerField(choices=[(i, str(i)) for i in range(1, 6)])
    comment = models.TextField(blank=True)
    rated_by_email = models.EmailField()

    def __str__(self):
        return f"CSAT {self.rating}/5 for {self.ticket.reference_number}"
