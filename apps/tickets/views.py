from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Exists, Max, OuterRef, Q
from django.http import Http404, HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from apps.core.models import BouncedAddress, SourceApplication

from .forms import CannedResponseForm, CommentForm, TicketCreateForm, TicketEditForm, TicketFilterForm
from .models import (
    CannedResponse,
    Comment,
    Queue,
    SavedView,
    Tag,
    Ticket,
    TicketEvent,
    TicketParticipant,
)
from .permissions import can_view_ticket
from .services import BULK_MAX, TicketService, save_attachments


def render_or_partial(request, full_template, partial_template, context):
    template = partial_template if request.htmx else full_template
    return render(request, template, context)


def _sidebar_context(ticket):
    from apps.accounts.models import User

    return {"ticket": ticket, "agents": User.objects.filter(is_staff=True, is_active=True)}


def _timeline_context(request, ticket):
    comments = list(ticket.comments.select_related("author").all())
    events = ticket.events.select_related("actor").all()
    # Filter out internal notes for non-staff -- including the "comment added" event.
    if not request.user.is_staff:
        comments = [c for c in comments if c.comment_type != Comment.CommentType.INTERNAL]
        events = events.customer_visible()
    events = list(events)
    timeline = sorted(comments + events, key=lambda x: x.created_at)
    return {
        "ticket": ticket,
        "timeline": timeline,
        "last_event_id": cache.get(f"ticket_activity:{ticket.pk}") or "",
    }


@login_required
def queue_view(request):
    cached_last_event = cache.get("tickets:last_event_id")
    if request.htmx:
        last_event = request.GET.get("last_event")
        if last_event and cached_last_event is not None and str(cached_last_event) == last_event:
            return HttpResponse(status=204)

    # Customers get the portal, not a staff page with the nav stripped out.
    if not request.user.is_staff:
        return redirect("portal:ticket_list")

    tickets = Ticket.objects.select_related("queue", "assigned_to", "source_application").prefetch_related("tags")

    # Filtering
    status = request.GET.get("status")
    priority = request.GET.get("priority")
    queue_slug = request.GET.get("queue")
    assigned = request.GET.get("assigned_to")
    search = request.GET.get("q")

    if status:
        tickets = tickets.filter(status=status)
    else:
        tickets = tickets.exclude(status__in=[Ticket.Status.CLOSED, Ticket.Status.MERGED])

    if priority:
        tickets = tickets.filter(priority=priority)
    if queue_slug and request.user.is_staff:
        tickets = tickets.filter(queue__slug=queue_slug)
    if assigned == "me" and request.user.is_staff:
        tickets = tickets.filter(assigned_to=request.user)
    elif assigned == "unassigned" and request.user.is_staff:
        tickets = tickets.filter(assigned_to__isnull=True)
    elif assigned and request.user.is_staff:
        tickets = tickets.filter(assigned_to_id=assigned)
    app_slug = request.GET.get("source_application")
    if app_slug and request.user.is_staff:
        tickets = tickets.filter(source_application__slug=app_slug)

    if search:
        tickets = tickets.filter(Q(subject__icontains=search) | Q(reference_number__icontains=search))

    # Sorting
    sort = request.GET.get("sort", "-created_at")
    valid_sorts = ["created_at", "-created_at", "priority", "-priority", "updated_at", "-updated_at"]
    if sort in valid_sorts:
        tickets = tickets.order_by(sort)

    paginator = Paginator(tickets, 50)
    page = paginator.get_page(request.GET.get("page"))

    filter_form = TicketFilterForm(request.GET)
    queues = Queue.objects.filter(is_active=True)
    applications = SourceApplication.objects.filter(is_active=True)
    saved_views = SavedView.objects.filter(Q(user=request.user) | Q(is_shared=True))

    from apps.accounts.models import User

    context = {
        "page": page,
        "filter_form": filter_form,
        "queues": queues,
        "applications": applications,
        "saved_views": saved_views,
        "agents": User.objects.filter(is_staff=True, is_active=True),
        "current_filters": request.GET.urlencode(),
        "last_event_id": cached_last_event or "",
    }
    return render_or_partial(request, "tickets/queue.html", "tickets/partials/ticket_list.html", context)


