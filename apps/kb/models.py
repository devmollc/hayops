from django.conf import settings
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVectorField
from django.db import models
from django.utils.text import slugify

from apps.core.models import TimestampedModel


def _unique_slug(instance, source):
    """Slugify `source`, truncated to the field and suffixed until it is unique.

    Truncation is not optional: Article.title allows 200 characters and a
    SlugField is 50, so slugifying a long title unchecked writes a value the
    column cannot hold.

    ponytail: check-then-insert, so two authors saving the same title in the
    same instant still collide. Retry on IntegrityError if that ever happens.
    """
    max_length = instance._meta.get_field("slug").max_length
    # A title with no ASCII-able characters slugifies to "", which a SlugField
    # rejects -- fall back rather than raise on someone's non-Latin title.
    base = slugify(source)[:max_length].rstrip("-") or "untitled"

    slug, n = base, 1
    taken = type(instance)._default_manager.exclude(pk=instance.pk)
    while taken.filter(slug=slug).exists():
        n += 1
        suffix = f"-{n}"
        slug = f"{base[: max_length - len(suffix)].rstrip('-')}{suffix}"
    return slug


class Category(TimestampedModel):
    name = models.CharField(max_length=100)
    slug = models.SlugField(unique=True)
    order = models.IntegerField(default=0)

    class Meta:
        ordering = ["order", "name"]
        verbose_name_plural = "categories"

    def __str__(self):
        return self.name


class Article(TimestampedModel):
    title = models.CharField(max_length=200)
    slug = models.SlugField(unique=True, blank=True, help_text="Leave blank to generate one from the title.")
    body = models.TextField(help_text="Markdown content")
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name="articles")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="kb_articles"
    )
    is_published = models.BooleanField(default=False)
    source_application = models.ForeignKey(
        "core.SourceApplication",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="articles",
        help_text="Blank = visible on every portal",
    )
    search_vector = SearchVectorField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            GinIndex(fields=["search_vector"]),
        ]

    def save(self, *args, **kwargs):
        # Here rather than in ArticleForm so every writer gets it -- the staff
        # form, the admin, fixtures and the shell. An existing slug is never
        # regenerated from an edited title, because it is the article's URL.
        if not self.slug:
            self.slug = _unique_slug(self, self.title)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.title
