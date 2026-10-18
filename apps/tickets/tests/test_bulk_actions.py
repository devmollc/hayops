import pytest
from django.urls import reverse

from apps.accounts.models import User
from apps.tickets.models import Attachment, Comment, Ticket, TicketEvent, TicketParticipant
from apps.tickets.services import TicketService

pytestmark = pytest.mark.django_db

BULK_URL = reverse("tickets:bulk_action")


@pytest.fixture
def superuser(db):
    return User.objects.create_superuser(email="root@devmosolutions.com", password="x")


@pytest.fixture
def other_agent(db):
    return User.objects.create_user(email="agent2@devmosolutions.com", password="x", is_staff=True)


def make_ticket(source_app, subject="Thing broke", **kwargs):
    t = Ticket.objects.create(subject=subject, body="Details.", source_application=source_app, **kwargs)
    TicketParticipant.objects.create(
        ticket=t, email="customer@example.com", role=TicketParticipant.Role.SUBMITTER
    )
    return t


# --- Permissions -------------------------------------------------------------


def test_bulk_requires_staff(client, source_app, db):
    customer = User.objects.create_user(email="nosy@example.com", password="x", is_staff=False)
    ticket = make_ticket(source_app)
    client.force_login(customer)

    resp = client.post(BULK_URL, {"action": "update", "ticket_ids": [ticket.pk], "status": "closed"})

    assert resp.status_code == 403
    ticket.refresh_from_db()
    assert ticket.status == Ticket.Status.NEW


def test_bulk_delete_rejects_plain_staff(client, staff_user, source_app):
    ticket = make_ticket(source_app)
    client.force_login(staff_user)

    resp = client.post(
        BULK_URL, {"action": "delete", "ticket_ids": [ticket.pk], "confirm": "DELETE"}
    )

    assert resp.status_code == 403
    assert Ticket.objects.filter(pk=ticket.pk).exists()


def test_bulk_delete_requires_typed_confirmation(client, superuser, source_app):
    ticket = make_ticket(source_app)
    client.force_login(superuser)

    resp = client.post(BULK_URL, {"action": "delete", "ticket_ids": [ticket.pk], "confirm": "delete"})

    assert resp.status_code == 302
    assert Ticket.objects.filter(pk=ticket.pk).exists()


# --- Update path -------------------------------------------------------------


def test_bulk_resolve_sets_resolved_at_and_audit_trail(client, staff_user, source_app):
    """The whole point of routing through the service: queryset.update() skips both."""
    tickets = [make_ticket(source_app, subject=f"T{i}") for i in range(3)]
    client.force_login(staff_user)

    resp = client.post(
        BULK_URL,
        {"action": "update", "ticket_ids": [t.pk for t in tickets], "status": "resolved"},
    )

    assert resp.status_code == 302
    for t in tickets:
        t.refresh_from_db()
        assert t.status == Ticket.Status.RESOLVED
        assert t.resolved_at is not None, "resolved_at drives the resolution and SLA reports"
        assert TicketEvent.objects.filter(
            ticket=t, event_type=TicketEvent.EventType.STATUS_CHANGED, actor=staff_user
        ).exists()


def test_bulk_update_applies_status_priority_and_assignee_together(
    client, staff_user, other_agent, source_app
):
    ticket = make_ticket(source_app)
    client.force_login(staff_user)

    client.post(
        BULK_URL,
        {
            "action": "update",
            "ticket_ids": [ticket.pk],
            "status": "pending",
            "priority": "urgent",
            "assigned_to": other_agent.pk,
        },
    )

    ticket.refresh_from_db()
    assert ticket.status == Ticket.Status.PENDING
    assert ticket.priority == Ticket.Priority.URGENT
    assert ticket.assigned_to == other_agent


def test_bulk_unassign(client, staff_user, other_agent, source_app):
    ticket = make_ticket(source_app, assigned_to=other_agent)
    client.force_login(staff_user)

    client.post(BULK_URL, {"action": "update", "ticket_ids": [ticket.pk], "assigned_to": "__none__"})

    ticket.refresh_from_db()
    assert ticket.assigned_to is None


def test_empty_assignee_leaves_assignment_alone(client, staff_user, other_agent, source_app):
    ticket = make_ticket(source_app, assigned_to=other_agent)
    client.force_login(staff_user)

    client.post(
        BULK_URL, {"action": "update", "ticket_ids": [ticket.pk], "status": "closed", "assigned_to": ""}
    )

    ticket.refresh_from_db()
    assert ticket.status == Ticket.Status.CLOSED
    assert ticket.assigned_to == other_agent


def test_invalid_status_is_rejected(client, staff_user, source_app):
    ticket = make_ticket(source_app)
    client.force_login(staff_user)

    client.post(BULK_URL, {"action": "update", "ticket_ids": [ticket.pk], "status": "merged"})

    ticket.refresh_from_db()
    assert ticket.status == Ticket.Status.NEW


