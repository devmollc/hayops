from django.urls import path

from . import views

app_name = "webhooks"

urlpatterns = [
    path("", views.endpoint_list, name="endpoint_list"),
    path("create/", views.endpoint_create, name="endpoint_create"),
    path("<int:pk>/edit/", views.endpoint_edit, name="endpoint_edit"),
    path("<int:pk>/delete/", views.endpoint_delete, name="endpoint_delete"),
    path("<int:pk>/deliveries/", views.delivery_list, name="delivery_list"),
]