@login_required
def ticket_detail(request, reference):
    if not request.user.is_staff:
        return redirect("portal:ticket_detail", reference=reference)

    ticket = get_object_or_404(
        Ticket.objects.select_related("queue", "assigned_to", "source_application", "merged_into"),
        reference_number=reference,
    )

    if not can_view_ticket(request.user, ticket):
        raise Http404

    participants = (
        ticket.participants.select_related("user")
        .annotate(is_bouncing=Exists(BouncedAddress.objects.filter(email__iexact=OuterRef("email"))))
        .all()
    )

    comment_form = CommentForm()
    canned_responses = CannedResponse.objects.filter(Q(queue=ticket.queue) | Q(queue__isnull=True))

    # Collision detection: register viewer
    viewer_key = f"ticket_viewers:{ticket.pk}"
    viewers = cache.get(viewer_key, {})
    viewers[str(request.user.pk)] = {
        "email": request.user.email,
        "timestamp": timezone.now().isoformat(),
    }
    cache.set(viewer_key, viewers, timeout=30)

    other_viewers = [
        v["email"]
        for k, v in viewers.items()
        if k != str(request.user.pk) and (timezone.now() - timezone.datetime.fromisoformat(v["timestamp"])).seconds < 15
    ]

    from apps.accounts.models import User

    agents = User.objects.filter(is_staff=True, is_active=True)
    tags = Tag.objects.all()

    context = {
        "ticket": ticket,
        "participants": participants,
        "comment_form": comment_form,
        "canned_responses": canned_responses,
        "other_viewers": other_viewers,
        "agents": agents,
        "tags": tags,
    }
    context.update(_timeline_context(request, ticket))
    return render(request, "tickets/detail.html", context)


@login_required
def timeline_poll(request, pk):
    ticket = get_object_or_404(Ticket, pk=pk)
    if not can_view_ticket(request.user, ticket):
        raise Http404

    last_event = request.GET.get("last_event")
    cached = cache.get(f"ticket_activity:{ticket.pk}")
    if last_event and cached is not None and str(cached) == last_event:
        return HttpResponse(status=204)

    return render(request, "tickets/partials/timeline.html", _timeline_context(request, ticket))


@login_required
@require_POST
def claim_ticket(request, pk):
    if not request.user.is_staff:
        return HttpResponseForbidden("Staff only.")
    # select_for_update requires an explicit transaction (ATOMIC_REQUESTS is off),
    # and the lock has to span the assign or two agents can both win the row.
    with transaction.atomic():
        ticket = Ticket.objects.select_for_update(skip_locked=True).filter(pk=pk, assigned_to__isnull=True).first()
        if ticket:
            TicketService.assign_ticket(ticket, request.user, actor=request.user)
            messages.success(request, f"Claimed {ticket.reference_number}")
        else:
            messages.warning(request, "Ticket already claimed or not found.")

    if request.htmx:
        ticket = get_object_or_404(Ticket, pk=pk)
        return render(request, "tickets/partials/ticket_row.html", {"ticket": ticket})
    return redirect("tickets:queue")


@login_required
@require_POST
def assign_ticket(request, pk):
    if not request.user.is_staff:
        return HttpResponseForbidden("Staff only.")
    ticket = get_object_or_404(Ticket, pk=pk)
    from apps.accounts.models import User

    assignee_id = request.POST.get("assigned_to")
    if assignee_id:
        assignee = get_object_or_404(User, pk=assignee_id, is_staff=True)
        TicketService.assign_ticket(ticket, assignee, actor=request.user)
    else:
        TicketService.assign_ticket(ticket, None, actor=request.user)

    if request.htmx:
        return render(request, "tickets/partials/ticket_sidebar.html", _sidebar_context(ticket))
    return redirect("tickets:detail", reference=ticket.reference_number)


@login_required
@require_POST
def add_comment(request, pk):
    ticket = get_object_or_404(Ticket, pk=pk)

    if not can_view_ticket(request.user, ticket):
        raise Http404

    form = CommentForm(request.POST, request.FILES)

    if form.is_valid():
        # Non-staff can only add public comments
        comment_type = form.cleaned_data["comment_type"]
        if not request.user.is_staff:
            comment_type = Comment.CommentType.PUBLIC

        comment = TicketService.add_comment(
            ticket=ticket,
            body=form.cleaned_data["body"],
            author=request.user,
            comment_type=comment_type,
        )

        save_attachments(ticket, request.FILES.getlist("files"), comment=comment)

        if request.htmx:
            return render(request, "tickets/partials/timeline_item.html", {"item": comment, "ticket": ticket})
        return redirect("tickets:detail", reference=ticket.reference_number)

    if request.htmx:
        return render(request, "tickets/partials/comment_form.html", {"comment_form": form, "ticket": ticket})
    return redirect("tickets:detail", reference=ticket.reference_number)


