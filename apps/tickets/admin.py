from django.contrib import admin, messages
from django.db.models import Count
from django.utils.html import format_html

from .models import (
    Attachment,
    CannedResponse,
    Comment,
    CSATRating,
    CustomFieldDefinition,
    Queue,
    SavedView,
    SLAPolicy,
    Tag,
    Ticket,
    TicketEvent,
    TicketParticipant,
)
from .services import BULK_MAX, TicketService

# ── Inlines ──────────────────────────────────────────────


class TicketParticipantInline(admin.TabularInline):
    model = TicketParticipant
    extra = 0
    fields = ("email", "user", "role")


class CommentInline(admin.StackedInline):
    model = Comment
    extra = 0
    readonly_fields = ("created_at",)
    fields = ("author", "author_email", "comment_type", "body", "created_at")


class AttachmentInline(admin.TabularInline):
    model = Attachment
    extra = 0
    readonly_fields = ("filename", "content_type", "size_bytes", "created_at")
    fields = ("file", "filename", "content_type", "size_bytes", "created_at")


class TicketEventInline(admin.TabularInline):
    model = TicketEvent
    extra = 0
    readonly_fields = ("event_type", "actor", "old_value", "new_value", "metadata", "created_at")
    fields = ("event_type", "actor", "old_value", "new_value", "created_at")
    show_change_link = False
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


class SLAPolicyInline(admin.TabularInline):
    model = SLAPolicy
    extra = 0
    fields = ("name", "first_response_minutes", "resolution_minutes")


class CannedResponseInline(admin.TabularInline):
    model = CannedResponse
    extra = 0
    fields = ("title", "shortcut", "usage_count")
    readonly_fields = ("usage_count",)


# ── Queue ────────────────────────────────────────────────


@admin.register(Queue)
class QueueAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "ticket_count", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}
    inlines = [SLAPolicyInline, CannedResponseInline]

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_ticket_count=Count("tickets"))

    @admin.display(description="Tickets", ordering="_ticket_count")
    def ticket_count(self, obj):
        return obj._ticket_count


# ── Tag ──────────────────────────────────────────────────


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "color_preview", "ticket_count")
    search_fields = ("name",)
    prepopulated_fields = {"slug": ("name",)}

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_ticket_count=Count("tickets"))

    @admin.display(description="Color")
    def color_preview(self, obj):
        return format_html(
            '<span style="display:inline-block;width:14px;height:14px;'
            'border-radius:3px;background:{color};vertical-align:middle"></span> {color}',
            color=obj.color,
        )

    @admin.display(description="Tickets", ordering="_ticket_count")
    def ticket_count(self, obj):
        return obj._ticket_count


