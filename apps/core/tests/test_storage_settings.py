"""Guard the storage split: R2 vs AWS namespaces, and public vs private buckets.

Two separate hazards are covered here.

1. django-ses falls back to ``settings.AWS_ACCESS_KEY_ID`` and django-storages
   falls back to the ``AWS_ACCESS_KEY_ID`` env var, so an R2 key parked on either
   name makes one service authenticate with the other's credentials. R2 config
   must travel through STORAGES OPTIONS only.
2. Branding assets are world-readable; ticket attachments must not be. They live
   in different buckets with different URL-signing behaviour.
"""

from config.settings import base

# Names django-ses / django-storages resolve implicitly. Assigning R2 values to
# any of them reintroduces the collision this split exists to prevent.
AWS_FALLBACK_NAMES = (
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_S3_ACCESS_KEY_ID",
    "AWS_S3_SECRET_ACCESS_KEY",
    "AWS_STORAGE_BUCKET_NAME",
    "AWS_S3_ENDPOINT_URL",
)


def test_r2_credentials_do_not_occupy_the_aws_namespace():
    for name in AWS_FALLBACK_NAMES:
        assert not hasattr(base, name), (
            f"{name} must stay unset — django-ses/django-storages fall back to it. "
            "Pass R2 config via STORAGES OPTIONS instead."
        )


def test_both_buckets_carry_their_own_credentials():
    for options in (base.R2_PRIVATE_STORAGE_OPTIONS, base.R2_PUBLIC_STORAGE_OPTIONS):
        for key in ("bucket_name", "endpoint_url", "access_key", "secret_key"):
            assert key in options, f"missing {key}"


def test_public_and_private_buckets_are_distinct():
    assert base.R2_PUBLIC_STORAGE_OPTIONS["bucket_name"] != base.R2_PRIVATE_STORAGE_OPTIONS["bucket_name"], (
        "attachments would land in the world-readable bucket"
    )


def test_attachment_urls_stay_signed():
    """The private bucket must not hand out unsigned URLs to customer uploads."""
    assert base.R2_PRIVATE_STORAGE_OPTIONS["querystring_auth"] is True
    assert "custom_domain" not in base.R2_PRIVATE_STORAGE_OPTIONS


def test_attachments_download_rather_than_render():
    """Uploads are attacker-controlled; an .html/.svg must not be a live page.

    Set on the private bucket only -- branding logos are rendered in an <img> and
    are not attacker-controlled.
    """
    assert base.R2_PRIVATE_STORAGE_OPTIONS["object_parameters"]["ContentDisposition"] == "attachment"
    assert "ContentDisposition" not in base.R2_PUBLIC_STORAGE_OPTIONS["object_parameters"]


def test_branding_urls_do_not_expire():
    """Presigned logo URLs would dead-link in already-delivered email."""
    assert base.R2_PUBLIC_STORAGE_OPTIONS["querystring_auth"] is False


def test_production_wires_both_storages():
    from config.settings import production

    assert production.STORAGES["default"]["OPTIONS"] is base.R2_PRIVATE_STORAGE_OPTIONS
    assert production.STORAGES["public_media"]["OPTIONS"] is base.R2_PUBLIC_STORAGE_OPTIONS


def test_logo_field_uses_the_public_bucket():
    from apps.core.models import SourceApplication, public_media_storage

    field = SourceApplication._meta.get_field("logo")
    assert field._storage_callable is public_media_storage, "logo must not fall back to default (private) storage"
