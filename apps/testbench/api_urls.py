from django.urls import path

from . import api

urlpatterns = [
    path("", api.application_list, name="testbench-api-apps"),
    path("<slug:slug>/suite/", api.suite, name="testbench-api-suite"),
    path("<slug:slug>/cases/", api.case_create, name="testbench-api-case-create"),
    path("<slug:slug>/cases/<str:key>/", api.case_detail, name="testbench-api-case"),
    path("<slug:slug>/cases/<str:key>/history/", api.case_history, name="testbench-api-case-history"),
    path("<slug:slug>/runs/", api.runs, name="testbench-api-runs"),
    path("<slug:slug>/runs/current/", api.current_run, name="testbench-api-current-run"),
]
