"""Recommendation 15: helpdesk sent no Content-Security-Policy."""

import re
from pathlib import Path

import pytest
from django.http import HttpResponse

from apps.core.csp import ContentSecurityPolicyMiddleware, build_policy

INLINE_HANDLER = re.compile(r"(^|\s)on(click|change|submit|input|load|error|key\w+|focus|blur)=")


def _directives(header):
    return dict(part.strip().split(" ", 1) for part in header.split(";"))


@pytest.mark.django_db
def test_every_response_carries_the_policy(client):
    assert "default-src 'self'" in client.get("/ht/")["Content-Security-Policy"]


def test_inline_script_is_not_allowed():
    """The whole point: no 'unsafe-inline' and no nonce for script-src."""
    script_src = _directives(build_policy())["script-src"]
    assert "'unsafe-inline'" not in script_src
    assert "nonce-" not in script_src


def test_no_template_ships_an_inline_script_or_handler():
    """A nonce-free policy only holds if nothing inline is left to run."""
    root = Path(__file__).resolve().parents[3]
    offenders = []
    for template in [*root.glob("templates/**/*.html"), *root.glob("apps/**/templates/**/*.html")]:
        text = template.read_text()
        offenders += [
            f"{template.relative_to(root)}: inline <script>"
            for tag in re.findall(r"<script\b[^>]*>", text)
            if "src=" not in tag
        ]
        if INLINE_HANDLER.search(text):
            offenders.append(f"{template.relative_to(root)}: inline event handler")
    assert not offenders, offenders


def test_the_dangerous_fetch_directives_are_locked_down():
    directives = _directives(build_policy())
    assert directives["object-src"] == "'none'"
    assert directives["frame-ancestors"] == "'none'"
    assert directives["base-uri"] == "'self'"
    assert directives["form-action"] == "'self'"


def test_the_public_media_host_is_allowed_for_images(settings):
    settings.R2_PUBLIC_HOST = "media.example.com"
    assert "https://media.example.com" in _directives(build_policy())["img-src"]


def test_the_r2_endpoint_is_allowed_when_no_public_host_is_set(settings):
    """django-storages builds logo URLs against the endpoint when custom_domain
    is empty, which is how the test environment is configured."""
    settings.R2_PUBLIC_HOST = ""
    settings.R2_ENDPOINT_URL = "https://abc123.r2.cloudflarestorage.com"
    assert "https://abc123.r2.cloudflarestorage.com" in _directives(build_policy())["img-src"]


def test_report_only_mode_swaps_the_header(settings):
    settings.CSP_REPORT_ONLY = True
    middleware = ContentSecurityPolicyMiddleware(lambda request: HttpResponse())
    assert middleware.header == "Content-Security-Policy-Report-Only"


def test_the_umami_host_is_allowed_to_load_and_report(settings):
    settings.UMAMI_HOST = "https://pulse.devmo.llc"
    directives = _directives(build_policy())
    assert "https://pulse.devmo.llc" in directives["script-src"]
    assert "https://pulse.devmo.llc" in directives["connect-src"]


def test_no_umami_host_leaves_connect_src_self_only(settings):
    settings.UMAMI_HOST = ""
    assert _directives(build_policy())["connect-src"] == "'self'"
