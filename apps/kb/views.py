from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.postgres.search import SearchQuery, SearchRank
from django.db.models import Prefetch, Q
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.core.models import SourceApplication

from .forms import ArticleForm
from .models import Article, Category
from .templatetags.kb_tags import render_markdown


def _visible_to(request, articles):
    if request.source_application:
        return articles.filter(Q(source_application=request.source_application) | Q(source_application__isnull=True))
    return articles


def article_list(request):
    visible = _visible_to(request, Article.objects.filter(is_published=True))

    # A portal is already scoped to its own app; the filter is for the admin
    # domain, where every app's articles are mixed together. Global articles
    # (no application) belong to every app, so they stay in any selection.
    applications, selected_apps = [], []
    if not request.source_application:
        applications = SourceApplication.objects.filter(articles__in=visible).distinct().order_by("name")
        selected_apps = request.GET.getlist("app")
        if selected_apps:
            visible = visible.filter(Q(source_application__slug__in=selected_apps) | Q(source_application__isnull=True))

    articles = visible.select_related("category", "author")

    q = request.GET.get("q")
    if q:
        query = SearchQuery(q, search_type="websearch")
        articles = (
            articles.filter(search_vector=query).annotate(rank=SearchRank("search_vector", query)).order_by("-rank")
        )

    # The template reads category.articles.all directly, so the prefetch queryset
    # (not just `articles` above) must carry the same visibility filter.
    categories = Category.objects.prefetch_related(
        Prefetch("articles", queryset=visible)
    ).all()

    context = {
        "articles": articles,
        "categories": categories,
        "search_query": q or "",
        "applications": applications,
        "selected_apps": selected_apps,
        "base_template": "portal/base.html" if request.source_application else "base.html",
    }
    return render(request, "kb/article_list.html", context)


def article_detail(request, slug):
    articles = _visible_to(request, Article.objects.select_related("category", "author").filter(is_published=True))
    article = get_object_or_404(articles, slug=slug)
    context = {
        "article": article,
        "base_template": "portal/base.html" if request.source_application else "base.html",
    }
    return render(request, "kb/article_detail.html", context)


@login_required
def article_create(request):
    if not request.user.is_staff:
        messages.error(request, "You do not have permission to create articles.")
        return redirect("kb:article_list")

    if request.method == "POST":
        form = ArticleForm(request.POST)
        if form.is_valid():
            article = form.save(commit=False)
            article.author = request.user
            article.save()
            messages.success(request, f'Article "{article.title}" created.')
            return redirect("kb:article_detail", slug=article.slug)
    else:
        form = ArticleForm()

    return render(request, "kb/article_form.html", {"form": form, "editing": False})


@login_required
def article_edit(request, slug):
    if not request.user.is_staff:
        messages.error(request, "You do not have permission to edit articles.")
        return redirect("kb:article_list")

    article = get_object_or_404(Article, slug=slug)

    if request.method == "POST":
        form = ArticleForm(request.POST, instance=article)
        if form.is_valid():
            form.save()
            messages.success(request, f'Article "{article.title}" updated.')
            return redirect("kb:article_detail", slug=article.slug)
    else:
        form = ArticleForm(instance=article)

    return render(request, "kb/article_form.html", {"form": form, "editing": True, "article": article})


@login_required
def article_delete(request, slug):
    if not request.user.is_staff:
        messages.error(request, "You do not have permission to delete articles.")
        return redirect("kb:article_list")

    article = get_object_or_404(Article, slug=slug)

    if request.method == "POST":
        title = article.title
        article.delete()
        messages.success(request, f'Article "{title}" deleted.')
        return redirect("kb:article_list")

    return render(request, "kb/article_confirm_delete.html", {"article": article})


@login_required
@require_POST
def article_preview(request):
    """Render the editor's Markdown the way article_detail will render it."""
    if not request.user.is_staff:
        return HttpResponseForbidden("Staff only.")
    return HttpResponse(render_markdown(request.POST.get("body", "")))


@login_required
def kb_search(request):
    q = request.GET.get("q", "")
    articles = _visible_to(request, Article.objects.filter(is_published=True))

    if q:
        query = SearchQuery(q, search_type="websearch")
        articles = (
            articles.filter(search_vector=query)
            .annotate(rank=SearchRank("search_vector", query))
            .order_by("-rank")[:10]
        )
    else:
        articles = articles[:10]

    return render(request, "kb/partials/search_results.html", {"articles": articles, "search_query": q})
