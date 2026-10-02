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


def _fake_loader(monkeypatch, mods):
    import types

    from app.registry.plugins import loader

    monkeypatch.setattr(loader.pkgutil, "iter_modules", lambda path: [types.SimpleNamespace(name=n) for n in mods])
    monkeypatch.setattr(loader.importlib, "import_module", lambda name: mods[name.rsplit(".", 1)[1]])
    return loader.load_plugins()


def _fake(name, **attrs):
    import types

    mod = types.ModuleType(name)
    mod.PRICE_ROLE, mod.parse, mod.ORDER = "fill", lambda: {}, 10
    mod.__dict__.update(attrs)
    return mod


def test_plugins_load_in_order(monkeypatch):
    mods = {
        "late": _fake("late", SOURCE="late", ORDER=50),
        "b_early": _fake("b_early", SOURCE="b_early", ORDER=10),
        "a_early": _fake("a_early", SOURCE="a_early", ORDER=10),
        "no_order": _fake("no_order", SOURCE="no_order", ORDER=None),
    }

    plugins = _fake_loader(monkeypatch, mods)

    assert list(plugins) == ["no_order", "a_early", "b_early", "late"]
    assert isinstance(plugins["no_order"], ValueError)


def test_loader_checks_priority(monkeypatch):
    mods = {
        "none": _fake("none", SOURCE="none"),
        "good": _fake("good", SOURCE="good", PRIORITY={"eol_date": 90}),
        "is_bool": _fake("is_bool", SOURCE="is_bool", PRIORITY={"eol_date": True}),
        "is_str": _fake("is_str", SOURCE="is_str", PRIORITY={"eol_date": "90"}),
        "is_list": _fake("is_list", SOURCE="is_list", PRIORITY=[("eol_date", 90)]),
        "int_key": _fake("int_key", SOURCE="int_key", PRIORITY={1: 90}),
    }

    plugins = _fake_loader(monkeypatch, mods)

    assert plugins["none"] is mods["none"] and plugins["good"] is mods["good"]
    for name in ("is_bool", "is_str", "is_list", "int_key"):
        assert isinstance(plugins[name], ValueError) and "PRIORITY" in str(plugins[name])


def test_loader_rejects_reserved_sources(monkeypatch):
    mods = {name: _fake(name, SOURCE=name) for name in ("litellm", "proxy", "manual", "ok_src")}

    plugins = _fake_loader(monkeypatch, mods)

    assert plugins["ok_src"] is mods["ok_src"]
    for name in ("litellm", "proxy", "manual"):
        assert isinstance(plugins[name], ValueError) and "reserved" in str(plugins[name])


def test_disabled_plugin_is_skipped(monkeypatch):
    from app.registry import config
    from app.registry.plugins.loader import load_plugins

    monkeypatch.setattr(config, "DISABLED_PLUGINS", {"deepinfra_api"})
    assert "deepinfra_api" not in load_plugins()


def test_disabled_override_plugin_keeps_its_precedence(monkeypatch):
    from app.registry import config
    from app.registry.plugins.loader import override_sources

    monkeypatch.setattr(config, "DISABLED_PLUGINS", {"deepinfra_api"})
    assert "deepinfra_api" in override_sources()
