import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

django_application = get_asgi_application()

# Imported once Django is set up, because the MCP server uses the ORM.
from apps.testbench.mcp_server import http_app as mcp_application  # noqa: E402


async def application(scope, receive, send):
    """Django for every path except /mcp, which is the MCP SDK's own Starlette app.

    The SDK only speaks ASGI, which is why the helpdesk runs under uvicorn workers.
    Lifespan events go to the MCP app, which starts its session manager on them; Django
    has no lifespan handling of its own.
    """
    path = scope.get("path", "")
    if scope["type"] == "lifespan" or path == "/mcp" or path.startswith("/mcp/"):
        await mcp_application(scope, receive, send)
    else:
        await django_application(scope, receive, send)
