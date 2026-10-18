from django.apps import AppConfig


class WidgetApiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.widget_api"
    label = "widget_api"

    def ready(self):
        import apps.widget_api.cors  # noqa: F401