@login_required
@require_POST
def change_status(request, pk):
    if not request.user.is_staff:
        return HttpResponseForbidden("Staff only.")
    ticket = get_object_or_404(Ticket, pk=pk)
    new_status = request.POST.get("status")
    if new_status in dict(Ticket.Status.choices):
        TicketService.change_status(ticket, new_status, actor=request.user)

    if request.htmx:
        return render(request, "tickets/partials/ticket_sidebar.html", _sidebar_context(ticket))
    return redirect("tickets:detail", reference=ticket.reference_number)


@login_required
@require_POST
def change_priority(request, pk):
    if not request.user.is_staff:
        return HttpResponseForbidden("Staff only.")
    ticket = get_object_or_404(Ticket, pk=pk)
    new_priority = request.POST.get("priority")
    if new_priority in dict(Ticket.Priority.choices):
        TicketService.change_priority(ticket, new_priority, actor=request.user)

    if request.htmx:
        return render(request, "tickets/partials/ticket_sidebar.html", _sidebar_context(ticket))
    return redirect("tickets:detail", reference=ticket.reference_number)


# Selection is per-page and the queue pages at 50, so BULK_MAX only ever trips on a
# hand-crafted POST. It bounds the blast radius of the delete action.

# No NEW (nothing moves a ticket back to unhandled in bulk) and no MERGED
# (that needs a target ticket, which is per-ticket by definition).
BULK_STATUSES = frozenset(
    {Ticket.Status.OPEN, Ticket.Status.PENDING, Ticket.Status.RESOLVED, Ticket.Status.CLOSED}
)


def _safe_next(request, target):
    """The caller-supplied return URL, or "" if it points off-site."""
    if target and url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return target
    return ""


def _redirect_next(request):
    """Back to the page the agent came from, filters and page intact."""
    return redirect(_safe_next(request, request.POST.get("next")) or reverse("tickets:queue"))


@login_required
@require_POST
def bulk_action(request):
    if not request.user.is_staff:
        return HttpResponseForbidden("Staff only.")

    try:
        ids = [int(v) for v in request.POST.getlist("ticket_ids")]
    except ValueError:
        messages.error(request, "Invalid ticket selection.")
        return _redirect_next(request)

    if not ids:
        messages.warning(request, "No tickets selected.")
        return _redirect_next(request)

    if len(ids) > BULK_MAX:
        messages.error(request, f"Select at most {BULK_MAX} tickets at a time ({len(ids)} selected).")
        return _redirect_next(request)

    action = request.POST.get("action")
    tickets = list(Ticket.objects.filter(pk__in=ids).select_related("assigned_to", "merged_into"))

    if action == "delete":
        return _bulk_delete(request, tickets)
    if action != "update":
        messages.error(request, "Unknown bulk action.")
        return _redirect_next(request)

    status = request.POST.get("status") or None
    if status and status not in BULK_STATUSES:
        messages.error(request, "Invalid status.")
        return _redirect_next(request)

    priority = request.POST.get("priority") or None
    if priority and priority not in dict(Ticket.Priority.choices):
        messages.error(request, "Invalid priority.")
        return _redirect_next(request)

    assignee = TicketService.UNCHANGED
    raw_assignee = request.POST.get("assigned_to") or ""
    if raw_assignee == "__none__":
        assignee = None
    elif raw_assignee:
        from apps.accounts.models import User

        assignee = get_object_or_404(User, pk=raw_assignee, is_staff=True, is_active=True)

    if not status and not priority and assignee is TicketService.UNCHANGED:
        messages.warning(request, "Pick at least one change to apply.")
        return _redirect_next(request)

    notify = request.POST.get("notify") == "on"
    changed = TicketService.bulk_update(
        tickets,
        actor=request.user,
        status=status,
        priority=priority,
        assignee=assignee,
        notify=notify,
    )

    if changed:
        suffix = "" if notify else " (customers not notified)"
        messages.success(request, f"Updated {changed} of {len(tickets)} selected ticket(s).{suffix}")
    else:
        messages.info(request, "Selected tickets already matched those values; nothing changed.")
    return _redirect_next(request)