# ── Ticket ───────────────────────────────────────────────


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = (
        "reference_number",
        "subject",
        "status_badge",
        "priority_badge",
        "queue",
        "assigned_to",
        "source_application",
        "created_at",
    )
    list_filter = ("status", "priority", "queue", "source_application", "assigned_to")
    search_fields = ("reference_number", "subject", "body")
    readonly_fields = ("reference_number", "search_vector", "created_at", "updated_at")
    date_hierarchy = "created_at"
    list_per_page = 30
    raw_id_fields = ("assigned_to", "merged_into")
    filter_horizontal = ("tags",)
    inlines = [TicketParticipantInline, CommentInline, AttachmentInline, TicketEventInline]

    fieldsets = (
        (None, {"fields": ("reference_number", "subject", "body")}),
        ("Classification", {"fields": ("status", "priority", "queue", "tags")}),
        ("Assignment", {"fields": ("assigned_to", "source_application")}),
        ("Merge", {
            "fields": ("merged_into",),
            "classes": ("collapse",),
        }),
        ("SLA Tracking", {
            "fields": (
                "first_response_due_at",
                "resolution_due_at",
                "first_responded_at",
                "resolved_at",
            ),
        }),
        ("Custom Fields", {
            "fields": ("custom_fields",),
            "classes": ("collapse",),
        }),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )

    actions = [
        "mark_open",
        "mark_pending",
        "mark_resolved",
        "mark_closed",
        "set_priority_urgent",
        "set_priority_high",
        "set_priority_normal",
        "set_priority_low",
    ]

    STATUS_COLORS = {
        "new": "#3B82F6",
        "open": "#F59E0B",
        "pending": "#8B5CF6",
        "resolved": "#10B981",
        "closed": "#6B7280",
        "merged": "#9CA3AF",
    }

    PRIORITY_COLORS = {
        "low": "#6B7280",
        "normal": "#3B82F6",
        "high": "#F59E0B",
        "urgent": "#EF4444",
    }

    @admin.display(description="Status", ordering="status")
    def status_badge(self, obj):
        color = self.STATUS_COLORS.get(obj.status, "#6B7280")
        return format_html(
            '<span style="padding:2px 8px;border-radius:9999px;font-size:11px;'
            'font-weight:600;color:#fff;background:{color}">{label}</span>',
            color=color,
            label=obj.get_status_display(),
        )

    @admin.display(description="Priority", ordering="priority")
    def priority_badge(self, obj):
        color = self.PRIORITY_COLORS.get(obj.priority, "#6B7280")
        return format_html(
            '<span style="padding:2px 8px;border-radius:9999px;font-size:11px;'
            'font-weight:600;color:#fff;background:{color}">{label}</span>',
            color=color,
            label=obj.get_priority_display(),
        )

    def _bulk_apply(self, request, queryset, *, status=None, priority=None):
        """Run an admin action through TicketService rather than queryset.update().

        update() is one fast query that skips everything that makes the change real:
        resolved_at (which the resolution and SLA reports read), the TicketEvent audit
        row, and the webhook that fires from it. Going through the service costs a few
        queries per ticket, hence the BULK_MAX guard.

        Customers are not emailed. That matches what these actions did before, and the
        staff queue at /tickets/ is the place to bulk-change status *with* notification.
        """
        total = queryset.count()
        if total > BULK_MAX:
            self.message_user(
                request,
                f"Select at most {BULK_MAX} tickets for this action ({total} selected).",
                level=messages.ERROR,
            )
            return

        changed = TicketService.bulk_update(
            list(queryset), actor=request.user, status=status, priority=priority
        )
        if changed:
            self.message_user(
                request, f"Updated {changed} of {total} ticket(s). Customers were not notified."
            )
        else:
            self.message_user(request, "Selected tickets already had those values.")

    def delete_queryset(self, request, queryset):
        # The cascade drops attachment rows but leaves their files in the bucket, and
        # SET_NULL leaves merge tombstones pointing at nothing. The service fixes both.
        TicketService.delete_tickets(queryset, actor=request.user)

    def delete_model(self, request, obj):
        TicketService.delete_tickets([obj], actor=request.user)

    @admin.action(description="Set status: Open")
    def mark_open(self, request, queryset):
        self._bulk_apply(request, queryset, status=Ticket.Status.OPEN)

    @admin.action(description="Set status: Pending")
    def mark_pending(self, request, queryset):
        self._bulk_apply(request, queryset, status=Ticket.Status.PENDING)

    @admin.action(description="Set status: Resolved")
    def mark_resolved(self, request, queryset):
        self._bulk_apply(request, queryset, status=Ticket.Status.RESOLVED)

    @admin.action(description="Set status: Closed")
    def mark_closed(self, request, queryset):
        self._bulk_apply(request, queryset, status=Ticket.Status.CLOSED)

    @admin.action(description="Set priority: Urgent")
    def set_priority_urgent(self, request, queryset):
        self._bulk_apply(request, queryset, priority=Ticket.Priority.URGENT)

    @admin.action(description="Set priority: High")
    def set_priority_high(self, request, queryset):
        self._bulk_apply(request, queryset, priority=Ticket.Priority.HIGH)

    @admin.action(description="Set priority: Normal")
    def set_priority_normal(self, request, queryset):
        self._bulk_apply(request, queryset, priority=Ticket.Priority.NORMAL)

    @admin.action(description="Set priority: Low")
    def set_priority_low(self, request, queryset):
        self._bulk_apply(request, queryset, priority=Ticket.Priority.LOW)


# ── Comment ──────────────────────────────────────────────


