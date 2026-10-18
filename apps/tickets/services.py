import logging

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import (
    Attachment,
    Comment,
    Ticket,
    TicketEvent,
    TicketParticipant,
)

logger = logging.getLogger(__name__)


def customer_scope(field, source_application):
    """Limit a customer-facing queryset to one application's tickets.

    Matches this application, plus tickets belonging to no application at all.
    A ticket gets its application from the recipient domain it arrived on or the
    staff create form, so the unattributed ones are legacy; scoping them out
    would hide them from the portal *and* every widget, leaving the customer no
    way to reach a ticket they can see referenced in their own email.

    Lives here because the portal and the widget API both need exactly this rule
    and it decides what one customer of one product is allowed to read.
    """
    if source_application is None:
        return Q()
    return Q(**{field: source_application}) | Q(**{f"{field}__isnull": True})


# Distinguishes "leave the assignee alone" from "unassign", which is a real None.
_UNCHANGED = object()

# bulk_update walks tickets one at a time on purpose, so an unbounded selection is a
# hang rather than a slow query. Both the staff queue and the admin refuse past this.
BULK_MAX = 200

# Upload limits. The portal submit form is unauthenticated, so these are a trust
# boundary, not a nicety.
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_ATTACHMENTS = 5


def save_uploads(files, create):
    """Apply the upload limits, persisting each accepted file with `create(**fields)`.

    Returns (saved, errors). Oversized files are rejected individually rather
    than failing the whole submission — losing a customer's message because one
    screenshot was too big is worse than losing the screenshot.

    filename/content_type are truncated to their column widths: both come straight
    from the client, and a 300-char filename would otherwise be a DataError.
    """
    saved, errors = [], []
    for f in files[:MAX_ATTACHMENTS]:
        if f.size > MAX_ATTACHMENT_BYTES:
            errors.append(f"{f.name} is larger than {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB and was not attached.")
            continue
        saved.append(
            create(file=f, filename=f.name[:255], content_type=(f.content_type or "")[:100], size_bytes=f.size)
        )
    if len(files) > MAX_ATTACHMENTS:
        errors.append(f"Only the first {MAX_ATTACHMENTS} files were attached.")
    return saved, errors


def save_attachments(ticket, files, comment=None):
    """Persist uploaded files against a ticket (and optionally a comment). See save_uploads."""
    return save_uploads(files, lambda **fields: Attachment.objects.create(ticket=ticket, comment=comment, **fields))


def _user_for_email(email):
    """Existing account for this address, if any.

    Portal, email and anonymous widget submissions arrive with no user object.
    Linking them here means a customer who already signed in once sees new tickets
    in their portal list without having to sign in again.
    """
    if not email:
        return None
    from apps.accounts.models import User

    return User.objects.filter(email__iexact=email).first()


