from django.apps import AppConfig


class TicketsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.tickets"
    label = "tickets"

    def ready(self):
        import apps.tickets.ses_events  # noqa: F401
        import apps.tickets.signals  # noqa: F401
