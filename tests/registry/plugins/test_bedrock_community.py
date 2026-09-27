import pytest

from app.registry.plugins.parsers import bedrock_community
from app.registry.plugins.runner import validate

CATALOG = [
    {
        "modelId": "anthropic.claude-sonnet-4-20250514-v1:0",
        "inferenceTypesSupported": ["INFERENCE_PROFILE"],
        "modelLifecycle": {
            "status": "LEGACY",
            "startOfLifeTime": "2025-05-14 00:00:00+00:00",
            "legacyTime": "2026-04-14 00:00:00+00:00",
            "publicExtendedAccessTime": "not a date",
            "endOfLifeTime": "2026-10-14 00:00:00+00:00",
        },
        "regions": ["us-east-1", "eu-central-1"],
        "modelCard": {"mantleRegions": ["us-west-2"], "modelEolDate": "October 2026"},
    },
    {
        # Mantle only, no modelArn and no model card.
        "modelId": "anthropic.claude-haiku-4-5",
        "mantleOnly": True,
        "inferenceTypesSupported": ["ON_DEMAND"],
        "modelLifecycle": {"status": "ACTIVE", "startOfLifeTime": "2025-10-01"},
        "regions": ["us-east-1"],
    },
]


def test_transform_splits_bedrock_and_mantle():
    out = bedrock_community.transform(CATALOG)
    models = {(m["provider"], m["model_id"]): m for m in out["models"]}
    sonnet = "anthropic.claude-sonnet-4-20250514-v1:0"

    assert set(models) == {
        ("bedrock", sonnet), ("bedrock_mantle", sonnet), ("bedrock_mantle", "anthropic.claude-haiku-4-5"),
    }
    assert models[("bedrock", sonnet)]["regions"] == [
        {"cloud_region": "eu-central-1", "call_types": ["INFERENCE_PROFILE"]},
        {"cloud_region": "us-east-1", "call_types": ["INFERENCE_PROFILE"]},
    ]
    # Bedrock's call types do not apply to Mantle.
    assert models[("bedrock_mantle", sonnet)]["regions"] == [{"cloud_region": "us-west-2", "call_types": []}]
    life = models[("bedrock", sonnet)]["lifecycle"]
    assert life["legacy_at"] == "2026-04-14" and life["eol_date"] == "2026-10-14"
    # A bad date drops that date, not the source.
    assert life["extended_access_until"] is None
    # The output passes the runner's checks as it is.
    assert len(validate(out, "bedrock_community")) == 3


@pytest.mark.parametrize("bad", [{"not": "a list"}, [{"regions": []}]])
def test_transform_rejects_bad_payload(bad):
    with pytest.raises(ValueError):
        bedrock_community.transform(bad)


@pytest.mark.parametrize("field", ["regions", "inferenceTypesSupported"])
def test_transform_rejects_a_string_where_a_list_belongs(field):
    row = dict(CATALOG[0], **{field: "us-east-1"})
    with pytest.raises(ValueError, match="not a list"):
        bedrock_community.transform([row])