def _bulk_delete(request, tickets):
    # Deleting destroys the audit trail along with the ticket, so it sits behind
    # superuser rather than plain staff.
    if not request.user.is_superuser:
        return HttpResponseForbidden("Deleting tickets requires a superuser.")

    if request.POST.get("confirm") != "DELETE":
        messages.error(request, "Deletion was not confirmed; nothing was changed.")
        return _redirect_next(request)

    references = [t.reference_number for t in tickets]
    count = TicketService.delete_tickets(tickets, actor=request.user)

    shown = ", ".join(references[:5])
    if count > 5:
        shown += f" and {count - 5} more"
    messages.success(request, f"Permanently deleted {count} ticket(s): {shown}")
    return _redirect_next(request)


@login_required
@require_POST
def add_tag(request, pk):
    if not request.user.is_staff:
        return HttpResponseForbidden("Staff only.")
    ticket = get_object_or_404(Ticket, pk=pk)
    tag_id = request.POST.get("tag_id")
    if tag_id:
        tag = get_object_or_404(Tag, pk=tag_id)
        ticket.tags.add(tag)
        TicketEvent.objects.create(
            ticket=ticket,
            event_type=TicketEvent.EventType.TAG_ADDED,
            actor=request.user,
            new_value={"tag": tag.name},
        )

    if request.htmx:
        return render(request, "tickets/partials/tag_list.html", {"ticket": ticket, "tags": Tag.objects.all()})
    return redirect("tickets:detail", reference=ticket.reference_number)


@login_required
@require_POST
def remove_tag(request, pk, tag_pk):
    if not request.user.is_staff:
        return HttpResponseForbidden("Staff only.")
    ticket = get_object_or_404(Ticket, pk=pk)
    tag = get_object_or_404(Tag, pk=tag_pk)
    ticket.tags.remove(tag)
    TicketEvent.objects.create(
        ticket=ticket,
        event_type=TicketEvent.EventType.TAG_REMOVED,
        actor=request.user,
        old_value={"tag": tag.name},
    )

    if request.htmx:
        return render(request, "tickets/partials/tag_list.html", {"ticket": ticket, "tags": Tag.objects.all()})
    return redirect("tickets:detail", reference=ticket.reference_number)


@login_required
def create_ticket(request):
    if request.method == "POST":
        form = TicketCreateForm(request.POST)
        if form.is_valid():
            # submitter_email is optional, so cleaned_data holds "" rather than no key --
            # .get()'s default never fired and a blank field produced a participant with
            # no address at all. Fall back on `or`, not on a dict default.
            submitter_email = form.cleaned_data.get("submitter_email") or request.user.email
            # Only claim the agent as the submitter when the ticket really is theirs.
            # Attaching them to a customer's address made the sole participant look like
            # staff, so the "created" notification found no customer to mail.
            filed_for_self = submitter_email.lower() == request.user.email.lower()
            ticket = TicketService.create_ticket(
                subject=form.cleaned_data["subject"],
                body=form.cleaned_data["body"],
                submitter_email=submitter_email,
                submitter_user=request.user if filed_for_self else None,
                actor=request.user,
                queue=form.cleaned_data.get("queue"),
                priority=form.cleaned_data.get("priority"),
                source_application=form.cleaned_data.get("source_application"),
            )
            messages.success(request, f"Ticket {ticket.reference_number} created.")
            return redirect("tickets:detail", reference=ticket.reference_number)
    else:
        form = TicketCreateForm()

    applications = SourceApplication.objects.filter(is_active=True)
    return render(request, "tickets/create.html", {"form": form, "applications": applications})


def _can_edit_ticket(user, ticket):
    """Check if user is staff or the ticket submitter."""
    if user.is_staff:
        return True
    submitter = ticket.participants.filter(role=TicketParticipant.Role.SUBMITTER, user=user).first()
    return submitter is not None


