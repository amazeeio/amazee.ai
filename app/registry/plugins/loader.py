"""Find the plugin modules under `parsers/`, without running them."""

import importlib
import logging
import pkgutil
from types import ModuleType

from app.registry import config
from app.registry.plugins import parsers

logger = logging.getLogger(__name__)

PRICE_ROLES = ("override", "fill")
# Other writers store rows under these names, so a plugin with one of them would mix its rows with theirs.
RESERVED_SOURCES = frozenset({"litellm", "proxy", "manual"})


def load_plugins(include_disabled: bool = False) -> dict[str, ModuleType | Exception]:
    """SOURCE -> module for every enabled plugin, in run order: by ORDER, then
    by SOURCE, so the order never depends on file listing. A module that fails
    to load maps its file name to the error, so the runner can record it; it
    sorts first, so a broken plugin is reported before the others run."""
    plugins: dict[str, ModuleType | Exception] = {}
    for info in sorted(pkgutil.iter_modules(parsers.__path__), key=lambda i: i.name):
        try:
            module = importlib.import_module(f"{parsers.__name__}.{info.name}")
            source = getattr(module, "SOURCE", None)
            if not isinstance(source, str) or not source:
                raise ValueError("SOURCE is missing")
            if source in RESERVED_SOURCES:
                raise ValueError(f"SOURCE {source!r} is reserved")
            if getattr(module, "PRICE_ROLE", None) not in PRICE_ROLES:
                raise ValueError(f"PRICE_ROLE must be one of {PRICE_ROLES}")
            if not callable(getattr(module, "parse", None)):
                raise ValueError("parse() is missing")
            order = getattr(module, "ORDER", None)
            if isinstance(order, bool) or not isinstance(order, int):
                raise ValueError("ORDER must be a whole number")
            priority = getattr(module, "PRIORITY", None)
            if priority is not None and not (
                isinstance(priority, dict)
                and all(isinstance(k, str) for k in priority)
                and all(isinstance(v, int) and not isinstance(v, bool) for v in priority.values())
            ):
                raise ValueError("PRIORITY must map field names to whole numbers")
            if source in plugins:
                raise ValueError(f"SOURCE {source!r} is used twice")
        except Exception as e:  # a broken plugin must not stop the others
            logger.error("Plugin %s cannot load: %s", info.name, e)
            plugins[info.name] = e
            continue
        if include_disabled or source not in config.DISABLED_PLUGINS:
            plugins[source] = module
    return dict(
        sorted(plugins.items(), key=lambda kv: (not isinstance(kv[1], Exception), getattr(kv[1], "ORDER", 0), kv[0]))
    )


def override_sources() -> set[str]:
    """Sources whose prices LiteLLM's list must not overwrite. A disabled
    plugin counts too: switching one off keeps its rows as they are."""
    return {
        source
        for source, module in load_plugins(include_disabled=True).items()
        if not isinstance(module, Exception) and module.PRICE_ROLE == "override"
    }
