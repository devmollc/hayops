import pytest
from procrastinate import testing
from procrastinate.contrib.django import app as procrastinate_app


@pytest.fixture(autouse=True)
def _test_settings(settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    # CACHES is left alone: the DB cache rolls back with each test's transaction,
    # unlike LocMemCache, which leaked entries between tests.


@pytest.fixture
def job_connector():
    """In-memory procrastinate connector; inspect deferred jobs via .jobs."""
    in_memory = testing.InMemoryConnector()
    with procrastinate_app.replace_connector(in_memory):
        yield in_memory


@pytest.fixture
def source_app(db):
    from apps.core.models import SourceApplication

    return SourceApplication.objects.create(
        name="Appsfolio",
        slug="appsfolio-test",
        product_name="Appsfolio",
        primary_color="#10b981",
        portal_domain="support.appsfol.io",
        mail_domain="mail.appsfol.io",
        allowed_domains=["appsfol.io", "*.appsfol.io"],
    )


@pytest.fixture
def staff_user(db):
    from apps.accounts.models import User

    return User.objects.create_user(email="agent@devmosolutions.com", password="x", is_staff=True)


@pytest.fixture
def ticket(db, source_app):
    from apps.tickets.models import Ticket, TicketParticipant

    t = Ticket.objects.create(
        subject="Widget is broken",
        body="It does not load.",
        source_application=source_app,
    )
    TicketParticipant.objects.create(ticket=t, email="customer@example.com", role=TicketParticipant.Role.SUBMITTER)
    return t
