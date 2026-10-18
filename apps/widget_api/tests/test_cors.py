from apps.widget_api.cors import domain_matches


def test_exact_match():
    assert domain_matches("appsfol.io", "appsfol.io") is True


def test_wildcard_subdomain_match():
    assert domain_matches("test.appsfol.io", "*.appsfol.io") is True
    assert domain_matches("a.b.appsfol.io", "*.appsfol.io") is True


def test_wildcard_does_not_match_apex():
    assert domain_matches("appsfol.io", "*.appsfol.io") is False


def test_non_match():
    assert domain_matches("evil.com", "appsfol.io") is False
    assert domain_matches("notappsfol.io", "*.appsfol.io") is False
