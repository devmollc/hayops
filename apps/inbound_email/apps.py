from django.apps import AppConfig


class InboundEmailConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.inbound_email"
    label = "inbound_email"
