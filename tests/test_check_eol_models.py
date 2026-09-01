"""Tests for scripts/check_eol_models.py."""

import json
import runpy
from datetime import date, timedelta
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import pytest

_SCRIPT_PATH = str(
    Path(__file__).resolve().parent.parent / "scripts" / "check_eol_models.py"
)


def _load_module():
    return runpy.run_path(_SCRIPT_PATH)


# ---------------------------------------------------------------------------
# extract_eol_date
# ---------------------------------------------------------------------------


class TestExtractEolDate:
    def test_string_with_eol(self):
        mod = _load_module()
        result = mod["extract_eol_date"](
            "High speed, low cost Claude model. (EOL: 2026-10-01)"
        )
        assert result == date(2026, 10, 1)

    def test_string_without_eol(self):
        mod = _load_module()
        result = mod["extract_eol_date"]("Just a normal description, no EOL.")
        assert result is None

    def test_dict_with_eol_in_description_key(self):
        mod = _load_module()
        result = mod["extract_eol_date"](
            {"description": "Some model. (EOL: 2025-03-15)"}
        )
        assert result == date(2025, 3, 15)

    def test_dict_fallback_to_json_stringify(self):
        mod = _load_module()
        result = mod["extract_eol_date"](
            {"custom_key": "Will be deprecated. (EOL: 2025-12-31)"}
        )
        assert result == date(2025, 12, 31)

    def test_none_input(self):
        mod = _load_module()
        assert mod["extract_eol_date"](None) is None

    def test_integer_input(self):
        mod = _load_module()
        assert mod["extract_eol_date"](42) is None

    def test_invalid_date_format(self):
        mod = _load_module()
        # Valid regex match but invalid date.
        result = mod["extract_eol_date"]("(EOL: 2025-13-40)")
        assert result is None

    def test_eol_with_extra_spaces(self):
        mod = _load_module()
        result = mod["extract_eol_date"]("Model info. (EOL:  2026-06-15)")
        assert result == date(2026, 6, 15)


# ---------------------------------------------------------------------------
# check_eol_models
# ---------------------------------------------------------------------------


class TestCheckEolModels:
    def _make_regions(self, models):
        """Helper to wrap models into the region response structure."""
        return [{"region": "us-east-1", "status": "available", "models": models}]

    def test_expired_model(self):
        mod = _load_module()
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        regions = self._make_regions(
            [
                {
                    "model_id": "old-model",
                    "display_name": "Old Model",
                    "metadata_raw": f"Legacy model. (EOL: {yesterday})",
                }
            ]
        )
        result = mod["check_eol_models"](regions, warning_days=30)
        assert len(result["expired"]) == 1
        assert len(result["approaching"]) == 0
        assert result["expired"][0]["model_id"] == "old-model"
        assert result["expired"][0]["days_remaining"] < 0

    def test_approaching_model(self):
        mod = _load_module()
        in_two_weeks = (date.today() + timedelta(days=14)).isoformat()
        regions = self._make_regions(
            [
                {
                    "model_id": "soon-model",
                    "display_name": "Soon Model",
                    "metadata_raw": f"Will retire. (EOL: {in_two_weeks})",
                }
            ]
        )
        result = mod["check_eol_models"](regions, warning_days=30)
        assert len(result["expired"]) == 0
        assert len(result["approaching"]) == 1
        assert result["approaching"][0]["model_id"] == "soon-model"
        assert result["approaching"][0]["days_remaining"] == 14

    def test_model_beyond_warning_window(self):
        mod = _load_module()
        far_future = (date.today() + timedelta(days=90)).isoformat()
        regions = self._make_regions(
            [
                {
                    "model_id": "safe-model",
                    "display_name": "Safe Model",
                    "metadata_raw": f"Fine for now. (EOL: {far_future})",
                }
            ]
        )
        result = mod["check_eol_models"](regions, warning_days=30)
        assert len(result["expired"]) == 0
        assert len(result["approaching"]) == 0

    def test_model_without_eol(self):
        mod = _load_module()
        regions = self._make_regions(
            [
                {
                    "model_id": "no-eol-model",
                    "display_name": "No EOL",
                    "metadata_raw": "Just a regular model.",
                }
            ]
        )
        result = mod["check_eol_models"](regions, warning_days=30)
        assert len(result["expired"]) == 0
        assert len(result["approaching"]) == 0

    def test_deduplication_across_regions(self):
        mod = _load_module()
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        regions = [
            {
                "region": "us-east-1",
                "status": "available",
                "models": [
                    {
                        "model_id": "dupe-model",
                        "display_name": "Dupe",
                        "metadata_raw": f"Old. (EOL: {yesterday})",
                    }
                ],
            },
            {
                "region": "eu-central-1",
                "status": "available",
                "models": [
                    {
                        "model_id": "dupe-model",
                        "display_name": "Dupe",
                        "metadata_raw": f"Old. (EOL: {yesterday})",
                    }
                ],
            },
        ]
        result = mod["check_eol_models"](regions, warning_days=30)
        # Should only appear once despite being in two regions.
        assert len(result["expired"]) == 1

    def test_sorting_expired_most_overdue_first(self):
        mod = _load_module()
        three_days_ago = (date.today() - timedelta(days=3)).isoformat()
        ten_days_ago = (date.today() - timedelta(days=10)).isoformat()
        regions = self._make_regions(
            [
                {
                    "model_id": "recently-expired",
                    "display_name": "Recent",
                    "metadata_raw": f"(EOL: {three_days_ago})",
                },
                {
                    "model_id": "long-expired",
                    "display_name": "Long Expired",
                    "metadata_raw": f"(EOL: {ten_days_ago})",
                },
            ]
        )
        result = mod["check_eol_models"](regions, warning_days=30)
        assert result["expired"][0]["model_id"] == "long-expired"
        assert result["expired"][1]["model_id"] == "recently-expired"

    def test_eol_today_counts_as_approaching(self):
        """A model whose EOL is exactly today is NOT expired (eol_date < today is false)."""
        mod = _load_module()
        today = date.today().isoformat()
        regions = self._make_regions(
            [
                {
                    "model_id": "today-model",
                    "display_name": "Today",
                    "metadata_raw": f"(EOL: {today})",
                }
            ]
        )
        result = mod["check_eol_models"](regions, warning_days=30)
        assert len(result["expired"]) == 0
        assert len(result["approaching"]) == 1

    def test_null_models_field_skipped(self):
        """A region with models: null should be skipped gracefully."""
        mod = _load_module()
        regions = [
            {"region": "broken-region", "status": "available", "models": None},
            {
                "region": "good-region",
                "status": "available",
                "models": [
                    {
                        "model_id": "valid-model",
                        "display_name": "Valid",
                        "metadata_raw": f"(EOL: {(date.today() - timedelta(days=1)).isoformat()})",
                    }
                ],
            },
        ]
        result = mod["check_eol_models"](regions, warning_days=30)
        # The null-models region is skipped; the valid region is still processed.
        assert len(result["expired"]) == 1
        assert result["expired"][0]["model_id"] == "valid-model"


