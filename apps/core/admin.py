import secrets

from django.contrib import admin
from django.utils.html import format_html

from .models import BouncedAddress, SourceApplication


@admin.register(SourceApplication)
class SourceApplicationAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "slug",
        "portal_domain",
        "umami_website_id",
        "inbound_address",
        "truncated_api_key",
        "domain_count",
        "is_active",
        "created_at",
    )
    list_filter = ("is_active",)
    search_fields = ("name", "slug")
    readonly_fields = ("api_key", "api_secret", "created_at", "updated_at")
    prepopulated_fields = {"slug": ("name",)}
    date_hierarchy = "created_at"
    list_per_page = 30

    fieldsets = (
        (None, {"fields": ("name", "slug", "is_active")}),
        (
            "Domains",
            {
                "fields": ("portal_domain", "mail_domain", "allowed_domains"),
                "description": "portal_domain: customer portal host (e.g. support.example.com). "
                "mail_domain: SES-verified email domain (e.g. mail.example.com). "
                "allowed_domains: origins allowed to embed the widget.",
            },
        ),
        ("Branding", {"fields": ("product_name", "primary_color", "logo", "email_signature")}),
        ("Testbench", {"fields": ("github_repo",)}),
        ("Analytics", {"fields": ("umami_website_id",)}),
        (
            "API Credentials",
            {
                "fields": ("api_key", "api_secret"),
                "description": "Auto-generated on creation. Use the action below to regenerate.",
            },
        ),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )

    actions = ["regenerate_api_credentials", "activate", "deactivate"]

    @admin.display(description="API Key")
    def truncated_api_key(self, obj):
        return format_html("<code>{}&hellip;</code>", obj.api_key[:12])

    @admin.display(description="Domains")
    def domain_count(self, obj):
        count = len(obj.allowed_domains) if obj.allowed_domains else 0
        return count

    @admin.display(description="Inbound address")
    def inbound_address(self, obj):
        """Any local part routes to the app, so support@ is just the one to hand out."""
        if not obj.mail_domain:
            return "—"
        return f"support@{obj.mail_domain}"

    @admin.action(description="Regenerate API credentials")
    def regenerate_api_credentials(self, request, queryset):
        for app in queryset:
            app.api_key = secrets.token_hex(32)
            app.api_secret = secrets.token_hex(32)
            app.save(update_fields=["api_key", "api_secret", "updated_at"])
        self.message_user(request, f"Regenerated credentials for {queryset.count()} application(s).")

    @admin.action(description="Activate selected applications")
    def activate(self, request, queryset):
        count = queryset.update(is_active=True)
        self.message_user(request, f"Activated {count} application(s).")

    @admin.action(description="Deactivate selected applications")
    def deactivate(self, request, queryset):
        count = queryset.update(is_active=False)
        self.message_user(request, f"Deactivated {count} application(s).")


@admin.register(BouncedAddress)
class BouncedAddressAdmin(admin.ModelAdmin):
    """Deleting a row clears the flag -- the way to un-flag a mailbox that has been fixed."""

    list_display = ("email", "status", "updated_at")
    search_fields = ("email",)
    readonly_fields = ("created_at", "updated_at")
