"""Daily fill of each registry model's family and manufacturer where they are empty.

It never overwrites a value, so a manual change stays and a second run
changes nothing.
"""

import re
from collections import defaultdict
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.registry.models import models_by_ident
from app.registry.runs import record_run

STEP = "family"

MAKER_ALIASES = {
    "meta-llama": "meta",
    "deepseek-ai": "deepseek",
    "mistralai": "mistral",
    "moonshotai": "moonshot",
    "minimaxai": "minimax",
    "zai-org": "zai",
}
# Only these ids start with a maker and a dot. Other ids carry real dots, such as `gpt-4.1`.
BEDROCK_PROVIDERS = {"bedrock", "bedrock_mantle"}
# Only these ids name the maker before a slash. Other providers put a zone, size or route there.
SLASH_MAKER_PROVIDERS = {"deepinfra", "vertex_ai"}

# A 4-digit version after `@` is a model version and stays; any other suffix goes.
_VERTEX_SUFFIX = re.compile(r"@(?:(\d{4})(?=-|$))?.*$")
# The `:0` revision and a context part such as `:300k` always go. Before them, only
# version 1 goes: a later version, such as Claude 3.5 Sonnet `-v2`, is another model.
_BEDROCK_VERSION = re.compile(r"(?:-v?1)?:\d+(?::\w+)?$")
# A segment with a dash is model text, not a maker.
_BEDROCK_PREFIX = re.compile(r"^([a-z0-9]+)\.")
# Only a full date goes, as `20250929` or `2025-09-29`; 4-digit parts are different model versions.
_DATE = re.compile(r"-(?:\d{8}|\d{4}-\d{2}-\d{2})(?=-|$)")
_NOT_ALNUM = re.compile(r"[^a-z0-9]")


def _split(provider: str, model_id: str) -> tuple[str | None, str]:
    """The maker prefix of a model id, or None, and the rest without version suffixes."""
    rest = model_id.lower()
    rest = _VERTEX_SUFFIX.sub(lambda m: f"-{m[1]}" if m[1] else "", rest, count=1)
    rest = _BEDROCK_VERSION.sub("", rest)
    rest = rest.removesuffix("-maas")
    # A prefix with a second slash is a quality and a size, not a maker.
    prefix, _, rest = rest.rpartition("/")
    maker = prefix if provider in SLASH_MAKER_PROVIDERS and prefix and "/" not in prefix else None
    if provider in BEDROCK_PROVIDERS and (dotted := _BEDROCK_PREFIX.match(rest)):
        maker, rest = dotted[1], rest[dotted.end():]
    return maker, rest


def family_of(provider: str, model_id: str) -> str | None:
    """The model id without maker, version suffixes, dates and punctuation, so the
    same model at different providers gets the same family."""
    _, rest = _split(provider, model_id)
    return _NOT_ALNUM.sub("", _DATE.sub("", rest)) or None


def manufacturer_of(provider: str, model_id: str) -> str | None:
    maker, _ = _split(provider, model_id)
    return MAKER_ALIASES.get(maker, maker) if maker else None


def apply_family(db: Session) -> dict:
    """Fill only empty values. A model without its own manufacturer takes the one
    of its family when the family has exactly one."""
    models = models_by_ident(db)
    families = manufacturers = 0
    now = datetime.now(UTC)

    for (provider, model_id), model in models.items():
        if model.family is None and (family := family_of(provider, model_id)):
            model.family = family
            model.updated_at = now
            families += 1
        if model.manufacturer is None and (maker := manufacturer_of(provider, model_id)):
            model.manufacturer = maker
            model.updated_at = now
            manufacturers += 1

    makers: dict[str, set[str]] = defaultdict(set)
    for model in models.values():
        if model.family and model.manufacturer:
            makers[model.family].add(model.manufacturer)
    for model in models.values():
        if model.family and model.manufacturer is None and len(makers[model.family]) == 1:
            model.manufacturer = next(iter(makers[model.family]))
            model.updated_at = now
            manufacturers += 1
    return {"models": len(models), "families": families, "manufacturers": manufacturers}


def run_family(db: Session) -> dict:
    return record_run(db, STEP, lambda: apply_family(db))
