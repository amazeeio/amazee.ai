"""One module per source. The runner loads every module here.

A plugin module defines:

    SOURCE = "unique_name"      # stored as `source` on every row it writes
    ORDER = 10                  # run order: lower first, ties by SOURCE
    PRICE_ROLE = "override"     # "override": beats LiteLLM's price; "fill": only unpriced models
    def parse() -> dict: ...    # no input: fetches its own source

and returns:

    {"schema": 1, "source": SOURCE, "models": [
        {"provider": "deepinfra", "model_id": "BAAI/bge-m3",   # required
         "mode": "embedding", "max_input_tokens": 8192, "max_output_tokens": None,
         "prices": {"base": {...}, "geo:us": {...}, "region:us-east-1": {...}},
         "lifecycle": {"status": "ACTIVE", "launched_at": "2026-01-01", "legacy_at": None,
                       "extended_access_until": None, "eol_date": None},
         "regions": [{"cloud_region": "us-east-1", "call_types": ["ON_DEMAND"]}]}]}

All plugins run as one daily step, after LiteLLM's model list and before
models.dev. Give first-party "override" plugins a lower ORDER than "fill"
ones: a fill plugin then sees the override prices of the same run and steps
aside.

Every key but provider and model_id is optional. Prices use LiteLLM's
per-unit field names (`input_cost_per_token`, ...); the plugin converts units.
Keep fetching in parse() and the mapping in a pure function, so tests can
feed a saved payload.
"""
