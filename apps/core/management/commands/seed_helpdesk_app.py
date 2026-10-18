from django.core.management.base import BaseCommand

from apps.core.models import SourceApplication


class Command(BaseCommand):
    help = "Seed the HayOps SourceApplication used by the built-in widget"

    def add_arguments(self, parser):
        parser.add_argument(
            "--domain",
            default="localhost:8000",
            help="portal_domain / allowed origin for the app (default: localhost:8000)",
        )
        parser.add_argument(
            "--name",
            default="HayOps",
            help="Display name for the application (default: HayOps)",
        )
        parser.add_argument(
            "--slug",
            default="helpdesk",
            help="Slug for the application (default: helpdesk)",
        )

    def handle(self, *args, **options):
        slug = options["slug"]
        name = options["name"]
        domain = options["domain"]

        app, created = SourceApplication.objects.get_or_create(
            slug=slug,
            defaults={
                "name": name,
                "allowed_domains": [domain],
                "portal_domain": domain,
                "is_active": True,
            },
        )

        if created:
            self.stdout.write(self.style.SUCCESS(f"Created SourceApplication '{app.name}'"))
        else:
            self.stdout.write(f"SourceApplication '{app.name}' already exists")

        self.stdout.write(f"  slug:       {app.slug}")
        self.stdout.write(f"  api_key:    {app.api_key}")
        self.stdout.write(f"  api_secret: {app.api_secret}")
        self.stdout.write(f"  domains:    {app.allowed_domains}")
