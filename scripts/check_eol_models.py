#!/usr/bin/env python3

"""Check for models approaching or past their End-of-Life (EOL) date.

Queries ``GET /public/models`` on the configured backend (defaults to
https://api.amazee.ai), parses the ``(EOL: YYYY-MM-DD)`` annotation from
each model's ``metadata_raw`` field, and emits GitHub Actions outputs for
Slack notification when models are:

* **Expired**: EOL date is in the past.
* **Approaching EOL**: EOL date is within the configured warning window
  (default 30 days).

The script is intentionally stdlib-only so the workflow doesn't need to
install anything.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError


DEFAULT_API_URL = "https://api.amazee.ai"
# How far in advance to warn about upcoming EOL dates.
DEFAULT_WARNING_DAYS = 30


# Regex to extract (EOL: YYYY-MM-DD) from metadata_raw strings.
_EOL_PATTERN = re.compile(r"\(EOL:\s*(\d{4}-\d{2}-\d{2})\)")


def fetch_public_models(api_url: str, timeout: int = 60) -> list[dict[str, Any]]:
    """Fetch the public models endpoint and return the JSON response."""
    url = api_url.rstrip("/") + "/public/models"
    request = urllib.request.Request(url, headers={"Accept": "application/json"})

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.load(response)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Invalid JSON from {url}: {exc}"
        ) from exc
    except HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(
            f"Backend returned HTTP {exc.code} for {url}: {body}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(f"Failed to reach {url}: {exc}") from exc

    if not isinstance(data, list):
        raise RuntimeError(
            f"Unexpected response format from {url}: expected a list, got {type(data).__name__}"
        )
    return data


def extract_eol_date(metadata_raw: Any) -> date | None:
    """Extract an EOL date from metadata_raw if present.

    metadata_raw can be a string or a dict; we look for the pattern in
    string values. Returns None if no EOL date is found.
    """
    text = None
    if isinstance(metadata_raw, str):
        text = metadata_raw
    elif isinstance(metadata_raw, dict):
        # Check common keys that might hold a description string.
        for key in ("description", "info", "metadata", "raw"):
            val = metadata_raw.get(key)
            if isinstance(val, str):
                text = val
                break
        # Fallback: stringify the whole dict and search.
        if text is None:
            text = json.dumps(metadata_raw)

    if text is None:
        return None

    match = _EOL_PATTERN.search(text)
    if not match:
        return None

    try:
        return date.fromisoformat(match.group(1))
    except ValueError:
        return None


def check_eol_models(
    regions: list[dict[str, Any]], warning_days: int = DEFAULT_WARNING_DAYS
) -> dict[str, list[dict[str, Any]]]:
    """Check all models for EOL status.

    Returns a dict with two keys:
    - "expired": models past their EOL date
    - "approaching": models within the warning window
    """
    today = date.today()
    warning_threshold = today + timedelta(days=warning_days)

    expired: list[dict[str, Any]] = []
    approaching: list[dict[str, Any]] = []

    # Track model_ids we've already seen to avoid duplicates across regions.
    seen: set[str] = set()

    for region_data in regions:
        region_name = region_data.get("region", "unknown")
        models = region_data.get("models", [])
        if not isinstance(models, list):
            continue

        for model in models:
            model_id = model.get("model_id", "unknown")

            # Deduplicate: same model may appear in multiple regions.
            if model_id in seen:
                continue
            seen.add(model_id)

            eol_date = extract_eol_date(model.get("metadata_raw"))
            if eol_date is None:
                continue

            entry = {
                "model_id": model_id,
                "display_name": model.get("display_name", model_id),
                "eol_date": eol_date.isoformat(),
                "days_remaining": (eol_date - today).days,
                "region": region_name,
            }

            if eol_date < today:
                expired.append(entry)
            elif eol_date <= warning_threshold:
                approaching.append(entry)

    # Sort: expired by most overdue first, approaching by soonest first.
    expired.sort(key=lambda m: m["days_remaining"])
    approaching.sort(key=lambda m: m["days_remaining"])

    return {"expired": expired, "approaching": approaching}


def build_slack_summary(results: dict[str, list[dict[str, Any]]]) -> str:
    """Build a Slack mrkdwn summary of EOL findings."""
    sections: list[str] = []

    expired = results["expired"]
    approaching = results["approaching"]

    if expired:
        lines = [f":rotating_light: *{len(expired)} model(s) PAST End-of-Life:*"]
        for model in expired:
            days_past = abs(model["days_remaining"])
            lines.append(
                f"• `{model['model_id']}` — EOL was *{model['eol_date']}* "
                f"({days_past} day{'s' if days_past != 1 else ''} ago)"
            )
        sections.append("\n".join(lines))

    if approaching:
        lines = [f":warning: *{len(approaching)} model(s) approaching End-of-Life:*"]
        for model in approaching:
            days_left = model["days_remaining"]
            lines.append(
                f"• `{model['model_id']}` — EOL on *{model['eol_date']}* "
                f"({days_left} day{'s' if days_left != 1 else ''} remaining)"
            )
        sections.append("\n".join(lines))

    return "\n\n".join(sections)


def write_github_outputs(results: dict[str, list[dict[str, Any]]], summary: str) -> None:
    """Emit $GITHUB_OUTPUT lines for the surrounding workflow."""
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return

    has_alerts = bool(results["expired"] or results["approaching"])

    with open(output_path, "a", encoding="utf-8") as handle:
        handle.write(f"has_alerts={'true' if has_alerts else 'false'}\n")
        handle.write(f"expired_count={len(results['expired'])}\n")
        handle.write(f"approaching_count={len(results['approaching'])}\n")

    # Write the summary to a file the workflow can read.
    summary_file = Path("eol-summary.txt")
    summary_file.write_text(summary, encoding="utf-8")


def main() -> int:
    api_url = os.environ.get("AMAZEEAI_API_URL", DEFAULT_API_URL)
    warning_days_raw = os.environ.get("EOL_WARNING_DAYS", str(DEFAULT_WARNING_DAYS))
    try:
        warning_days = int(warning_days_raw)
        if warning_days < 0:
            raise ValueError("must be >= 0")
    except ValueError as exc:
        print(
            f"ERROR: EOL_WARNING_DAYS must be a non-negative integer (got {warning_days_raw!r}): {exc}",
            file=sys.stderr,
        )
        return 1

    try:
        regions = fetch_public_models(api_url)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    results = check_eol_models(regions, warning_days=warning_days)
    summary = build_slack_summary(results)

    write_github_outputs(results, summary)

    # Print human-readable output for logs.
    if results["expired"] or results["approaching"]:
        print(summary)
        print(
            f"\nTotal: {len(results['expired'])} expired, "
            f"{len(results['approaching'])} approaching EOL "
            f"(within {warning_days} days)"
        )
    else:
        print(f"No models at or approaching EOL (checked within {warning_days} days).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