@login_required
def edit_ticket(request, pk):
    ticket = get_object_or_404(
        Ticket.objects.select_related("queue", "source_application"),
        pk=pk,
    )

    if not _can_edit_ticket(request.user, ticket):
        messages.error(request, "You don't have permission to edit this ticket.")
        return redirect("tickets:detail", reference=ticket.reference_number)

    if request.method == "POST":
        form = TicketEditForm(request.POST)
        if form.is_valid():
            fields = {"subject": form.cleaned_data["subject"], "body": form.cleaned_data["body"]}
            # Routing is staff-only: a submitter may fix their wording, not move the
            # ticket into another queue or another product's application.
            if request.user.is_staff:
                fields["queue"] = form.cleaned_data.get("queue")
                fields["source_application"] = form.cleaned_data.get("source_application")
            TicketService.update_ticket(ticket, actor=request.user, **fields)
            messages.success(request, "Ticket updated.")
            return redirect("tickets:detail", reference=ticket.reference_number)
    else:
        form = TicketEditForm(
            initial={
                "subject": ticket.subject,
                "body": ticket.body,
                "queue": ticket.queue_id,
                "source_application": ticket.source_application_id,
            }
        )

    applications = SourceApplication.objects.filter(is_active=True)
    return render(
        request,
        "tickets/edit.html",
        {
            "form": form,
            "ticket": ticket,
            "applications": applications,
        },
    )


@login_required
@require_POST
def change_source_application(request, pk):
    if not request.user.is_staff:
        return HttpResponseForbidden("Staff only.")
    ticket = get_object_or_404(Ticket, pk=pk)
    app_id = request.POST.get("source_application")
    if app_id:
        new_app = get_object_or_404(SourceApplication, pk=app_id, is_active=True)
    else:
        new_app = None

    TicketService.change_source_application(ticket, new_app, actor=request.user)

    if request.htmx:
        return render(request, "tickets/partials/ticket_sidebar.html", _sidebar_context(ticket))
    return redirect("tickets:detail", reference=ticket.reference_number)


@login_required
@require_POST
def merge_ticket(request, pk):
    if not request.user.is_staff:
        return HttpResponseForbidden("Staff only.")
    source = get_object_or_404(Ticket, pk=pk)
    target_ref = request.POST.get("target_reference")
    target = get_object_or_404(Ticket, reference_number=target_ref)

    if source.pk == target.pk:
        messages.error(request, "Cannot merge a ticket into itself.")
        return redirect("tickets:detail", reference=source.reference_number)

    TicketService.merge_tickets(source, target, actor=request.user)
    messages.success(request, f"Merged {source.reference_number} into {target.reference_number}")
    return redirect("tickets:detail", reference=target.reference_number)


@login_required
def search_tickets(request):
    q = request.GET.get("q", "")
    tickets = Ticket.objects.filter(Q(reference_number__icontains=q) | Q(subject__icontains=q)).exclude(
        status=Ticket.Status.MERGED
    )
    # The merge modal passes the ticket being merged; offering it as its own target
    # is a dead end the view only rejects after a round trip.
    if (exclude := request.GET.get("exclude", "")).isdigit():
        tickets = tickets.exclude(pk=exclude)
    if not request.user.is_staff:
        tickets = tickets.filter(participants__user=request.user)
    tickets = tickets[:10]
    return render(request, "tickets/partials/search_results.html", {"tickets": tickets})


@login_required
def heartbeat(request, pk):
    ticket = get_object_or_404(Ticket, pk=pk)
    if not can_view_ticket(request.user, ticket):
        raise Http404
    viewer_key = f"ticket_viewers:{ticket.pk}"
    viewers = cache.get(viewer_key, {})
    viewers[str(request.user.pk)] = {
        "email": request.user.email,
        "timestamp": timezone.now().isoformat(),
    }
    cache.set(viewer_key, viewers, timeout=30)

    other_viewers = [
        v["email"]
        for k, v in viewers.items()
        if k != str(request.user.pk) and (timezone.now() - timezone.datetime.fromisoformat(v["timestamp"])).seconds < 15
    ]
    return render(request, "tickets/partials/collision_banner.html", {"other_viewers": other_viewers})


@login_required
def canned_response_search(request):
    if not request.user.is_staff:
        return HttpResponseForbidden("Staff only.")
    q = request.GET.get("q", "")
    queue_id = request.GET.get("queue_id")
    responses = CannedResponse.objects.filter(Q(title__icontains=q) | Q(shortcut__icontains=q))
    if queue_id:
        responses = responses.filter(Q(queue_id=queue_id) | Q(queue__isnull=True))
    else:
        responses = responses.filter(queue__isnull=True)
    return render(request, "tickets/partials/canned_results.html", {"canned_responses": responses[:10]})


@login_required
def canned_response_list(request):
    if not request.user.is_staff:
        return HttpResponseForbidden("Staff only.")
    responses = CannedResponse.objects.select_related("queue").order_by("-usage_count", "title")
    return render(request, "tickets/canned_list.html", {"canned_responses": responses})