def test_bulk_rejects_oversized_selection(client, staff_user, source_app):
    from apps.tickets.services import BULK_MAX

    ticket = make_ticket(source_app)
    client.force_login(staff_user)

    resp = client.post(
        BULK_URL,
        {"action": "update", "ticket_ids": [ticket.pk] * (BULK_MAX + 1), "status": "closed"},
    )

    assert resp.status_code == 302
    ticket.refresh_from_db()
    assert ticket.status == Ticket.Status.NEW


# --- Notifications -----------------------------------------------------------


NOTIFICATION_TASK = "apps.inbound_email.tasks.send_ticket_notification"


def _queued(connector, task_name):
    return [j for j in connector.jobs.values() if task_name in j["task_name"]]


def _deferred_types(connector):
    return [j["args"]["notification_type"] for j in connector.jobs.values() if j["task_name"] == NOTIFICATION_TASK]


def test_bulk_update_is_silent_by_default(client, staff_user, source_app, job_connector):
    ticket = make_ticket(source_app)
    client.force_login(staff_user)

    client.post(BULK_URL, {"action": "update", "ticket_ids": [ticket.pk], "status": "resolved"})

    assert not _queued(job_connector, "send_ticket_notification"), (
        "bulk sweeps must not mail every submitter by default"
    )
    # Suppressing the customer email must not cost the audit trail or the integrations.
    assert _queued(job_connector, "dispatch_webhook"), "webhook subscribers still need to hear it"
    assert TicketEvent.objects.filter(
        ticket=ticket, event_type=TicketEvent.EventType.STATUS_CHANGED
    ).exists()


def test_notify_opt_in_sends_csat_survey(
    client, staff_user, source_app, job_connector, django_capture_on_commit_callbacks
):
    ticket = make_ticket(source_app)
    client.force_login(staff_user)

    with django_capture_on_commit_callbacks(execute=True):
        client.post(
            BULK_URL,
            {"action": "update", "ticket_ids": [ticket.pk], "status": "resolved", "notify": "on"},
        )

    assert _deferred_types(job_connector) == ["csat_survey"]


def test_single_ticket_path_still_notifies(
    client, staff_user, source_app, job_connector, django_capture_on_commit_callbacks
):
    """The notify flag defaults to True, so the detail view is unaffected."""
    ticket = make_ticket(source_app)
    client.force_login(staff_user)

    with django_capture_on_commit_callbacks(execute=True):
        client.post(reverse("tickets:change_status", args=[ticket.pk]), {"status": "resolved"})

    assert _deferred_types(job_connector) == ["csat_survey"]


# --- Delete ------------------------------------------------------------------


def test_bulk_delete_removes_ticket_and_children(client, superuser, source_app):
    ticket = make_ticket(source_app)
    Comment.objects.create(ticket=ticket, body="hello", author_email="customer@example.com")
    client.force_login(superuser)

    resp = client.post(
        BULK_URL, {"action": "delete", "ticket_ids": [ticket.pk], "confirm": "DELETE"}
    )

    assert resp.status_code == 302
    assert not Ticket.objects.filter(pk=ticket.pk).exists()
    assert not Comment.objects.filter(ticket_id=ticket.pk).exists()
    assert not TicketParticipant.objects.filter(ticket_id=ticket.pk).exists()


def test_delete_purges_attachment_files_from_storage(
    client, superuser, source_app, django_capture_on_commit_callbacks
):
    from django.core.files.base import ContentFile

    ticket = make_ticket(source_app)
    attachment = Attachment.objects.create(
        ticket=ticket, file=ContentFile(b"payload", name="notes.txt"),
        filename="notes.txt", size_bytes=7,
    )
    storage, name = attachment.file.storage, attachment.file.name
    assert storage.exists(name)

    client.force_login(superuser)
    # The purge runs on_commit deliberately, so a rolled-back delete keeps its files.
    with django_capture_on_commit_callbacks(execute=True):
        client.post(BULK_URL, {"action": "delete", "ticket_ids": [ticket.pk], "confirm": "DELETE"})

    assert not storage.exists(name), "cascade drops the row but leaves the stored object"
    storage.delete(name)


def test_delete_repoints_merge_tombstone_to_surviving_target(staff_user, source_app):
    """A -> B -> C. Deleting B must leave A pointing at C, not at nothing."""
    a, b, c = (make_ticket(source_app, subject=s) for s in ("A", "B", "C"))
    TicketService.merge_tickets(b, c, actor=staff_user)
    TicketService.merge_tickets(a, b, actor=staff_user)

    b.refresh_from_db()
    TicketService.delete_tickets([b], actor=staff_user)

    a.refresh_from_db()
    assert a.merged_into == c
    assert a.status == Ticket.Status.MERGED


