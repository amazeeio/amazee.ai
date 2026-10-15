"""The registry API, for system admins only."""

# Registers its routes on the registry router before app.main includes it.
from app.registry.api import groups_aliases  # noqa: F401
