from django.urls import path

from . import auth_views, views

app_name = "portal"

urlpatterns = [
    path("submit/", views.submit, name="submit"),
    path("portal/login/", auth_views.login_request, name="login"),
    path("portal/login/<str:token>/", auth_views.login_verify, name="login_verify"),
    path("portal/logout/", auth_views.logout_view, name="logout"),
    path("portal/tickets/", auth_views.ticket_list, name="ticket_list"),
    path("portal/tickets/<str:reference>/", auth_views.ticket_detail, name="ticket_detail"),
]
