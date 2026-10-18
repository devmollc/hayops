from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.core"
    label = "core"
    # Admin sidebar group name; holds the per-product SourceApplication settings
    verbose_name = "Applications"
