"""Finding 11: /api/ses/inbound/ accepted any caller whose SigningCertURL merely
started with https://sns. and contained .amazonaws.com, then fetched their
SubscribeURL."""

import json

import pytest
from django.urls import reverse

TOPIC = "arn:aws:sns:us-east-1:123456789012:helpdesk-inbound"
GOOD_CERT = "https://sns.us-east-1.amazonaws.com/SimpleNotificationService-abc123.pem"

pytestmark = pytest.mark.django_db


@pytest.fixture
def configured(settings):
    settings.SES_INBOUND_TOPIC_ARN = TOPIC
    settings.SES_INBOUND_BUCKET = "helpdesk-inbound-mail"
    return settings


def _post(client, body):
    return client.post(
        reverse("inbound_email:ses_inbound"),
        data=json.dumps(body),
        content_type="application/json",
    )


def _notification(**overrides):
    body = {
        "Type": "Notification",
        "TopicArn": TOPIC,
        "SigningCertURL": GOOD_CERT,
        "Signature": "irrelevant-for-these-tests",
        "Message": json.dumps(
            {"receipt": {"action": {"bucketName": "helpdesk-inbound-mail", "objectKey": "incoming/x"}}}
        ),
    }
    body.update(overrides)
    return body


@pytest.mark.parametrize(
    "cert_url",
    [
        "https://sns.evil.tld/x.amazonaws.com",  # the exact string the old check let through
        "https://sns.us-east-1.amazonaws.com.evil.tld/SimpleNotificationService-a.pem",
        "http://sns.us-east-1.amazonaws.com/SimpleNotificationService-a.pem",  # not https
        "",
    ],
)
def test_untrusted_certificate_url_is_rejected(client, configured, cert_url):
    assert _post(client, _notification(SigningCertURL=cert_url)).status_code == 403


def test_a_well_formed_cert_url_still_needs_a_valid_signature(client, configured, monkeypatch):
    """Shape alone is not enough -- a failed crypto check has to reach the response.

    django-ses owns the SHA1withRSA verification itself (and tests it); what is
    this view's job is refusing the message when that verification says no.
    """
    calls = []
    monkeypatch.setattr(
        "apps.inbound_email.views.verify_event_message",
        lambda body: calls.append(body) or False,
    )
    assert _post(client, _notification()).status_code == 403
    assert len(calls) == 1


def test_unset_topic_arn_fails_closed(client, settings):
    settings.SES_INBOUND_TOPIC_ARN = ""
    assert _post(client, _notification()).status_code == 403


def test_wrong_topic_is_rejected(client, configured):
    assert _post(client, _notification(TopicArn="arn:aws:sns:us-east-1:1:someone-elses")).status_code == 403


def test_subscription_confirmation_does_not_fetch_an_attacker_url(client, configured, monkeypatch):
    fetched = []
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: fetched.append(a))
    # Verification is what stops this one; the SubscribeURL host check is the backstop.
    response = _post(
        client,
        {
            "Type": "SubscriptionConfirmation",
            "TopicArn": TOPIC,
            "SigningCertURL": GOOD_CERT,
            "Signature": "forged",
            "SubscribeURL": "http://169.254.169.254/latest/meta-data/",
        },
    )
    assert response.status_code == 403
    assert fetched == []


def test_subscribe_url_host_is_checked_even_past_verification(client, configured, monkeypatch):
    fetched = []
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: fetched.append(a))
    monkeypatch.setattr("apps.inbound_email.views.verify_event_message", lambda body: True)

    response = _post(
        client,
        {
            "Type": "SubscriptionConfirmation",
            "TopicArn": TOPIC,
            "SigningCertURL": GOOD_CERT,
            "Signature": "x",
            "SubscribeURL": "http://169.254.169.254/latest/meta-data/",
        },
    )
    assert response.status_code == 403
    assert fetched == []


def test_the_header_cannot_override_the_signed_type(client, configured, monkeypatch):
    """Type is inside the signature; x-amz-sns-message-type is not."""
    monkeypatch.setattr("apps.inbound_email.views.verify_event_message", lambda body: True)
    deferred = []
    monkeypatch.setattr(
        "apps.inbound_email.views.process_ses_inbound",
        type("T", (), {"defer": staticmethod(lambda **kw: deferred.append(kw))})(),
    )

    response = client.post(
        reverse("inbound_email:ses_inbound"),
        data=json.dumps(_notification()),
        content_type="application/json",
        headers={"x-amz-sns-message-type": "SubscriptionConfirmation"},
    )
    assert response.status_code == 200
    assert deferred == [{"bucket": "helpdesk-inbound-mail", "key": "incoming/x"}]
