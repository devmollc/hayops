from django.urls import path

from . import views

app_name = "kb"

urlpatterns = [
    path("", views.article_list, name="article_list"),
    path("create/", views.article_create, name="article_create"),
    path("search/", views.kb_search, name="kb_search"),
    path("preview/", views.article_preview, name="article_preview"),
    path("<slug:slug>/", views.article_detail, name="article_detail"),
    path("<slug:slug>/edit/", views.article_edit, name="article_edit"),
    path("<slug:slug>/delete/", views.article_delete, name="article_delete"),
]
