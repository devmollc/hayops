import pytest

from apps.inbound_email.email_parser import extract_reply_body, parse_email


def _raw(body, content_type="text/plain"):
    return (
        f"From: customer@example.com\r\nTo: support@mail.appsfol.io\r\n"
        f"Subject: Re: [HD-0001] Widget\r\nMessage-ID: <x@test>\r\n"
        f"Content-Type: {content_type}; charset=utf-8\r\n\r\n{body}"
    ).encode()


def test_gmail_quote_attribution_is_stripped():
    body = extract_reply_body(
        "Thanks, that worked.\n\nOn Mon, Jan 6, 2026 at 9:14 AM Support <s@x.com> wrote:\n> Have you tried it?\n"
    )
    assert body == "Thanks, that worked."


def test_outlook_original_message_block_is_stripped():
    body = extract_reply_body(
        "Still broken.\n\n-----Original Message-----\nFrom: Support\nSent: Monday\nAny luck?\n"
    )
    assert body == "Still broken."


def test_outlook_underscore_separator_is_stripped():
    body = extract_reply_body(
        "Confirmed fixed.\n\n________________________________\nFrom: Support <s@x.com>\nSent: Monday\n"
    )
    assert body == "Confirmed fixed."


def test_indented_quote_lines_are_dropped():
    assert extract_reply_body("My answer.\n  > the original question\n") == "My answer."


def test_signature_delimiter_still_truncates():
    assert extract_reply_body("Here you go.\n--\nJo, Acme Corp\n") == "Here you go."


def test_html_only_reply_keeps_just_the_new_text():
    """HTML senders used to store the raw markup of the entire quoted thread."""
    html = (
        "<div dir='ltr'>That fixed it, thank you!</div>"
        "<div class='gmail_quote'><div>On Mon, Jan 6, 2026 Support wrote:</div>"
        "<blockquote>Please try clearing your cache.</blockquote></div>"
    )
    parsed = parse_email(_raw(html, content_type="text/html"))

    assert "That fixed it, thank you!" in parsed["body"]
    assert "Please try clearing your cache." not in parsed["body"]
    assert "<div" not in parsed["body"]


def test_html_entities_are_decoded():
    parsed = parse_email(_raw("<p>Tom &amp; Jerry &lt;3</p>", content_type="text/html"))
    assert parsed["body"] == "Tom & Jerry <3"


# ── finding 12: who really sent this ─────────────────────────────────────────


def _with_verdict(header_block):
    return parse_email(
        f"From: cfo@victim.com\r\n"
        f"To: support@mail.appsfol.io\r\n"
        f"Subject: Re: [HD-0042] Invoice\r\n"
        f"{header_block}"
        f"\r\nPay this instead.\r\n".encode()
    )


def test_dmarc_pass_authenticates_the_from_header():
    assert _with_verdict("X-SES-DMARC-Verdict: PASS\r\n")["from_authenticated"] is True


@pytest.mark.parametrize(
    "header_block",
    [
        "X-SES-DMARC-Verdict: FAIL\r\n",
        # No DMARC policy published, so alignment is unprovable, not proven.
        "X-SES-DMARC-Verdict: GRAY\r\n",
        "X-SES-DMARC-Verdict: PROCESSING_FAILED\r\n",
        # SPF covers the envelope sender and DKIM covers whoever signed; an
        # attacker passes both from their own domain while forging From:.
        "X-SES-SPF-Verdict: PASS\r\nX-SES-DKIM-Verdict: PASS\r\n",
        "",
    ],
)
def test_anything_short_of_dmarc_pass_is_unauthenticated(header_block):
    assert _with_verdict(header_block)["from_authenticated"] is False
