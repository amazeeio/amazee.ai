#!/usr/bin/env python3

"""Notify Slack about new Bedrock models that are not yet deployed.

Queries ``GET /public/models/missing`` on the configured backend (defaults to
https://api.amazee.ai), diffs the response against
``scripts/bedrock-missing-models-state.json`` checked into the repo, and emits
machine-readable outputs the surrounding workflow uses to:

* post a Slack message listing only the *newly* missing models per market;
* commit the refreshed state file so the next run only flags new gaps.

The script is intentionally stdlib-only so the workflow doesn't need to
install anything.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError


DEFAULT_API_URL = "https://api.amazee.ai"
DEFAULT_STATE_FILE = (
    Path(__file__).resolve().parent / "bedrock-missing-models-state.json"
)
DEFAULT_REPORT_FILE = Path("bedrock-model-report.json")


def fetch_report(api_url: str, token: str | None, timeout: int = 60) -> dict[str, Any]:
    url = api_url.rstrip("/") + "/public/models/missing"
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    if token:
        request.add_header("Authorization", f"Bearer {token}")

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except HTTPError as exc:
        raise RuntimeError(
            f"Backend returned HTTP {exc.code} for {url}: {exc.read().decode('utf-8', 'replace')[:500]}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(f"Failed to reach {url}: {exc}") from exc


def load_state(state_file: Path) -> dict[str, Any]:
    if not state_file.exists():
        return {"markets": {}}
    try:
        return json.loads(state_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"State file '{state_file}' is not valid JSON: {exc}"
        ) from exc


def save_state(state_file: Path, state: dict[str, Any]) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def build_diff(report: dict[str, Any], previous_state: dict[str, Any]) -> dict[str, Any]:
    """Compute ``new_missing`` per market vs. ``previous_state`` and the next
    state to persist.

    A model counts as "new" only when it appears in the current report and
    *did not* appear in the previous state for the same market.
    """
    summary_sections: list[str] = []
    has_new = False

    next_state: dict[str, Any] = {
        "generated_at": report.get("generated_at"),
        "models_url": report.get("models_url"),
        "markets": {},
    }

    diff_markets: list[dict[str, Any]] = []

    for market_entry in report.get("markets", []):
        market = market_entry["market"]
        current_missing = market_entry.get("missing_models", [])
        current_ids = [m["model_id"] for m in current_missing]
        prev_ids = set(
            previous_state.get("markets", {})
            .get(market, {})
            .get("missing_model_ids", [])
        )
        new_missing = [m for m in current_missing if m["model_id"] not in prev_ids]
        if new_missing:
            has_new = True
            section_lines = [
                f"*{market}* ({market_entry['aws_region']}) — {len(new_missing)} new missing model(s)"
            ]
            for model in new_missing:
                section_lines.append(
                    f"• `{model['model_id']}` ({model['provider_name']} / {model['model_name']})"
                )
            summary_sections.append("\n".join(section_lines))

        diff_markets.append(
            {
                "market": market,
                "aws_region": market_entry["aws_region"],
                "available_model_count": market_entry["available_model_count"],
                "configured_model_count": market_entry["configured_model_count"],
                "missing_model_count": market_entry["missing_model_count"],
                "new_missing_model_count": len(new_missing),
                "new_missing_models": new_missing,
            }
        )
        next_state["markets"][market] = {
            "aws_region": market_entry["aws_region"],
            "regions": market_entry.get("regions", []),
            "missing_model_ids": sorted(current_ids),
        }

    comparable_prev = {
        "models_url": previous_state.get("models_url"),
        "markets": previous_state.get("markets", {}),
    }
    comparable_next = {
        "models_url": next_state.get("models_url"),
        "markets": next_state.get("markets", {}),
    }

    return {
        "generated_at": report.get("generated_at"),
        "models_url": report.get("models_url"),
        "is_authenticated": report.get("is_authenticated", False),
        "has_new_missing": has_new,
        "state_changed": comparable_next != comparable_prev,
        "markets": diff_markets,
        "slack_summary": "\n\n".join(summary_sections),
        "next_state": next_state,
    }


def write_github_outputs(diff: dict[str, Any]) -> None:
    """Emit ``$GITHUB_OUTPUT`` lines so the surrounding workflow can branch."""
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    summary = diff["slack_summary"] or "No new missing models detected."
    with open(output_path, "a", encoding="utf-8") as handle:
        handle.write(f"has_new_missing={'true' if diff['has_new_missing'] else 'false'}\n")
        handle.write(f"state_changed={'true' if diff['state_changed'] else 'false'}\n")
        # JSON-encode so newlines/quotes are safe to interpolate into a Slack payload.
        handle.write(f"summary_json={json.dumps(summary)}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--api-url",
        default=os.environ.get("AMAZEEAI_API_URL", DEFAULT_API_URL),
        help="Base URL of the api.amazee.ai backend (default: %(default)s).",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("AMAZEEAI_ADMIN_TOKEN") or None,
        help=(
            "Optional Bearer token. When set (typically a system admin API "
            "token), private/dedicated regions are included in the diff."
        ),
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=DEFAULT_STATE_FILE,
        help="Path to persisted state JSON (default: %(default)s).",
    )
    parser.add_argument(
        "--report-file",
        type=Path,
        default=DEFAULT_REPORT_FILE,
        help="Path to write the full diff report (default: %(default)s).",
    )
    args = parser.parse_args()

    try:
        report = fetch_report(args.api_url, args.token)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    previous_state = load_state(args.state_file)
    diff = build_diff(report, previous_state)
    save_state(args.state_file, diff["next_state"])

    args.report_file.write_text(json.dumps(diff, indent=2) + "\n", encoding="utf-8")

    write_github_outputs(diff)

    print(
        json.dumps(
            {
                "api_url": args.api_url,
                "is_authenticated": diff["is_authenticated"],
                "has_new_missing": diff["has_new_missing"],
                "state_changed": diff["state_changed"],
                "markets": {
                    m["market"]: m["new_missing_model_count"] for m in diff["markets"]
                },
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
