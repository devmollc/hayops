"""Create a superuser, overriding the password if the user already exists.

Shadows Django's built-in createsuperuser command to add password-override
support for existing users, which is useful in automated deployment scripts.

Usage:
    python manage.py createsuperuser --email admin@example.com --password secret
    python manage.py createsuperuser  # Interactive mode
"""

import getpass

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Create a superuser, overriding the password if the user already exists"

    def add_arguments(self, parser):
        parser.add_argument(
            "--email",
            dest="email",
            help="Email address for the superuser",
        )
        parser.add_argument(
            "--password",
            dest="password",
            help="Password for the superuser",
        )
        parser.add_argument(
            "--no-input",
            "--noinput",
            action="store_true",
            dest="no_input",
            help="Do not prompt for input (requires --email and --password)",
        )

    def handle(self, *args, **options):
        User = get_user_model()  # noqa: N806 -- a model class, so PascalCase is correct here
        email = options.get("email")
        password = options.get("password")
        no_input = options.get("no_input")

        if not email:
            if no_input:
                raise CommandError("--email is required when using --no-input")
            email = input("Email address: ").strip()

        if not email:
            raise CommandError("Email address is required")

        existing_user = User.objects.filter(email=email).first()

        if not password:
            if no_input:
                raise CommandError("--password is required when using --no-input")
            password = getpass.getpass("Password: ")
            password_confirm = getpass.getpass("Password (again): ")
            if password != password_confirm:
                raise CommandError("Passwords do not match")

        if not password:
            raise CommandError("Password is required")

        if existing_user:
            existing_user.set_password(password)
            existing_user.is_staff = True
            existing_user.is_superuser = True
            existing_user.save()
            self.stdout.write(self.style.SUCCESS(f"Password updated for superuser: {email}"))
        else:
            User.objects.create_superuser(email=email, password=password)
            self.stdout.write(self.style.SUCCESS(f"Superuser created: {email}"))
