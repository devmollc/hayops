from django.urls import path

from . import views

app_name = "tickets"

urlpatterns = [
    path("", views.queue_view, name="queue"),
    path("create/", views.create_ticket, name="create"),
    path("activity/", views.ticket_activity, name="activity"),
    # Must stay above <str:reference>/, which would otherwise swallow any
    # single-segment path -- serving it as a ticket whose reference is "bulk"
    # or "canned". Deeper paths (canned/new/) are safe wherever they sit.
    path("bulk/", views.bulk_action, name="bulk_action"),
    path("canned/", views.canned_response_list, name="canned_list"),
    path("<str:reference>/", views.ticket_detail, name="detail"),
    path("<int:pk>/edit/", views.edit_ticket, name="edit"),
    path("<int:pk>/claim/", views.claim_ticket, name="claim"),
    path("<int:pk>/assign/", views.assign_ticket, name="assign"),
    path("<int:pk>/comment/", views.add_comment, name="add_comment"),
    path("<int:pk>/status/", views.change_status, name="change_status"),
    path("<int:pk>/priority/", views.change_priority, name="change_priority"),
    path("<int:pk>/tags/add/", views.add_tag, name="add_tag"),
    path("<int:pk>/tags/<int:tag_pk>/remove/", views.remove_tag, name="remove_tag"),
    path("<int:pk>/application/", views.change_source_application, name="change_source_application"),
    path("<int:pk>/merge/", views.merge_ticket, name="merge"),
    path("<int:pk>/heartbeat/", views.heartbeat, name="heartbeat"),
    path("<int:pk>/timeline/", views.timeline_poll, name="timeline_poll"),
    path("search/tickets/", views.search_tickets, name="search_tickets"),
    path("search/canned/", views.canned_response_search, name="canned_search"),
    path("canned/new/", views.canned_response_form, name="canned_create"),
    path("canned/<int:pk>/edit/", views.canned_response_form, name="canned_edit"),
    path("canned/<int:pk>/delete/", views.canned_response_delete, name="canned_delete"),
    path("canned/<int:pk>/use/", views.use_canned_response, name="use_canned"),
    path("views/save/", views.save_view, name="save_view"),
    path("views/<int:pk>/delete/", views.delete_saved_view, name="delete_saved_view"),
    path("<int:pk>/resend-link/", views.resend_magic_link, name="resend_magic_link"),
]
