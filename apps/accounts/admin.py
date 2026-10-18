from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ("email", "full_name", "is_staff", "is_superuser", "is_active", "date_joined", "last_login")
    list_filter = ("is_staff", "is_superuser", "is_active", "groups")
    search_fields = ("email", "first_name", "last_name")
    ordering = ("email",)
    date_hierarchy = "date_joined"
    list_per_page = 30

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Personal info", {"fields": ("first_name", "last_name")}),
        (
            "Permissions",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                ),
            },
        ),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )

    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "first_name", "last_name", "password1", "password2", "is_staff", "is_superuser"),
            },
        ),
    )

    readonly_fields = ("last_login", "date_joined")

    actions = ["activate_users", "deactivate_users", "make_staff", "remove_staff"]

    @admin.display(description="Name")
    def full_name(self, obj):
        name = obj.get_full_name()
        return name if name else "-"

    @admin.action(description="Activate selected users")
    def activate_users(self, request, queryset):
        count = queryset.update(is_active=True)
        self.message_user(request, f"Activated {count} user(s).")

    @admin.action(description="Deactivate selected users")
    def deactivate_users(self, request, queryset):
        queryset.exclude(pk=request.user.pk).update(is_active=False)
        self.message_user(request, "Deactivated selected users (excluding yourself).")

    @admin.action(description="Grant staff access")
    def make_staff(self, request, queryset):
        count = queryset.update(is_staff=True)
        self.message_user(request, f"Granted staff access to {count} user(s).")

    @admin.action(description="Remove staff access")
    def remove_staff(self, request, queryset):
        queryset.exclude(pk=request.user.pk).update(is_staff=False)
        self.message_user(request, "Removed staff access from selected users (excluding yourself).")
