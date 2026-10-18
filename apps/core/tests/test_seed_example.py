import pytest
from django.core.management import CommandError, call_command

from apps.core.models import SourceApplication
from apps.kb.models import Article
from apps.tickets.models import Ticket


@pytest.mark.django_db
def test_seed_example_is_idempotent(settings, job_connector):
    settings.DEBUG = True
    call_command("seed_example")
    call_command("seed_example")
    app = SourceApplication.objects.get(slug="acme")
    assert app.portal_domain == "acme.localhost"
    assert Article.objects.filter(source_application=app, is_published=True).count() == 1
    assert Ticket.objects.filter(source_application=app).count() == 3


@pytest.mark.django_db
def test_seed_example_refuses_without_debug(settings):
    settings.DEBUG = False
    with pytest.raises(CommandError):
        call_command("seed_example")
    assert not SourceApplication.objects.filter(slug="acme").exists()
