"""No template may open a `{# #}` comment it does not close on the same line.

Django lexes `{# #}` with a regex that has no DOTALL flag, so a comment spanning
two lines never matches and is emitted as literal text -- into the page, and via
strip_tags() into the plain-text part of customer email. It has leaked twice
(4a5a45c, and again into the reply box). `{% comment %}` is the multi-line form.
"""

from pathlib import Path

from django.conf import settings

ROOT = Path(settings.BASE_DIR)


def test_no_template_opens_a_comment_it_does_not_close_on_the_same_line():
    offenders = [
        f"{path.relative_to(ROOT)}:{number}"
        for directory in ("templates", "apps")
        for path in sorted((ROOT / directory).rglob("*.html"))
        for number, line in enumerate(path.read_text().splitlines(), 1)
        if line.count("{#") > line.count("#}")
    ]

    assert not offenders, "use {% comment %} for multi-line comments: " + ", ".join(offenders)
