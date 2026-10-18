import pytest
from django.conf import settings
from django.urls import reverse

pytestmark = pytest.mark.django_db


def test_help_page_renders_for_staff(client, staff_user):
    client.force_login(staff_user)
    response = client.get("/help/")
    assert response.status_code == 200
    assert b"Support team guide" in response.content


def test_help_page_lists_every_published_alias_per_application(client, staff_user, source_app):
    from apps.core.views import INBOUND_ALIASES

    client.force_login(staff_user)
    body = client.get("/help/").content.decode()
    assert source_app.branding_name in body
    for alias in INBOUND_ALIASES:
        assert f"{alias}@{source_app.mail_domain}" in body


def test_help_page_links_the_portal_url(client, staff_user, source_app):
    client.force_login(staff_user)
    body = client.get("/help/").content.decode()
    assert f'href="https://{source_app.portal_domain}/"' in body


def test_help_page_explains_unassociated_tickets(client, staff_user, source_app):
    """Staff need to know an off-domain sender still gets a ticket, just an orphaned one."""
    client.force_login(staff_user)
    body = client.get("/help/").content.decode()
    assert "no application set" in body
    assert settings.DEFAULT_EMAIL_DOMAIN in body


def test_help_page_flags_an_application_with_no_mail_domain(client, staff_user, source_app):
    """An unroutable app is exactly what this table exists to surface."""
    source_app.mail_domain = ""
    source_app.save(update_fields=["mail_domain"])
    client.force_login(staff_user)
    assert "No mail domain set" in client.get("/help/").content.decode()


def test_help_page_explains_the_testbench_and_connecting_claude(client, staff_user):
    """The MCP command is built from the host being viewed, so test and prod each show their own URL."""
    client.force_login(staff_user)
    body = client.get("/help/").content.decode()
    assert 'id="testbench"' in body and 'id="mcp"' in body
    assert "claude mcp add --scope user --transport http helpdesk http://testserver/mcp" in body
    assert f'href="{reverse("testbench:tokens")}"' in body


def test_help_page_forbidden_for_non_staff(client, django_user_model):
    user = django_user_model.objects.create_user(email="c@example.com", password="x")
    client.force_login(user)
    assert client.get("/help/").status_code == 403
