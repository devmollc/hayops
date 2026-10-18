from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django_ses.views import SESEventWebhookView

from apps.core import views as core_views
from apps.widget_api.widget_js import widget_js, widget_vendor_js
from config.health import health_check

admin.site.site_header = "HayOps Administration"
admin.site.site_title = "HayOps Admin"
admin.site.index_title = "Dashboard"

urlpatterns = [
    # SNS posts SES bounce/complaint events here. Unauthenticated by
    # necessity — django-ses verifies the SNS message signature instead,
    # and confirms the subscription on first POST.
    path("ses/events/", SESEventWebhookView.as_view(), name="ses_event_webhook"),
    path("ht/", health_check),
    path("widget/v<str:version>/helpdesk-widget.js", widget_js, name="widget-js"),
    path("widget/v<str:version>/vendor/<str:name>", widget_vendor_js, name="widget-vendor-js"),
    path("admin/", admin.site.urls),
    path("help/", core_views.help_page, name="help"),
    path("accounts/", include("apps.accounts.urls")),
    path("tickets/", include("apps.tickets.urls")),
    path("kb/", include("apps.kb.urls")),
    path("", include("apps.portal.urls")),
    path("reports/", include("apps.reporting.urls")),
    path("webhooks/", include("apps.webhooks.urls")),
    path("testbench/", include("apps.testbench.urls")),
    path("api/widget/", include("apps.widget_api.urls")),
    path("api/testbench/", include("apps.testbench.api_urls")),
    path("api/ses/", include("apps.inbound_email.urls")),
    path("", include("apps.tickets.csat_urls")),
    path("", include("apps.tickets.magic_link_urls")),
    path("", include("apps.tickets.urls_root")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    try:
        import debug_toolbar

        urlpatterns += [path("__debug__/", include(debug_toolbar.urls))]
    except ImportError:
        pass
