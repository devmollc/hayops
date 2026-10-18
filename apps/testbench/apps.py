from django.apps import AppConfig


class TestbenchConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.testbench"
    label = "testbench"

    def ready(self):
        import apps.testbench.signals  # noqa: F401