@login_required
def canned_response_form(request, pk=None):
    """Create when pk is None, edit otherwise -- the form is identical either way."""
    if not request.user.is_staff:
        return HttpResponseForbidden("Staff only.")
    canned = get_object_or_404(CannedResponse, pk=pk) if pk else None

    if request.method == "POST":
        form = CannedResponseForm(request.POST, instance=canned)
        if form.is_valid():
            saved = form.save(commit=False)
            # Keep the original author on an edit; only a new response has no one.
            if saved.created_by is None:
                saved.created_by = request.user
            saved.save()
            messages.success(request, f'Canned response "{saved.title}" saved.')
            return _redirect_next(request)
    else:
        form = CannedResponseForm(instance=canned)

    back = _safe_next(request, request.GET.get("next")) or reverse("tickets:canned_list")
    return render(request, "tickets/canned_form.html", {"form": form, "next": back, "canned": canned})


@login_required
def canned_response_delete(request, pk):
    if not request.user.is_staff:
        return HttpResponseForbidden("Staff only.")
    canned = get_object_or_404(CannedResponse, pk=pk)

    if request.method == "POST":
        title = canned.title
        canned.delete()
        messages.success(request, f'Canned response "{title}" deleted.')
        return redirect("tickets:canned_list")

    return render(request, "tickets/canned_confirm_delete.html", {"canned": canned})


@login_required
@require_POST
def use_canned_response(request, pk):
    if not request.user.is_staff:
        return HttpResponseForbidden("Staff only.")
    canned = get_object_or_404(CannedResponse, pk=pk)
    canned.usage_count += 1
    canned.save(update_fields=["usage_count"])
    return JsonResponse({"body": canned.body})


@login_required
@require_POST
def save_view(request):
    if not request.user.is_staff:
        return HttpResponseForbidden("Staff only.")
    name = request.POST.get("name", "Untitled View")
    is_shared = request.POST.get("is_shared") == "on"
    filters = {}
    for key in ["status", "priority", "queue", "assigned_to", "source_application", "q", "sort"]:
        val = request.POST.get(key)
        if val:
            filters[key] = val

    SavedView.objects.create(
        name=name,
        user=request.user,
        is_shared=is_shared,
        filters=filters,
    )
    messages.success(request, f'View "{name}" saved.')
    return redirect("tickets:queue")


@login_required
@require_POST
def delete_saved_view(request, pk):
    if not request.user.is_staff:
        return HttpResponseForbidden("Staff only.")
    view = get_object_or_404(SavedView, pk=pk)
    if view.user == request.user or request.user.is_staff:
        view.delete()
    return redirect("tickets:queue")


@login_required
@require_POST
def resend_magic_link(request, pk):
    if not request.user.is_staff:
        return HttpResponseForbidden("Staff only.")
    ticket = get_object_or_404(Ticket, pk=pk)
    from apps.inbound_email.tasks import customer_recipients, send_ticket_notification

    # The notification mails every customer participant, not just the submitter --
    # name them all so the confirmation matches what actually goes out.
    recipients = customer_recipients(ticket)
    if recipients:
        send_ticket_notification.defer(ticket_id=ticket.pk, notification_type="created")
        messages.success(request, f"Access link resent to {', '.join(recipients)}.")
    else:
        messages.error(request, "No customer recipients found for this ticket.")
    return redirect("tickets:detail", reference=ticket.reference_number)


@login_required
def ticket_activity(request):
    if not request.user.is_staff:
        return HttpResponseForbidden("Staff only.")

    try:
        after = int(request.GET.get("after") or 0)
    except ValueError:
        after = 0

    cached_last_event = cache.get("tickets:last_event_id")
    if cached_last_event is not None and str(cached_last_event) == str(after):
        return JsonResponse({"latest": after})

    events = TicketEvent.objects.filter(pk__gt=after)
    new_tickets = events.filter(event_type=TicketEvent.EventType.CREATED).count()
    customer_replies = (
        events.filter(event_type=TicketEvent.EventType.COMMENT_ADDED)
        .filter(Q(actor__isnull=True) | Q(actor__is_staff=False))
        .count()
    )
    latest = events.aggregate(latest=Max("pk"))["latest"] or after

    return JsonResponse({"latest": latest, "new_tickets": new_tickets, "customer_replies": customer_replies})
