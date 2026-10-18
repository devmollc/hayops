from django.contrib import admin
from django.db.models import Count, Q

from .models import Article, Category


class ArticleInline(admin.StackedInline):
    model = Article
    extra = 0
    fields = ("title", "slug", "author", "is_published")
    prepopulated_fields = {"slug": ("title",)}
    show_change_link = True


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "order", "article_count", "published_count")
    search_fields = ("name",)
    prepopulated_fields = {"slug": ("name",)}
    inlines = [ArticleInline]

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .annotate(
                _article_count=Count("articles"),
                _published_count=Count("articles", filter=Q(articles__is_published=True)),
            )
        )

    @admin.display(description="Articles", ordering="_article_count")
    def article_count(self, obj):
        return obj._article_count

    @admin.display(description="Published", ordering="_published_count")
    def published_count(self, obj):
        return obj._published_count


@admin.register(Article)
class ArticleAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "category",
        "source_application",
        "author",
        "is_published_icon",
        "created_at",
        "updated_at",
    )
    list_filter = ("is_published", "category", "source_application")
    search_fields = ("title", "slug", "body")
    prepopulated_fields = {"slug": ("title",)}
    raw_id_fields = ("author",)
    date_hierarchy = "created_at"
    readonly_fields = ("search_vector", "created_at", "updated_at")
    list_per_page = 30

    fieldsets = (
        (None, {"fields": ("title", "slug", "category", "source_application", "author")}),
        ("Content", {"fields": ("body",)}),
        ("Publishing", {"fields": ("is_published",)}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )

    actions = ["publish", "unpublish"]

    @admin.display(description="Published", boolean=True, ordering="is_published")
    def is_published_icon(self, obj):
        return obj.is_published

    @admin.action(description="Publish selected articles")
    def publish(self, request, queryset):
        count = queryset.update(is_published=True)
        self.message_user(request, f"Published {count} article(s).")

    @admin.action(description="Unpublish selected articles")
    def unpublish(self, request, queryset):
        count = queryset.update(is_published=False)
        self.message_user(request, f"Unpublished {count} article(s).")
