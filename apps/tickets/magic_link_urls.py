from django.urls import path

from .magic_link import ticket_magic_link_view

urlpatterns = [
    path("ticket/<str:token>/", ticket_magic_link_view, name="ticket_magic_link"),
]
