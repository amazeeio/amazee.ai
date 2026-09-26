"""The registry must stay removable in one step, so imports only go one way."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

OLD_CATALOG = {
    "app.services.model_sync",
    "app.services.model_eol",
    "app.services.access_groups",
    "app.api.admin_models",
    "app.api.admin_model_apply",
    "app.api.access_groups",
    "app.api.public",
    "app.services.litellm",
}
# The only files outside the package allowed to import it.
REGISTRY_USERS = {
    "app/migrations/env.py",
    "scripts/initialise_resources.py",
    "scripts/trigger_registry_daily_job.py",
    "scripts/registry_import_proxies.py",
}


def _imports(path: Path) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_registry_does_not_import_old_catalog():
    for path in (ROOT / "app" / "registry").rglob("*.py"):
        bad = {n for n in _imports(path) if any(n == m or n.startswith(m + ".") for m in OLD_CATALOG)}
        assert not bad, f"{path.relative_to(ROOT)} imports {bad}"


def test_only_known_files_import_registry():
    for folder in ("app", "scripts"):
        for path in (ROOT / folder).rglob("*.py"):
            rel = path.relative_to(ROOT).as_posix()
            if rel.startswith("app/registry/") or rel in REGISTRY_USERS:
                continue
            bad = {n for n in _imports(path) if n == "app.registry" or n.startswith("app.registry.")}
            assert not bad, f"{rel} imports {bad}"
