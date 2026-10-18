from django.contrib import admin
from django.db.models import Count, Q
from django.utils.html import format_html

from .models import WebhookDelivery, WebhookEndpoint


class WebhookDeliveryInline(admin.TabularInline):
    model = WebhookDelivery
    extra = 0
    readonly_fields = ("event_type", "response_status", "attempts", "is_success", "delivered_at", "created_at")
    fields = ("event_type", "response_status", "attempts", "is_success", "delivered_at", "created_at")
    show_change_link = True
    can_delete = False
    max_num = 10
    ordering = ("-created_at",)

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(WebhookEndpoint)
class WebhookEndpointAdmin(admin.ModelAdmin):
    list_display = ("source_application", "url", "event_count", "delivery_stats", "is_active", "created_at")
    list_filter = ("is_active", "source_application")
    search_fields = ("url", "source_application__name")
    readonly_fields = ("secret", "created_at", "updated_at")
    date_hierarchy = "created_at"
    inlines = [WebhookDeliveryInline]

    fieldsets = (
        (None, {"fields": ("source_application", "url", "is_active")}),
        ("Events", {"fields": ("events",)}),
        ("Security", {"fields": ("secret",)}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )

    actions = ["activate", "deactivate"]

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            _total_deliveries=Count("deliveries"),
            _failed_deliveries=Count("deliveries", filter=Q(deliveries__is_success=False)),
        )

    @admin.display(description="Events")
    def event_count(self, obj):
        count = len(obj.events) if obj.events else 0
        return count

    @admin.display(description="Deliveries")
    def delivery_stats(self, obj):
        total = obj._total_deliveries
        failed = obj._failed_deliveries
        if total == 0:
            return "-"
        ok = total - failed
        return format_html("{ok} / {total}", ok=ok, total=total)

    @admin.action(description="Activate selected endpoints")
    def activate(self, request, queryset):
        count = queryset.update(is_active=True)
        self.message_user(request, f"Activated {count} endpoint(s).")

    @admin.action(description="Deactivate selected endpoints")
    def deactivate(self, request, queryset):
        count = queryset.update(is_active=False)
        self.message_user(request, f"Deactivated {count} endpoint(s).")


@admin.register(WebhookDelivery)
class WebhookDeliveryAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "endpoint",
        "event_type",
        "status_display",
        "attempts",
        "success_icon",
        "delivered_at",
        "created_at",
    )
    list_filter = ("is_success", "event_type")
    search_fields = ("endpoint__url", "event_type")
    readonly_fields = (
        "endpoint", "event_type", "payload", "response_status", "response_body",
        "attempts", "delivered_at", "is_success", "created_at", "updated_at",
    )
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.display(description="HTTP Status")
    def status_display(self, obj):
        if obj.response_status is None:
            return "-"
        if 200 <= obj.response_status < 300:
            color = "#10B981"
        elif 400 <= obj.response_status < 500:
            color = "#F59E0B"
        else:
            color = "#EF4444"
        return format_html(
            '<span style="color:{color};font-weight:600">{status}</span>',
            color=color,
            status=obj.response_status,
        )

    @admin.display(description="OK", boolean=True, ordering="is_success")
    def success_icon(self, obj):
        return obj.is_success
