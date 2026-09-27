from app.registry.plugins.parsers import deepinfra

PAYLOAD = {
    "object": "list",
    "data": [
        {"id": "microsoft/phi-4", "metadata": {"context_length": 16384, "max_tokens": 16384,
         "pricing": {"input_tokens": 0.07, "output_tokens": 0.14}, "tags": ["chat"]}},
        {"id": "BAAI/bge-m3", "metadata": {"context_length": 8192, "max_tokens": 0,
         "pricing": {"input_tokens": 0.01}, "tags": ["embed"]}},
        {"id": "Qwen/Qwen3-ASR-0.6B", "metadata": {"pricing": {"input_seconds": 3.33e-06}, "tags": ["stt"]}},
        {"id": "hexgrad/Kokoro-82M", "metadata": {"pricing": {"input_characters": 0.62}, "tags": ["tts"]}},
        {"id": "black-forest-labs/FLUX-1-dev", "metadata": {"pricing": {"per_image_unit": 0.009}, "tags": ["image-gen"]}},
        {"metadata": {}},
    ],
}


def test_transform_maps_units_and_modes():
    out = deepinfra.transform(PAYLOAD)

    assert out["schema"] == 1 and out["source"] == "deepinfra_api"
    models = {m["model_id"]: m for m in out["models"]}
    assert set(models) == {
        "microsoft/phi-4", "BAAI/bge-m3", "Qwen/Qwen3-ASR-0.6B", "hexgrad/Kokoro-82M", "black-forest-labs/FLUX-1-dev",
    }
    assert models["microsoft/phi-4"]["prices"] == {
        "base": {"input_cost_per_token": 7e-08, "output_cost_per_token": 1.4e-07}
    }
    assert models["microsoft/phi-4"]["max_input_tokens"] == 16384
    assert models["BAAI/bge-m3"]["mode"] == "embedding"
    # Seconds are already per second; characters are per million.
    assert models["Qwen/Qwen3-ASR-0.6B"]["prices"] == {"base": {"input_cost_per_second": 3.33e-06}}
    assert models["hexgrad/Kokoro-82M"]["prices"] == {"base": {"input_cost_per_character": 6.2e-07}}
    # Image units are not mapped.
    assert models["black-forest-labs/FLUX-1-dev"]["prices"] == {}
    assert all(m["provider"] == "deepinfra" for m in out["models"])


def test_loader_finds_the_deepinfra_plugin():
    from app.registry.plugins.loader import load_plugins, override_sources

    assert load_plugins()["deepinfra_api"] is deepinfra
    assert "deepinfra_api" in override_sources()


def test_plugins_load_in_order(monkeypatch):
    import types

    from app.registry.plugins import loader

    def fake(name, **attrs):
        mod = types.ModuleType(name)
        mod.PRICE_ROLE, mod.parse = "fill", lambda: {}
        mod.__dict__.update(attrs)
        return mod

    mods = {
        "late": fake("late", SOURCE="late", ORDER=50),
        "b_early": fake("b_early", SOURCE="b_early", ORDER=10),
        "a_early": fake("a_early", SOURCE="a_early", ORDER=10),
        "no_order": fake("no_order", SOURCE="no_order"),
    }
    monkeypatch.setattr(loader.pkgutil, "iter_modules", lambda path: [types.SimpleNamespace(name=n) for n in mods])
    monkeypatch.setattr(loader.importlib, "import_module", lambda name: mods[name.rsplit(".", 1)[1]])

    plugins = loader.load_plugins()

    assert list(plugins) == ["no_order", "a_early", "b_early", "late"]
    assert isinstance(plugins["no_order"], ValueError)


def test_disabled_plugin_is_skipped(monkeypatch):
    from app.registry import config
    from app.registry.plugins.loader import load_plugins

    monkeypatch.setattr(config, "DISABLED_PLUGINS", {"deepinfra_api"})
    assert "deepinfra_api" not in load_plugins()
