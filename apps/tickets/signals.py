from django.core.cache import cache
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Ticket, TicketEvent


@receiver(post_save, sender=Ticket)
def apply_sla_on_create(sender, instance, created, **kwargs):
    if created:
        from apps.tickets.sla import SLAService

        SLAService.apply_sla(instance)


@receiver(post_save, sender=TicketEvent)
def trigger_webhooks(sender, instance, created, **kwargs):
    if created:
        cache.set("tickets:last_event_id", instance.pk, None)
        cache.set(f"ticket_activity:{instance.ticket_id}", instance.pk, None)

        from apps.webhooks.tasks import dispatch_webhook

        try:
            dispatch_webhook.defer(event_id=instance.pk)
        except Exception:
            pass  # Don't break ticket operations if webhook dispatch fails