class TicketService:
    UNCHANGED = _UNCHANGED

    @staticmethod
    @transaction.atomic
    def create_ticket(subject, body, submitter_email, submitter_user=None, queue=None, priority=None,
                      source_application=None, custom_fields=None, actor=None):
        """Open a ticket and tell the submitter about it.

        `submitter_user` is whose ticket this is; `actor` is who filed it. They are the
        same for portal, email and widget submissions, but not when an agent opens a
        ticket for a customer -- there the agent is the actor and must not be recorded
        as the submitter, or the ticket looks staff-owned and the customer is never
        mailed. `actor` defaults to `submitter_user` for the self-service paths.
        """
        ticket = Ticket.objects.create(
            subject=subject,
            body=body,
            queue=queue,
            priority=priority or Ticket.Priority.NORMAL,
            source_application=source_application,
            custom_fields=custom_fields or {},
        )

        TicketParticipant.objects.create(
            ticket=ticket,
            email=submitter_email,
            user=submitter_user or _user_for_email(submitter_email),
            role=TicketParticipant.Role.SUBMITTER,
        )

        TicketEvent.objects.create(
            ticket=ticket,
            event_type=TicketEvent.EventType.CREATED,
            actor=actor or submitter_user,
            new_value={"status": ticket.status, "priority": ticket.priority},
        )

        def _notify():
            from apps.inbound_email.tasks import send_ticket_notification

            send_ticket_notification.defer(ticket_id=ticket.pk, notification_type="created")
            send_ticket_notification.defer(ticket_id=ticket.pk, notification_type="team_new_ticket")

        transaction.on_commit(_notify)

        return ticket

    @staticmethod
    @transaction.atomic
    def change_status(ticket, new_status, actor=None, notify=True):
        """Move a ticket to new_status, recording the change and telling the customer.

        notify=False keeps the state change, the audit event and the webhook but skips
        the customer email. Bulk sweeps use it so closing a hundred stale tickets does
        not put a hundred messages in a hundred inboxes.
        """
        old_status = ticket.status
        if old_status == new_status:
            return ticket

        ticket.status = new_status

        if new_status == Ticket.Status.RESOLVED and not ticket.resolved_at:
            ticket.resolved_at = timezone.now()

        ticket.save(update_fields=["status", "resolved_at", "updated_at"])

        TicketEvent.objects.create(
            ticket=ticket,
            event_type=TicketEvent.EventType.STATUS_CHANGED,
            actor=actor,
            old_value={"status": old_status},
            new_value={"status": new_status},
        )

        def _notify():
            from apps.inbound_email.tasks import send_ticket_notification

            notif_type = "csat_survey" if new_status == Ticket.Status.RESOLVED else "status_changed"
            send_ticket_notification.defer(ticket_id=ticket.pk, notification_type=notif_type)

        if notify:
            transaction.on_commit(_notify)

        return ticket

    @staticmethod
    @transaction.atomic
    def assign_ticket(ticket, assignee, actor=None):
        old_assignee = ticket.assigned_to
        ticket.assigned_to = assignee

        if ticket.status == Ticket.Status.NEW:
            ticket.status = Ticket.Status.OPEN

        ticket.save(update_fields=["assigned_to", "status", "updated_at"])

        TicketEvent.objects.create(
            ticket=ticket,
            event_type=TicketEvent.EventType.ASSIGNED,
            actor=actor,
            old_value={"assigned_to": old_assignee.email if old_assignee else None},
            new_value={"assigned_to": assignee.email if assignee else None},
        )

        return ticket

    @staticmethod
    @transaction.atomic
    def add_comment(ticket, body, author=None, author_email="", comment_type=Comment.CommentType.PUBLIC):
        is_first = bool(
            comment_type == Comment.CommentType.PUBLIC
            and author
            and author.is_staff
            and not ticket.first_responded_at
        )

        comment = Comment.objects.create(
            ticket=ticket,
            author=author,
            author_email=author_email or (author.email if author else ""),
            body=body,
            comment_type=comment_type,
            is_first_response=is_first,
        )

        if is_first:
            ticket.first_responded_at = timezone.now()
            ticket.save(update_fields=["first_responded_at", "updated_at"])

        if ticket.status == Ticket.Status.NEW and author and author.is_staff:
            ticket.status = Ticket.Status.OPEN
            ticket.save(update_fields=["status", "updated_at"])

        TicketEvent.objects.create(
            ticket=ticket,
            event_type=TicketEvent.EventType.COMMENT_ADDED,
            actor=author,
            new_value={"comment_type": comment_type, "comment_id": comment.pk},
        )

        # Add author as participant if not already
        if author_email or (author and author.email):
            email = author_email or author.email
            participant, created = TicketParticipant.objects.get_or_create(
                ticket=ticket,
                email=email,
                defaults={"user": author, "role": TicketParticipant.Role.COLLABORATOR},
            )
            # get_or_create's defaults don't touch an existing row, so a participant
            # recorded before the author had an account stays unlinked forever.
            if not created and participant.user_id is None:
                participant.user = author or _user_for_email(email)
                if participant.user_id is not None:
                    participant.save(update_fields=["user"])

        if comment_type == Comment.CommentType.PUBLIC:
            def _notify():
                from apps.inbound_email.tasks import send_ticket_notification

                notif_type = "reply" if (author and author.is_staff) else "team_reply"
                send_ticket_notification.defer(
                    ticket_id=ticket.pk, notification_type=notif_type, comment_id=comment.pk
                )

            transaction.on_commit(_notify)

        return comment

    @staticmethod
    @transaction.atomic
    def merge_tickets(source_ticket, target_ticket, actor=None):
        # Move comments to target
        source_ticket.comments.update(ticket=target_ticket)

        # Move attachments to target
        source_ticket.attachments.update(ticket=target_ticket)

        # Merge participants (skip duplicates)
        for participant in source_ticket.participants.all():
            TicketParticipant.objects.get_or_create(
                ticket=target_ticket,
                email=participant.email,
                defaults={"user": participant.user, "role": participant.role},
            )

        # Mark source as merged
        source_ticket.status = Ticket.Status.MERGED
        source_ticket.merged_into = target_ticket
        source_ticket.save(update_fields=["status", "merged_into", "updated_at"])

        # Audit events on both tickets
        TicketEvent.objects.create(
            ticket=source_ticket,
            event_type=TicketEvent.EventType.MERGED,
            actor=actor,
            new_value={"merged_into": target_ticket.reference_number},
        )

        TicketEvent.objects.create(
            ticket=target_ticket,
            event_type=TicketEvent.EventType.MERGED,
            actor=actor,
            new_value={"merged_from": source_ticket.reference_number},
        )

        return target_ticket

    @staticmethod
    @transaction.atomic
    def update_ticket(ticket, actor=None, **fields):
        """Update ticket fields (subject, body, queue, source_application) with audit trail."""
        changes = {}
        update_fields = ["updated_at"]

        for field_name in ("subject", "body", "queue", "source_application"):
            if field_name not in fields:
                continue
            new_value = fields[field_name]
            old_value = getattr(ticket, field_name)
            if old_value == new_value:
                continue

            if field_name in ("queue", "source_application"):
                old_display = str(old_value) if old_value else None
                new_display = str(new_value) if new_value else None
                changes[field_name] = {"old": old_display, "new": new_display}
                if field_name == "source_application":
                    update_fields.append("source_application")
                else:
                    update_fields.append("queue")
            else:
                changes[field_name] = {"old": old_value, "new": new_value}
                update_fields.append(field_name)

            setattr(ticket, field_name, new_value)

        if changes:
            ticket.save(update_fields=update_fields)
            for field_name, vals in changes.items():
                event_type = (
                    TicketEvent.EventType.QUEUE_CHANGED
                    if field_name == "queue"
                    else TicketEvent.EventType.EDITED
                )
                TicketEvent.objects.create(
                    ticket=ticket,
                    event_type=event_type,
                    actor=actor,
                    old_value={field_name: vals["old"]},
                    new_value={field_name: vals["new"]},
                )

        return ticket

    @staticmethod
    @transaction.atomic
    def change_source_application(ticket, new_source_application, actor=None):
        old_app = ticket.source_application
        if old_app == new_source_application:
            return ticket

        ticket.source_application = new_source_application
        ticket.save(update_fields=["source_application", "updated_at"])

        TicketEvent.objects.create(
            ticket=ticket,
            event_type=TicketEvent.EventType.EDITED,
            actor=actor,
            old_value={"source_application": str(old_app) if old_app else None},
            new_value={"source_application": str(new_source_application) if new_source_application else None},
        )

        return ticket

    @staticmethod
    @transaction.atomic
    def change_priority(ticket, new_priority, actor=None):
        old_priority = ticket.priority
        if old_priority == new_priority:
            return ticket

        ticket.priority = new_priority
        ticket.save(update_fields=["priority", "updated_at"])

        TicketEvent.objects.create(
            ticket=ticket,
            event_type=TicketEvent.EventType.PRIORITY_CHANGED,
            actor=actor,
            old_value={"priority": old_priority},
            new_value={"priority": new_priority},
        )

        return ticket

    @staticmethod
    @transaction.atomic
    def bulk_update(tickets, actor=None, status=None, priority=None, assignee=_UNCHANGED, notify=False):
        """Apply any of status/priority/assignee to many tickets at once.

        Every field routes through the same single-ticket method the detail view calls,
        which is the whole point: a queryset.update() would skip resolved_at, the audit
        event and the webhook, and silently break the resolution and SLA reports that
        read resolved_at. One extra query per ticket is a fair price for that.

        Pass assignee=None to unassign; leaving it at UNCHANGED means "don't touch".
        Returns the number of tickets that actually changed.
        """
        changed = 0
        for ticket in tickets:
            touched = False

            if status and ticket.status != status:
                TicketService.change_status(ticket, status, actor=actor, notify=notify)
                touched = True

            if priority and ticket.priority != priority:
                TicketService.change_priority(ticket, priority, actor=actor)
                touched = True

            if assignee is not _UNCHANGED:
                new_id = assignee.pk if assignee else None
                if ticket.assigned_to_id != new_id:
                    TicketService.assign_ticket(ticket, assignee, actor=actor)
                    touched = True

            if touched:
                changed += 1

        return changed

    @staticmethod
    @transaction.atomic
    def delete_tickets(tickets, actor=None):
        """Permanently delete tickets, cleaning up the two things the cascade gets wrong.

        Comments, attachments, events, participants and CSAT rows cascade correctly.
        These do not:

        - `merged_into` is SET_NULL, so the tombstones of a deleted merge target would
          keep status="merged" pointing at nothing. Re-point them at the nearest
          surviving target, or close them if the whole chain is going away.
        - Deleting an Attachment row does not delete the stored file. Collect the files
          and purge them *after* commit, so a rolled-back delete never leaves a live
          ticket pointing at a file that no longer exists.

        Returns the number of tickets deleted.
        """
        tickets = list(tickets)
        if not tickets:
            return 0

        doomed = {t.pk for t in tickets}
        TicketService._repoint_merge_tombstones(tickets, doomed, actor)

        files = [a.file for a in Attachment.objects.filter(ticket_id__in=doomed) if a.file]
        references = [t.reference_number for t in tickets]

        Ticket.objects.filter(pk__in=doomed).delete()

        # The audit trail dies with the ticket, so the log is the only record left.
        logger.warning(
            "Bulk ticket delete by %s: %d ticket(s) %s",
            getattr(actor, "email", "unknown"),
            len(references),
            ", ".join(references),
        )

        def _purge_files():
            for f in files:
                try:
                    f.storage.delete(f.name)
                except Exception:
                    # The rows are already gone; a storage hiccup must not undo that.
                    logger.exception("Could not delete stored file %s", f.name)

        transaction.on_commit(_purge_files)

        return len(references)

    @staticmethod
    def _repoint_merge_tombstones(tickets, doomed, actor):
        for ticket in tickets:
            children = Ticket.objects.filter(merged_into_id=ticket.pk).exclude(pk__in=doomed)
            if not children:
                continue

            target = TicketService._surviving_merge_target(ticket, doomed)
            for child in children:
                child.merged_into = target
                fields = ["merged_into", "updated_at"]
                if target is None:
                    # Nothing left to point at: its content went to a ticket that is
                    # being destroyed, so park it in a terminal state rather than
                    # leaving a "merged" ticket with no target.
                    child.status = Ticket.Status.CLOSED
                    fields.append("status")
                child.save(update_fields=fields)

                TicketEvent.objects.create(
                    ticket=child,
                    event_type=TicketEvent.EventType.MERGED,
                    actor=actor,
                    old_value={"merged_into": ticket.reference_number},
                    new_value={"merged_into": target.reference_number if target else None},
                    metadata={"reason": "merge target deleted"},
                )

    @staticmethod
    def _surviving_merge_target(ticket, doomed):
        """Walk up the merge chain to the first ticket that is not being deleted."""
        seen = set()
        node = ticket.merged_into
        while node is not None and node.pk in doomed and node.pk not in seen:
            seen.add(node.pk)
            node = node.merged_into
        return node
