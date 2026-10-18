from django.urls import path, re_path

from . import views

app_name = "testbench"

urlpatterns = [
    path("", views.index, name="index"),
    path("tokens/", views.tokens, name="tokens"),
    path("tokens/<int:pk>/delete/", views.token_delete, name="token_delete"),
    path("<slug:slug>/", views.board, name="board"),
    path("<slug:slug>/runs/new/", views.run_start, name="run_start"),
    path("<slug:slug>/import/", views.suite_import, name="suite_import"),
    path("<slug:slug>/export/", views.suite_export, name="suite_export"),
    path("<slug:slug>/sections/new/", views.section_form, name="section_create"),
    path("<slug:slug>/sections/<str:code>/edit/", views.section_form, name="section_edit"),
    path("<slug:slug>/results/<int:pk>/", views.result_update, name="result_update"),
    path("<slug:slug>/cases/new/", views.case_form, name="case_create"),
    path("<slug:slug>/cases/<str:key>/", views.case_detail, name="case_detail"),
    path("<slug:slug>/cases/<str:key>/edit/", views.case_form, name="case_edit"),
    path("<slug:slug>/cases/<str:key>/delete/", views.case_delete, name="case_delete"),
    re_path(
        r"^(?P<slug>[-\w]+)/cases/(?P<key>[^/]+)/(?P<action>claim|release|record)/$",
        views.case_action,
        name="case_action",
    ),
]
