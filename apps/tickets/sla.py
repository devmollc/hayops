from datetime import timedelta

from django.utils import timezone

from .models import SLAPolicy, Ticket, TicketEvent


class SLAService:
    @staticmethod
    def apply_sla(ticket):
        policy = SLAService._find_matching_policy(ticket)
        if not policy:
            return

        first_response_minutes = policy.first_response_minutes
        resolution_minutes = policy.resolution_minutes

        # Check for priority overrides
        overrides = policy.priority_overrides.get(ticket.priority, {})
        if overrides:
            first_response_minutes = overrides.get("first_response_minutes", first_response_minutes)
            resolution_minutes = overrides.get("resolution_minutes", resolution_minutes)

        now = timezone.now()
        ticket.first_response_due_at = now + timedelta(minutes=first_response_minutes)
        ticket.resolution_due_at = now + timedelta(minutes=resolution_minutes)
        ticket.save(update_fields=["first_response_due_at", "resolution_due_at"])

    @staticmethod
    def _find_matching_policy(ticket):
        # Most specific match wins: queue+source > queue > source > global
        if ticket.queue and ticket.source_application:
            policy = SLAPolicy.objects.filter(
                queue=ticket.queue, source_application=ticket.source_application
            ).first()
            if policy:
                return policy

        if ticket.queue:
            policy = SLAPolicy.objects.filter(queue=ticket.queue, source_application__isnull=True).first()
            if policy:
                return policy

        if ticket.source_application:
            policy = SLAPolicy.objects.filter(queue__isnull=True, source_application=ticket.source_application).first()
            if policy:
                return policy

        return SLAPolicy.objects.filter(queue__isnull=True, source_application__isnull=True).first()

    @staticmethod
    def check_sla_breaches():
        now = timezone.now()
        breached_tickets = Ticket.objects.filter(
            status__in=[Ticket.Status.NEW, Ticket.Status.OPEN, Ticket.Status.PENDING],
        ).filter(
            breached_sla_filter(now)
        )

        for ticket in breached_tickets:
            breach_details = {}
            if ticket.first_response_due_at and not ticket.first_responded_at and ticket.first_response_due_at < now:
                breach_details["first_response"] = True
            if ticket.resolution_due_at and not ticket.resolved_at and ticket.resolution_due_at < now:
                breach_details["resolution"] = True

            if breach_details:
                # Only create event if one doesn't already exist recently
                recent = TicketEvent.objects.filter(
                    ticket=ticket,
                    event_type=TicketEvent.EventType.SLA_BREACHED,
                    created_at__gte=now - timedelta(minutes=10),
                ).exists()
                if not recent:
                    TicketEvent.objects.create(
                        ticket=ticket,
                        event_type=TicketEvent.EventType.SLA_BREACHED,
                        new_value=breach_details,
                    )


def breached_sla_filter(now):
    from django.db.models import Q

    return (
        Q(first_response_due_at__lt=now, first_responded_at__isnull=True)
        | Q(resolution_due_at__lt=now, resolved_at__isnull=True)
    )
