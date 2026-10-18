import pytest
from django.conf import settings

from apps.widget_api import widget_js as module

pytestmark = pytest.mark.django_db  # middleware touches the session table


@pytest.fixture
def dist(tmp_path, settings):
    settings.BASE_DIR = tmp_path
    vendor = tmp_path / "widget" / "dist" / "vendor"
    vendor.mkdir(parents=True)
    (tmp_path / "widget" / "dist" / "helpdesk-widget.js").write_text("widget")
    for name in module.VENDOR_FILES:
        (vendor / name).write_text(name)
    return tmp_path


def test_serves_the_bundle_and_its_vendor_files(client, dist):
    v = settings.WIDGET_VERSION
    assert client.get(f"/widget/v{v}/helpdesk-widget.js").content == b"widget"
    for name in module.VENDOR_FILES:
        response = client.get(f"/widget/v{v}/vendor/{name}")
        assert response.status_code == 200 and response.content == name.encode()
        assert "immutable" in response["Cache-Control"]


def test_previous_version_still_served_unknown_refused(client, dist):
    assert client.get(f"/widget/v{module.PREVIOUS_VERSIONS[0]}/helpdesk-widget.js").status_code == 200
    assert client.get("/widget/v0.0.1/helpdesk-widget.js").status_code == 404


def test_vendor_route_serves_only_listed_files(client, dist):
    v = settings.WIDGET_VERSION
    assert client.get(f"/widget/v{v}/vendor/..%2F..%2Fsettings.py").status_code == 404
    assert client.get(f"/widget/v{v}/vendor/other.js").status_code == 404