# ---------------------------------------------------------------------------
# build_slack_summary
# ---------------------------------------------------------------------------


class TestBuildSlackSummary:
    def test_empty_results(self):
        mod = _load_module()
        summary = mod["build_slack_summary"]({"expired": [], "approaching": []})
        assert summary == ""

    def test_expired_only(self):
        mod = _load_module()
        results = {
            "expired": [
                {
                    "model_id": "old-model",
                    "display_name": "Old",
                    "eol_date": "2025-01-01",
                    "days_remaining": -5,
                    "region": "us-east-1",
                }
            ],
            "approaching": [],
        }
        summary = mod["build_slack_summary"](results)
        assert ":rotating_light:" in summary
        assert "old-model" in summary
        assert "5 days ago" in summary

    def test_approaching_only(self):
        mod = _load_module()
        results = {
            "expired": [],
            "approaching": [
                {
                    "model_id": "soon-model",
                    "display_name": "Soon",
                    "eol_date": "2026-07-01",
                    "days_remaining": 10,
                    "region": "us-east-1",
                }
            ],
        }
        summary = mod["build_slack_summary"](results)
        assert ":warning:" in summary
        assert "soon-model" in summary
        assert "10 days remaining" in summary

    def test_singular_day(self):
        mod = _load_module()
        results = {
            "expired": [
                {
                    "model_id": "m",
                    "display_name": "M",
                    "eol_date": "2025-01-01",
                    "days_remaining": -1,
                    "region": "r",
                }
            ],
            "approaching": [],
        }
        summary = mod["build_slack_summary"](results)
        assert "1 day ago" in summary


# ---------------------------------------------------------------------------
# fetch_public_models — response validation
# ---------------------------------------------------------------------------