def test_delete_closes_tombstone_when_no_target_survives(staff_user, source_app):
    source, target = make_ticket(source_app, subject="src"), make_ticket(source_app, subject="tgt")
    TicketService.merge_tickets(source, target, actor=staff_user)

    target.refresh_from_db()
    TicketService.delete_tickets([target], actor=staff_user)

    source.refresh_from_db()
    assert source.merged_into is None
    assert source.status == Ticket.Status.CLOSED, "a merged ticket with no target is a dead end"
    assert TicketEvent.objects.filter(
        ticket=source, event_type=TicketEvent.EventType.MERGED
    ).count() == 2


def test_delete_whole_merge_chain_at_once(staff_user, source_app):
    source, target = make_ticket(source_app, subject="src"), make_ticket(source_app, subject="tgt")
    TicketService.merge_tickets(source, target, actor=staff_user)
    source.refresh_from_db()
    target.refresh_from_db()

    assert TicketService.delete_tickets([source, target], actor=staff_user) == 2
    assert Ticket.objects.count() == 0


# --- Queue rendering ---------------------------------------------------------


def test_queue_renders_selection_controls_for_staff(client, staff_user, source_app):
    make_ticket(source_app)
    client.force_login(staff_user)

    html = client.get(reverse("tickets:queue")).content.decode()

    assert 'name="ticket_ids"' in html
    assert reverse("tickets:bulk_action") in html


def test_delete_button_hidden_from_non_superuser_staff(client, staff_user, superuser, source_app):
    make_ticket(source_app)

    client.force_login(staff_user)
    assert "Delete permanently" not in client.get(reverse("tickets:queue")).content.decode()

    client.force_login(superuser)
    assert "Delete permanently" in client.get(reverse("tickets:queue")).content.decode()


# --- Django admin actions ----------------------------------------------------


ADMIN_TICKETS_URL = "/admin/tickets/ticket/"


def _admin_action(client, action, tickets, **extra):
    return client.post(
        ADMIN_TICKETS_URL,
        {"action": action, "_selected_action": [t.pk for t in tickets], **extra},
        follow=True,
    )


def test_admin_mark_resolved_sets_resolved_at_and_audit_trail(client, superuser, source_app):
    """Regression: the action used queryset.update(), which skipped both."""
    tickets = [make_ticket(source_app, subject=f"A{i}") for i in range(2)]
    client.force_login(superuser)

    _admin_action(client, "mark_resolved", tickets)

    for t in tickets:
        t.refresh_from_db()
        assert t.status == Ticket.Status.RESOLVED
        assert t.resolved_at is not None
        assert TicketEvent.objects.filter(
            ticket=t, event_type=TicketEvent.EventType.STATUS_CHANGED, actor=superuser
        ).exists()


def test_admin_priority_action_records_event(client, superuser, source_app):
    ticket = make_ticket(source_app)
    client.force_login(superuser)

    _admin_action(client, "set_priority_urgent", [ticket])

    ticket.refresh_from_db()
    assert ticket.priority == Ticket.Priority.URGENT
    assert TicketEvent.objects.filter(
        ticket=ticket, event_type=TicketEvent.EventType.PRIORITY_CHANGED
    ).exists()


def test_admin_action_does_not_email_customers(client, superuser, source_app, job_connector):
    ticket = make_ticket(source_app)
    client.force_login(superuser)

    _admin_action(client, "mark_resolved", [ticket])

    assert not _queued(job_connector, "send_ticket_notification")


def test_admin_delete_purges_files_and_repoints_tombstones(
    client, superuser, source_app, django_capture_on_commit_callbacks
):
    from django.core.files.base import ContentFile

    source, target = make_ticket(source_app, subject="src"), make_ticket(source_app, subject="tgt")
    TicketService.merge_tickets(source, target, actor=superuser)
    target.refresh_from_db()

    attachment = Attachment.objects.create(
        ticket=target, file=ContentFile(b"x", name="doc.txt"), filename="doc.txt", size_bytes=1
    )
    storage, name = attachment.file.storage, attachment.file.name

    client.force_login(superuser)
    with django_capture_on_commit_callbacks(execute=True):
        client.post(
            ADMIN_TICKETS_URL,
            {"action": "delete_selected", "_selected_action": [target.pk], "post": "yes"},
            follow=True,
        )

    assert not Ticket.objects.filter(pk=target.pk).exists()
    assert not storage.exists(name), "admin delete must not orphan attachment files"
    source.refresh_from_db()
    assert source.merged_into is None and source.status == Ticket.Status.CLOSED


def test_admin_action_refuses_oversized_selection(client, superuser, source_app, monkeypatch):
    monkeypatch.setattr("apps.tickets.admin.BULK_MAX", 1)
    tickets = [make_ticket(source_app, subject=f"B{i}") for i in range(2)]
    client.force_login(superuser)

    resp = _admin_action(client, "mark_closed", tickets)

    assert "Select at most 1 tickets" in resp.content.decode()
    for t in tickets:
        t.refresh_from_db()
        assert t.status == Ticket.Status.NEW
