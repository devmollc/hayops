"""Article slugs generate themselves, which is what the form placeholder promises."""

import pytest
from django.urls import reverse

from apps.kb.models import Article, Category

pytestmark = pytest.mark.django_db


@pytest.fixture
def category(db):
    return Category.objects.create(name="Billing", slug="billing")


def make(category, title, **kwargs):
    return Article.objects.create(title=title, body="...", category=category, **kwargs)


def test_slug_is_generated_from_the_title(category):
    assert make(category, "How do I reset my password?").slug == "how-do-i-reset-my-password"


def test_an_explicit_slug_is_kept(category):
    assert make(category, "Refund policy", slug="refunds").slug == "refunds"


def test_editing_the_title_does_not_move_the_article_url(category):
    article = make(category, "Original title")
    article.title = "Something else entirely"
    article.save()

    assert article.slug == "original-title"


def test_duplicate_titles_get_distinct_slugs(category):
    slugs = [make(category, "Same title").slug for _ in range(3)]

    assert slugs == ["same-title", "same-title-2", "same-title-3"]
    assert len(set(slugs)) == 3


# Longest title the column allows, and far longer than the 50-char slug field.
LONG_TITLE = ("word " * 39).strip()


def test_long_title_is_truncated_to_fit_the_column(category):
    max_length = Article._meta.get_field("slug").max_length

    article = make(category, LONG_TITLE)

    assert len(article.slug) <= max_length
    assert not article.slug.endswith("-")


def test_colliding_long_titles_stay_within_the_column(category):
    first, second = make(category, LONG_TITLE), make(category, LONG_TITLE)

    assert len(second.slug) <= Article._meta.get_field("slug").max_length
    assert second.slug != first.slug


def test_a_title_that_slugifies_to_nothing_still_saves(category):
    """SlugField rejects "", so a title with no ASCII-able characters needs a fallback."""
    assert make(category, "日本語のタイトル").slug == "untitled"


def test_the_create_form_does_not_require_a_slug(category, client, staff_user):
    client.force_login(staff_user)

    resp = client.post(
        reverse("kb:article_create"),
        {"title": "Posted without a slug", "body": "...", "category": category.pk, "is_published": "on"},
        HTTP_HOST="testserver",
    )

    assert resp.status_code == 302
    assert Article.objects.get(title="Posted without a slug").slug == "posted-without-a-slug"


def test_the_editor_previews_markdown_as_html(client, staff_user):
    client.force_login(staff_user)

    resp = client.post(reverse("kb:article_preview"), {"body": "# Title\n\n- one\n- two"}, HTTP_HOST="testserver")

    assert resp.status_code == 200
    body = resp.content.decode()
    assert "<h1" in body
    assert "<li>one</li>" in body


def test_the_preview_is_staff_only(client, django_user_model):
    user = django_user_model.objects.create_user(email="not-staff@example.com", password="pw")
    client.force_login(user)

    resp = client.post(reverse("kb:article_preview"), {"body": "# Title"}, HTTP_HOST="testserver")

    assert resp.status_code == 403


def test_the_editor_links_to_a_cheatsheet_the_help_page_actually_has(client, staff_user):
    """The link is only useful if the anchor it points at exists."""
    client.force_login(staff_user)

    editor = client.get(reverse("kb:article_create"), HTTP_HOST="testserver").content.decode()
    help_page = client.get(reverse("help"), HTTP_HOST="testserver").content.decode()

    assert f'href="{reverse("help")}#markdown"' in editor
    assert 'id="markdown"' in help_page
