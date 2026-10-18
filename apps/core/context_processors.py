from django.conf import settings


def source_application(request):
    app = getattr(request, "source_application", None)
    context = {"current_application": app}
    # Staff host (app is None) stays untracked.
    if app and app.umami_website_id and settings.UMAMI_HOST:
        context["UMAMI"] = {
            "HOST": settings.UMAMI_HOST,
            "WEBSITE_ID": app.umami_website_id,
            "DOMAINS": app.portal_domain,
        }
    return context