class TestFetchPublicModels:
    def test_raises_on_non_list_response(self):
        mod = _load_module()
        # Simulate API returning a dict instead of a list.
        response_body = json.dumps({"error": "unexpected"}).encode()

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__ = lambda s: BytesIO(response_body)
            mock_urlopen.return_value.__exit__ = lambda s, *a: None
            with pytest.raises(RuntimeError, match="expected a list"):
                mod["fetch_public_models"]("https://api.example.com")

    def test_raises_on_null_response(self):
        mod = _load_module()
        response_body = b"null"

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__ = lambda s: BytesIO(response_body)
            mock_urlopen.return_value.__exit__ = lambda s, *a: None
            with pytest.raises(RuntimeError, match="expected a list"):
                mod["fetch_public_models"]("https://api.example.com")

    def test_succeeds_on_list_response(self):
        mod = _load_module()
        response_body = json.dumps([{"region": "us", "models": []}]).encode()

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__ = lambda s: BytesIO(response_body)
            mock_urlopen.return_value.__exit__ = lambda s, *a: None
            result = mod["fetch_public_models"]("https://api.example.com")
            assert result == [{"region": "us", "models": []}]

    def test_raises_on_invalid_json(self):
        mod = _load_module()
        # Simulate an HTML error page or malformed response.
        response_body = b"<html>502 Bad Gateway</html>"

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__ = lambda s: BytesIO(response_body)
            mock_urlopen.return_value.__exit__ = lambda s, *a: None
            with pytest.raises(RuntimeError, match="Invalid JSON"):
                mod["fetch_public_models"]("https://api.example.com")


# ---------------------------------------------------------------------------
# main — EOL_WARNING_DAYS validation
# ---------------------------------------------------------------------------


class TestMainWarningDaysValidation:
    def test_invalid_warning_days_returns_error(self):
        mod = _load_module()
        with patch.dict(
            "os.environ",
            {"EOL_WARNING_DAYS": "abc", "AMAZEEAI_API_URL": "https://x.example"},
        ):
            exit_code = mod["main"]()
            assert exit_code == 1

    def test_negative_warning_days_returns_error(self):
        mod = _load_module()
        with patch.dict(
            "os.environ",
            {"EOL_WARNING_DAYS": "-5", "AMAZEEAI_API_URL": "https://x.example"},
        ):
            exit_code = mod["main"]()
            assert exit_code == 1


# ---------------------------------------------------------------------------
# Structured eol_date field (issue #638)
# ---------------------------------------------------------------------------


class TestStructuredEolDateField:
    def test_prefers_structured_field_over_metadata_raw(self):
        """eol_date wins; metadata_raw is only a fallback for older backends."""
        mod = _load_module()
        results = mod["check_eol_models"](
            [
                {
                    "region": "test-region",
                    "models": [
                        {
                            "model_id": "claude-3-haiku",
                            "eol_date": "2026-08-01",
                            "metadata_raw": "Stale prose. (EOL: 2026-09-10)",
                        }
                    ],
                }
            ],
            warning_days=100000,
        )
        found = results["expired"] + results["approaching"]
        assert [m["eol_date"] for m in found] == ["2026-08-01"]

    def test_falls_back_to_metadata_raw_when_field_absent(self):
        """A backend that predates eol_date must still be handled."""
        mod = _load_module()
        results = mod["check_eol_models"](
            [
                {
                    "region": "test-region",
                    "models": [
                        {
                            "model_id": "claude-3-haiku",
                            "metadata_raw": "Legacy prose. (EOL: 2026-09-10)",
                        }
                    ],
                }
            ],
            warning_days=100000,
        )
        found = results["expired"] + results["approaching"]
        assert [m["eol_date"] for m in found] == ["2026-09-10"]

    def test_falls_back_when_field_is_null(self):
        """Models with no known EOL date send eol_date: null."""
        mod = _load_module()
        results = mod["check_eol_models"](
            [
                {
                    "region": "test-region",
                    "models": [
                        {
                            "model_id": "claude-3-haiku",
                            "eol_date": None,
                            "metadata_raw": "Prose only. (EOL: 2026-09-10)",
                        }
                    ],
                }
            ],
            warning_days=100000,
        )
        found = results["expired"] + results["approaching"]
        assert [m["eol_date"] for m in found] == ["2026-09-10"]

    def test_model_with_no_eol_anywhere_is_ignored(self):
        mod = _load_module()
        results = mod["check_eol_models"](
            [
                {
                    "region": "test-region",
                    "models": [
                        {
                            "model_id": "claude-opus-5",
                            "eol_date": None,
                            "metadata_raw": "No EOL here.",
                        }
                    ],
                }
            ],
            warning_days=100000,
        )
        assert results == {"expired": [], "approaching": []}

    @pytest.mark.parametrize("value", ["", "   ", "not-a-date", "2026-13-45", 20260910])
    def test_unparseable_structured_field_is_ignored(self, value):
        mod = _load_module()
        assert mod["parse_eol_date"](value) is None
