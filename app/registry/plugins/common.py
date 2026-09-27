"""Helpers every plugin uses, so none writes its own HTTP or unit code."""

from app.registry.litellm import fetch_model_list

fetch_json = fetch_model_list


def per_million(value) -> float | None:
    """A price per million units as a price per unit, or None if not a number.

    Rounded so 0.2 per million is 2e-07, not 2.0000000000000002e-07.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    return float(f"{value / 1_000_000:.12g}")
