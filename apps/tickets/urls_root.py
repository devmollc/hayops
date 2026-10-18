from django.urls import path

from apps.portal.views import home

urlpatterns = [
    path("", home, name="home"),
]
