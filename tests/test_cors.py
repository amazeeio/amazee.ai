import pytest

from app.main import build_allowed_origins, default_origins, is_production_environment

LAGOON_ROUTES = "https://api.amazee.ai,https://api.enterprise.mirantis.com"
FRONTEND_ROUTE = "https://dashboard.amazee.ai/"
ROUTE_ORIGINS = [
    "https://api.amazee.ai",
    "https://api.enterprise.mirantis.com",
    "https://dashboard.amazee.ai",
]


def test_allowed_origins_production_has_no_localhost():
    origins = build_allowed_origins(LAGOON_ROUTES, FRONTEND_ROUTE, production=True)

    assert origins == ROUTE_ORIGINS
    assert not any("localhost" in o or "127.0.0.1" in o for o in origins)


def test_allowed_origins_local_keeps_localhost():
    origins = build_allowed_origins(LAGOON_ROUTES, FRONTEND_ROUTE, production=False)

    assert origins == default_origins + ROUTE_ORIGINS


def test_allowed_origins_production_skips_empty_and_malformed_routes():
    assert build_allowed_origins("", " ,not-a-url", production=True) == []


@pytest.mark.parametrize(
    "value, expected", [("production", True), ("development", False), (None, False)]
)
def test_is_production_environment(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("LAGOON_ENVIRONMENT_TYPE", raising=False)
    else:
        monkeypatch.setenv("LAGOON_ENVIRONMENT_TYPE", value)

    assert is_production_environment() is expected
