import pytest
from pydantic import ValidationError

from app.core.config import Settings, settings
from app.registry.config import registry_manages


def test_registry_manages_every_region_on_local(monkeypatch):
    monkeypatch.setattr(settings, "ENV_SUFFIX", "local")
    monkeypatch.setattr(settings, "REGISTRY_MANAGED_REGIONS", "")
    assert registry_manages("any-region")


def test_registry_manages_no_region_on_production_with_an_empty_list(monkeypatch):
    monkeypatch.setattr(settings, "ENV_SUFFIX", "production")
    monkeypatch.setattr(settings, "REGISTRY_MANAGED_REGIONS", "")
    assert not registry_manages("us1")


def test_registry_manages_only_listed_regions_on_production(monkeypatch):
    monkeypatch.setattr(settings, "ENV_SUFFIX", "production")
    monkeypatch.setattr(settings, "REGISTRY_MANAGED_REGIONS", " us1 , eu1,")
    assert registry_manages("us1")
    assert registry_manages("eu1")
    assert not registry_manages("ch1")


def test_a_shared_region_stops_the_settings_off_local():
    with pytest.raises(ValidationError, match="us1"):
        Settings(
            ENV_SUFFIX="production",
            CATALOG_MANAGED_REGIONS="eu1,us1",
            REGISTRY_MANAGED_REGIONS="us1",
        )


def test_a_shared_region_is_allowed_on_local():
    s = Settings(
        ENV_SUFFIX="local",
        CATALOG_MANAGED_REGIONS="us1",
        REGISTRY_MANAGED_REGIONS="us1",
    )
    assert s.REGISTRY_MANAGED_REGIONS == "us1"