@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display = ("ticket", "author_display", "comment_type", "is_first_response", "created_at")
    list_filter = ("comment_type", "is_first_response")
    search_fields = ("ticket__reference_number", "body", "author_email")
    raw_id_fields = ("ticket", "author")
    date_hierarchy = "created_at"
    readonly_fields = ("created_at", "updated_at")

    @admin.display(description="Author")
    def author_display(self, obj):
        return obj.author or obj.author_email or "-"


# ── Attachment ───────────────────────────────────────────


@admin.register(Attachment)
class AttachmentAdmin(admin.ModelAdmin):
    list_display = ("filename", "ticket", "content_type", "file_size", "created_at")
    list_filter = ("content_type",)
    search_fields = ("filename", "ticket__reference_number")
    raw_id_fields = ("ticket", "comment")
    date_hierarchy = "created_at"
    readonly_fields = ("created_at", "updated_at")

    @admin.display(description="Size")
    def file_size(self, obj):
        if obj.size_bytes < 1024:
            return f"{obj.size_bytes} B"
        elif obj.size_bytes < 1024 * 1024:
            return f"{obj.size_bytes / 1024:.1f} KB"
        return f"{obj.size_bytes / (1024 * 1024):.1f} MB"


# ── Ticket Event ─────────────────────────────────────────


@admin.register(TicketEvent)
class TicketEventAdmin(admin.ModelAdmin):
    list_display = ("ticket", "event_type", "actor", "created_at")
    list_filter = ("event_type",)
    search_fields = ("ticket__reference_number",)
    raw_id_fields = ("ticket", "actor")
    date_hierarchy = "created_at"
    readonly_fields = (
        "ticket", "event_type", "actor", "old_value", "new_value", "metadata", "created_at", "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


# ── SLA Policy ───────────────────────────────────────────


@admin.register(SLAPolicy)
class SLAPolicyAdmin(admin.ModelAdmin):
    list_display = ("name", "queue", "source_application", "first_response_display", "resolution_display")
    list_filter = ("queue", "source_application")
    search_fields = ("name",)

    @admin.display(description="First Response")
    def first_response_display(self, obj):
        hours, mins = divmod(obj.first_response_minutes, 60)
        if hours:
            return f"{hours}h {mins}m"
        return f"{mins}m"

    @admin.display(description="Resolution")
    def resolution_display(self, obj):
        hours, mins = divmod(obj.resolution_minutes, 60)
        if hours:
            return f"{hours}h {mins}m"
        return f"{mins}m"


# ── Canned Response ──────────────────────────────────────


@admin.register(CannedResponse)
class CannedResponseAdmin(admin.ModelAdmin):
    list_display = ("title", "queue", "shortcut", "usage_count", "created_by", "created_at")
    list_filter = ("queue",)
    search_fields = ("title", "shortcut", "body")
    raw_id_fields = ("created_by",)
    readonly_fields = ("usage_count", "created_at", "updated_at")


# ── Saved View ───────────────────────────────────────────


@admin.register(SavedView)
class SavedViewAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "is_shared", "created_at")
    list_filter = ("is_shared",)
    search_fields = ("name",)
    raw_id_fields = ("user",)
    readonly_fields = ("created_at", "updated_at")


# ── Custom Field Definition ──────────────────────────────


@admin.register(CustomFieldDefinition)
class CustomFieldDefinitionAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "source_application", "field_type", "is_required")
    list_filter = ("field_type", "is_required", "source_application")
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}


# ── CSAT Rating ──────────────────────────────────────────


@admin.register(CSATRating)
class CSATRatingAdmin(admin.ModelAdmin):
    list_display = ("ticket", "star_rating", "rated_by_email", "created_at")
    list_filter = ("rating",)
    search_fields = ("ticket__reference_number", "rated_by_email", "comment")
    raw_id_fields = ("ticket",)
    date_hierarchy = "created_at"
    readonly_fields = ("created_at", "updated_at")

    @admin.display(description="Rating", ordering="rating")
    def star_rating(self, obj):
        return format_html(
            '<span style="color:#F59E0B;letter-spacing:2px">{stars}</span>'
            '<span style="color:#D1D5DB;letter-spacing:2px">{empty}</span>',
            stars="\u2605" * obj.rating,
            empty="\u2605" * (5 - obj.rating),
        )
