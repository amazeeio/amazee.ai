#!/usr/bin/env python3

"""Notify Slack about new hyperscaler models that are not yet deployed.

Queries ``GET /models/missing/{provider}`` on the configured backend
(defaults to https://api.amazee.ai), diffs the response against
``scripts/missing-models-state-{provider}.json`` checked into the repo, and
emits machine-readable outputs the surrounding workflow uses to:

* post a Slack message listing only the *newly* missing models per region group;
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
DEFAULT_PROVIDER = "aws"
SCRIPTS_DIR = Path(__file__).resolve().parent
# Slack Block Kit section.text.text supports up to 3000 characters.
# Keep some headroom so chunked summaries remain safely within the field limit.
SLACK_MAX_TEXT_LENGTH = 2900


def _default_state_file(provider: str) -> Path:
    return SCRIPTS_DIR / f"missing-models-state-{provider}.json"


def _default_report_file(provider: str) -> Path:
    return Path(f"missing-models-report-{provider}.json")


def fetch_report(
    api_url: str, provider: str, token: str | None, timeout: int = 60
) -> dict[str, Any]:
    url = api_url.rstrip("/") + f"/models/missing/{provider}"
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    if token:
        request.add_header("Authorization", f"Bearer {token}")

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(
            f"Backend returned HTTP {exc.code} for {url}: {body}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(f"Failed to reach {url}: {exc}") from exc


def load_state(state_file: Path) -> dict[str, Any]:
    if not state_file.exists():
        return {"region_groups": {}}
    try:
        return json.loads(state_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"State file '{state_file}' is not valid JSON: {exc}"
        ) from exc


def save_state(state_file: Path, state: dict[str, Any]) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def build_diff(
    report: dict[str, Any], previous_state: dict[str, Any]
) -> dict[str, Any]:
    """Compute per-region-group ``new_missing`` vs. ``previous_state`` plus
    the next state to persist.

    A model counts as "new" only when it appears in the current report and
    *did not* appear in the previous state for the same region group.
    """
    summary_sections: list[str] = []
    has_new = False

    next_state: dict[str, Any] = {
        "provider": report.get("provider"),
        "generated_at": report.get("generated_at"),
        "models_url": report.get("models_url"),
        "region_groups": {},
    }

    diff_groups: list[dict[str, Any]] = []
    prev_groups = previous_state.get("region_groups", {})

    for entry in report.get("region_groups", []):
        group = entry["region_group"]
        current_missing = entry.get("missing_models", [])
        current_ids = [m["model_id"] for m in current_missing]
        prev_ids = set(prev_groups.get(group, {}).get("missing_model_ids", []))
        new_missing = [m for m in current_missing if m["model_id"] not in prev_ids]
        if new_missing:
            has_new = True
            section_lines = [
                f"*{group}* ({entry['upstream_region']}) — {len(new_missing)} new missing model(s)"
            ]
            for model in new_missing:
                section_lines.append(
                    f"• `{model['model_id']}` ({model['provider_name']} / {model['model_name']})"
                )
            summary_sections.append("\n".join(section_lines))

        diff_groups.append(
            {
                "region_group": group,
                "upstream_region": entry["upstream_region"],
                "available_model_count": entry["available_model_count"],
                "configured_model_count": entry["configured_model_count"],
                "missing_model_count": entry["missing_model_count"],
                "new_missing_model_count": len(new_missing),
                "new_missing_models": new_missing,
            }
        )
        next_state["region_groups"][group] = {
            "upstream_region": entry["upstream_region"],
            "regions": entry.get("regions", []),
            "missing_model_ids": sorted(current_ids),
        }

    comparable_prev = {
        "models_url": previous_state.get("models_url"),
        "region_groups": previous_state.get("region_groups", {}),
    }
    comparable_next = {
        "models_url": next_state.get("models_url"),
        "region_groups": next_state.get("region_groups", {}),
    }

    return {
        "provider": report.get("provider"),
        "generated_at": report.get("generated_at"),
        "models_url": report.get("models_url"),
        "is_authenticated": report.get("is_authenticated", False),
        "has_new_missing": has_new,
        "state_changed": comparable_next != comparable_prev,
        "region_groups": diff_groups,
        "slack_summary": "\n\n".join(summary_sections),
        "next_state": next_state,
    }


def _chunk_summary(summary: str, max_length: int = SLACK_MAX_TEXT_LENGTH) -> list[str]:
    """Split the Slack summary into chunks that each fit within Slack's text block limit.

    Splits on section boundaries (double newlines) to keep region-group sections intact.
    """
    if not summary:
        return []
    if len(summary) <= max_length:
        return [summary]

    sections = summary.split("\n\n")
    chunks: list[str] = []
    current_parts: list[str] = []
    current_len = 0

    for section in sections:
        # Guard: if a single section exceeds max_length, split by lines.
        if len(section) > max_length:
            if current_parts:
                chunks.append("\n\n".join(current_parts))
                current_parts = []
                current_len = 0
            lines = section.split("\n")
            line_buf: list[str] = []
            buf_len = 0
            for line in lines:
                line_addition = len(line) + (1 if line_buf else 0)
                if line_buf and buf_len + line_addition > max_length:
                    chunks.append("\n".join(line_buf))
                    line_buf = [line]
                    buf_len = len(line)
                else:
                    line_buf.append(line)
                    buf_len += line_addition
            if line_buf:
                chunks.append("\n".join(line_buf))
            continue
        addition = len(section) + (2 if current_parts else 0)  # +2 for "\n\n" join
        if current_parts and current_len + addition > max_length:
            chunks.append("\n\n".join(current_parts))
            current_parts = [section]
            current_len = len(section)
        else:
            current_parts.append(section)
            current_len += addition

    if current_parts:
        chunks.append("\n\n".join(current_parts))

    return chunks


def write_github_outputs(diff: dict[str, Any]) -> None:
    """Emit ``$GITHUB_OUTPUT`` lines so the surrounding workflow can branch.

    Also writes chunked summaries to a JSON file for the workflow to iterate over
    when sending multiple Slack messages.
    """
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    chunks = _chunk_summary(diff["slack_summary"])
    if not chunks:
        chunks = ["No new missing models detected."]

    # Write chunks to a file the workflow can read with jq.
    chunks_file = Path("slack-summary-chunks.json")
    chunks_file.write_text(json.dumps(chunks, indent=2) + "\n", encoding="utf-8")

    with open(output_path, "a", encoding="utf-8") as handle:
        handle.write(
            f"has_new_missing={'true' if diff['has_new_missing'] else 'false'}\n"
        )
        handle.write(f"state_changed={'true' if diff['state_changed'] else 'false'}\n")
        handle.write(f"provider={diff.get('provider', '')}\n")
        handle.write(f"summary_chunk_count={len(chunks)}\n")
        # Keep summary_json as the first chunk for backward compat.
        handle.write(f"summary_json={json.dumps(chunks[0])}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--provider",
        default=os.environ.get("MISSING_MODELS_PROVIDER", DEFAULT_PROVIDER),
        choices=["aws", "google", "azure"],
        help="Hyperscaler to query (default: %(default)s).",
    )
    parser.add_argument(
        "--api-url",
        default=os.environ.get("AMAZEEAI_API_URL", DEFAULT_API_URL),
        help="Base URL of the api.amazee.ai backend (default: %(default)s).",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("AMAZEEAI_ADMIN_TOKEN") or None,
        help=(
            "Required Bearer token for the protected missing-models endpoint. "
            "A system admin token includes private/dedicated regions in the diff."
        ),
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        help="Path to persisted state JSON (default: scripts/missing-models-state-{provider}.json).",
    )
    parser.add_argument(
        "--report-file",
        type=Path,
        help="Path to write the full diff report (default: missing-models-report-{provider}.json).",
    )
    args = parser.parse_args()

    if not args.token:
        print(
            "ERROR: --token or AMAZEEAI_ADMIN_TOKEN is required for /models/missing.",
            file=sys.stderr,
        )
        return 1

    state_file = args.state_file or _default_state_file(args.provider)
    report_file = args.report_file or _default_report_file(args.provider)

    try:
        report = fetch_report(args.api_url, args.provider, args.token)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    previous_state = load_state(state_file)
    diff = build_diff(report, previous_state)
    save_state(state_file, diff["next_state"])

    report_file.write_text(json.dumps(diff, indent=2) + "\n", encoding="utf-8")

    write_github_outputs(diff)

    print(
        json.dumps(
            {
                "api_url": args.api_url,
                "provider": args.provider,
                "is_authenticated": diff["is_authenticated"],
                "has_new_missing": diff["has_new_missing"],
                "state_changed": diff["state_changed"],
                "region_groups": {
                    g["region_group"]: g["new_missing_model_count"]
                    for g in diff["region_groups"]
                },
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
