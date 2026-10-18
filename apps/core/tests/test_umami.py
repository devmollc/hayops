"""Umami loads on portal KB pages only: never on ticket pages or the staff host."""

import pytest

from apps.kb.models import Article, Category

pytestmark = pytest.mark.django_db

TRACKER = "https://pulse.devmo.llc/p.js"


@pytest.fixture(autouse=True)
def _umami(settings, source_app):
    settings.ALLOWED_HOSTS = [*settings.ALLOWED_HOSTS, source_app.portal_domain, settings.ADMIN_DOMAIN]
    settings.UMAMI_HOST = "https://pulse.devmo.llc"
    source_app.umami_website_id = "43463cb8-0000-0000-0000-000000000000"
    source_app.save()


@pytest.fixture
def article(db):
    category = Category.objects.create(name="Billing", slug="billing")
    return Article.objects.create(title="Refunds", body="...", category=category, is_published=True)


def test_kb_pages_on_a_portal_host_load_the_tracker(client, source_app, article):
    for path in ("/kb/", f"/kb/{article.slug}/"):
        html = client.get(path, HTTP_HOST=source_app.portal_domain).content.decode()
        assert TRACKER in html, path
        assert 'data-website-id="43463cb8-0000-0000-0000-000000000000"' in html
        assert 'data-domains="support.appsfol.io"' in html


def test_ticket_submit_page_does_not_load_the_tracker(client, source_app):
    resp = client.get("/", HTTP_HOST=source_app.portal_domain)
    assert resp.status_code == 200
    assert TRACKER not in resp.content.decode()


def test_staff_host_kb_does_not_load_the_tracker(client, settings, article):
    resp = client.get("/kb/", HTTP_HOST=settings.ADMIN_DOMAIN)
    assert resp.status_code == 200
    assert "p.js" not in resp.content.decode()


def test_no_website_id_means_no_tracker(client, source_app, article):
    source_app.umami_website_id = ""
    source_app.save()
    assert "p.js" not in client.get("/kb/", HTTP_HOST=source_app.portal_domain).content.decode()
