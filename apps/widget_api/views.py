import hashlib
import json
from urllib.parse import urlparse

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.core.models import SourceApplication
from apps.core.ratelimit import rate_limited
from apps.tickets.models import Attachment, Comment, Ticket, TicketParticipant
from apps.tickets.services import TicketService, customer_scope

from .authentication import AppKeyAuthentication, HMACAuthentication
from .serializers import (
    WidgetCommentSerializer,
    WidgetTicketCreateSerializer,
    WidgetTicketDetailSerializer,
    WidgetTicketListSerializer,
)

# Anonymous tickets per submitter address per hour. The app key is public, and
# every ticket mails a confirmation to submitter_email, so without this cap the
# widget is a relay for mailing arbitrary third parties.
ANON_EMAIL_LIMIT = 5
ANON_EMAIL_WINDOW = 3600  # seconds

# Raw "context" form field is capped before JSON parsing; oversized or
# malformed payloads are dropped rather than truncated (truncated JSON
# rarely still parses).
MAX_CONTEXT_BYTES = 8 * 1024


def _is_safe_url(value):
    """page_url / referrer get rendered as hrefs in the agent sidebar —
    reject non-http(s) schemes (e.g. "javascript:...") to avoid stored XSS."""
    return isinstance(value, str) and urlparse(value).scheme in ("http", "https")


class WidgetTicketViewSet(viewsets.ViewSet):
    authentication_classes = [HMACAuthentication, AppKeyAuthentication]
    parser_classes = [MultiPartParser, JSONParser]
    throttle_scope = "widget"

    def _get_user_tickets(self, request):
        """This user's tickets, scoped to the application whose key signed the request.

        Scoping matters beyond the list: retrieve and reply take a ticket id, so
        without it a user authenticated through one product's widget could read
        and reply to their ticket on another product by guessing its id.
        """
        user = request.user
        if user.is_anonymous:
            return Ticket.objects.none()
        ticket_ids = TicketParticipant.objects.filter(
            email=user.email
        ).values_list("ticket_id", flat=True)
        return Ticket.objects.filter(
            customer_scope("source_application", request.auth),
            id__in=ticket_ids,
        )

    def list(self, request):
        tickets = self._get_user_tickets(request).order_by("-created_at")
        serializer = WidgetTicketListSerializer(tickets, many=True)
        return Response(serializer.data)

    def retrieve(self, request, pk=None):
        tickets = self._get_user_tickets(request)
        try:
            ticket = tickets.prefetch_related("comments").get(pk=pk)
        except Ticket.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        serializer = WidgetTicketDetailSerializer(ticket)
        return Response(serializer.data)

    def create(self, request):
        serializer = WidgetTicketCreateSerializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data
        source_application = request.auth

        # Build custom_fields from browser metadata
        custom_fields = {}
        if data.get("page_url") and _is_safe_url(data["page_url"]):
            custom_fields["page_url"] = data["page_url"]
        if data.get("user_agent"):
            custom_fields["user_agent"] = data["user_agent"]
        context_raw = data.get("context")
        if context_raw and len(context_raw.encode("utf-8")) <= MAX_CONTEXT_BYTES:
            try:
                parsed_context = json.loads(context_raw)
            except (TypeError, ValueError):
                parsed_context = None
            if isinstance(parsed_context, dict) and "referrer" in parsed_context:
                if not _is_safe_url(parsed_context["referrer"]):
                    parsed_context = {k: v for k, v in parsed_context.items() if k != "referrer"}
            if parsed_context is not None:
                custom_fields["widget_context"] = parsed_context

        # Determine submitter user and email
        if request.user.is_anonymous:
            submitter_email = data["submitter_email"]
            email_key = hashlib.sha256(submitter_email.strip().lower().encode()).hexdigest()[:32]
            if rate_limited(f"widget_submit_email:{email_key}", ANON_EMAIL_LIMIT, ANON_EMAIL_WINDOW):
                return Response(status=status.HTTP_429_TOO_MANY_REQUESTS)
            submitter_name = data.get("submitter_name", "")
            submitter_user, created = User.objects.get_or_create(
                email=submitter_email,
                defaults={"is_staff": False},
            )
            # Only on creation. This endpoint takes an anonymous, app-key-only
            # submission, so writing the posted name onto an account that
            # already exists let anyone rename any user, staff included.
            if submitter_name and created:
                submitter_user.first_name = submitter_name
                submitter_user.save(update_fields=["first_name"])
            elif submitter_name:
                # Not thrown away -- the agent still wants to see what the
                # submitter called themselves, it just is not identity.
                custom_fields["submitter_name"] = submitter_name
        else:
            submitter_email = request.user.email
            submitter_user = request.user

        ticket = TicketService.create_ticket(
            subject=data["subject"],
            body=data["body"],
            submitter_email=submitter_email,
            submitter_user=submitter_user,
            source_application=source_application,
            custom_fields=custom_fields,
        )

        # Handle screenshot attachment
        screenshot = data.get("screenshot")
        if screenshot:
            Attachment.objects.create(
                ticket=ticket,
                file=screenshot,
                filename=screenshot.name or "screenshot.png",
                content_type=screenshot.content_type or "image/png",
                size_bytes=screenshot.size,
            )

        return Response(
            WidgetTicketDetailSerializer(ticket).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["post"])
    def reply(self, request, pk=None):
        tickets = self._get_user_tickets(request)
        try:
            ticket = tickets.get(pk=pk)
        except Ticket.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        serializer = WidgetCommentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        comment = TicketService.add_comment(
            ticket=ticket,
            body=serializer.validated_data["body"],
            author=request.user,
            author_email=request.user.email,
            comment_type=Comment.CommentType.PUBLIC,
        )

        return Response(
            WidgetCommentSerializer(comment).data,
            status=status.HTTP_201_CREATED,
        )


class WidgetConfigView(APIView):
    """Public branding config for the widget, keyed by app key alone (no HMAC)."""

    throttle_scope = "widget"

    def get(self, request):
        api_key = request.META.get("HTTP_X_APP_KEY") or request.query_params.get("key")
        if not api_key:
            return Response(status=status.HTTP_403_FORBIDDEN)

        try:
            app = SourceApplication.objects.get(api_key=api_key, is_active=True)
        except SourceApplication.DoesNotExist:
            return Response(status=status.HTTP_403_FORBIDDEN)

        logo_url = request.build_absolute_uri(app.logo.url) if app.logo else None
        return Response(
            {
                "product_name": app.branding_name,
                "primary_color": app.primary_color,
                "logo_url": logo_url,
            }
        )
