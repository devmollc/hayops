"""The merge modal's search has to hand its result to the field that does the merging."""

import pytest
from django.urls import reverse

from apps.tickets.models import Ticket

pytestmark = pytest.mark.django_db


def test_a_result_fills_the_target_field_instead_of_navigating_away(client, staff_user, source_app):
    other = Ticket.objects.create(subject="Widget still broken", body="...", source_application=source_app)
    client.force_login(staff_user)

    resp = client.get(reverse("tickets:search_tickets"), {"q": "Widget"}, HTTP_HOST="testserver")

    body = resp.content.decode()
    # staff-ui.js reads this attribute and fills #merge-target; the handler
    # moved out of the markup when the CSP stopped allowing inline ones.
    assert f'data-merge-reference="{other.reference_number}"' in body
    assert f'href="/tickets/{other.reference_number}/"' not in body


def test_the_ticket_being_merged_is_not_offered_as_its_own_target(client, staff_user, ticket, source_app):
    other = Ticket.objects.create(subject="Widget still broken", body="...", source_application=source_app)
    client.force_login(staff_user)

    resp = client.get(reverse("tickets:search_tickets"), {"q": "Widget", "exclude": ticket.pk}, HTTP_HOST="testserver")

    body = resp.content.decode()
    assert other.reference_number in body
    assert ticket.reference_number not in body
