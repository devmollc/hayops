from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import User
from apps.core.models import SourceApplication
from apps.kb.models import Article, Category
from apps.tickets.models import Queue, Tag, Ticket
from apps.tickets.services import TicketService

ADMIN_EMAIL = "admin@helpdesk.local"
ADMIN_PASSWORD = "admin123"


class Command(BaseCommand):
    help = (
        "Seed example data for local development: an admin login, an example "
        "application (Acme) with its own portal, queues, tags, a KB article and a few "
        "tickets. Idempotent. Refuses to run unless DEBUG is on, because the admin "
        "password is published in this file."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--portal-domain",
            default="acme.localhost",
            help="Host serving Acme's customer portal (default: acme.localhost, which browsers resolve to 127.0.0.1)",
        )

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("seed_example is for development only; it needs DEBUG=True.")

        admin, _ = User.objects.get_or_create(email=ADMIN_EMAIL, defaults={"is_staff": True, "is_superuser": True})
        admin.set_password(ADMIN_PASSWORD)
        admin.save()

        portal_domain = options["portal_domain"]
        app, _ = SourceApplication.objects.get_or_create(
            slug="acme",
            defaults={
                "name": "Acme",
                "product_name": "Acme Widgets",
                "primary_color": "#7c3aed",
                "portal_domain": portal_domain,
                "allowed_domains": [portal_domain],
            },
        )

        support, _ = Queue.objects.get_or_create(slug="support", defaults={"name": "Support"})
        billing, _ = Queue.objects.get_or_create(slug="billing", defaults={"name": "Billing"})
        for name, color in [("Bug", "#EF4444"), ("Feature", "#3B82F6"), ("Question", "#8B5CF6")]:
            Tag.objects.get_or_create(slug=name.lower(), defaults={"name": name, "color": color})

        category, _ = Category.objects.get_or_create(slug="getting-started", defaults={"name": "Getting started"})
        Article.objects.get_or_create(
            slug="acme-welcome",
            defaults={
                "title": "Welcome to Acme support",
                "body": "Search this knowledge base, or [submit a ticket](/submit/) and we will get back to you.",
                "category": category,
                "author": admin,
                "is_published": True,
                "source_application": app,
            },
        )

        if not Ticket.objects.filter(source_application=app).exists():
            for subject, body, email, queue, priority in [
                (
                    "Cannot log in to dashboard",
                    "I get a 403 error when I log in.",
                    "alice@example.com",
                    support,
                    "high",
                ),
                (
                    "Billing question about plan upgrade",
                    "How do I upgrade my plan?",
                    "bob@example.com",
                    billing,
                    "normal",
                ),
                ("Feature request: dark mode", "Would love a dark mode option.", "carol@example.com", support, "low"),
            ]:
                TicketService.create_ticket(
                    subject=subject,
                    body=body,
                    submitter_email=email,
                    source_application=app,
                    queue=queue,
                    priority=priority,
                )

        self.stdout.write(self.style.SUCCESS("Example data ready."))
        self.stdout.write(f"  staff UI:   http://localhost:8000/  ({ADMIN_EMAIL} / {ADMIN_PASSWORD})")
        self.stdout.write(f"  portal:     http://{app.portal_domain}:8000/")
        self.stdout.write(f"  api_key:    {app.api_key}")
        self.stdout.write(f"  api_secret: {app.api_secret}")
