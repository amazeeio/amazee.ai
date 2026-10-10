from datetime import date

import pytest

from app.registry.discovery import run_discovery
from app.registry.family import apply_family, family_of, manufacturer_of, run_family
from app.registry.models import DBRegistryModel, DBRegistryProvider, DBRegistryRun

TODAY = date(2026, 10, 10)


def _model(db, provider_name, model_id, family=None, manufacturer=None):
    provider = db.query(DBRegistryProvider).filter_by(name=provider_name).first()
    if provider is None:
        provider = DBRegistryProvider(name=provider_name)
        db.add(provider)
        db.flush()
    row = DBRegistryModel(
        provider_id=provider.id, model_id=model_id, supports=[], source="litellm", prices={},
        family=family, manufacturer=manufacturer, first_seen=TODAY, last_seen=TODAY,
    )
    db.add(row)
    db.flush()
    return row


@pytest.mark.parametrize(
    ("provider", "model_id", "family"),
    [
        ("bedrock", "anthropic.claude-sonnet-4-6", "claudesonnet46"),
        ("vertex_ai", "claude-sonnet-4-6@default", "claudesonnet46"),
        ("deepinfra", "anthropic/claude-sonnet-4-6", "claudesonnet46"),
        ("bedrock", "anthropic.claude-sonnet-4-5-20250929-v1:0", "claudesonnet45"),
        ("vertex_ai", "claude-sonnet-4-5@20250929", "claudesonnet45"),
        ("bedrock", "openai.gpt-oss-120b-1:0", "gptoss120b"),
        ("bedrock_mantle", "openai.gpt-oss-120b", "gptoss120b"),
        ("vertex_ai", "openai/gpt-oss-120b-maas", "gptoss120b"),
        ("deepinfra", "openai/gpt-oss-120b", "gptoss120b"),
        ("deepinfra", "meta-llama/Llama-3.3-70B-Instruct", "llama3370binstruct"),
        ("vertex_ai", "meta/llama-3.3-70b-instruct-maas", "llama3370binstruct"),
        ("deepinfra", "deepseek-ai/DeepSeek-R1-0528", "deepseekr10528"),
        ("vertex_ai", "deepseek-ai/deepseek-r1-0528-maas", "deepseekr10528"),
        ("bedrock", "amazon.nova-pro-v1:0:300k", "novapro"),
        ("bedrock", "mistral.mistral-large-2407-v1:0", "mistrallarge2407"),
        ("vertex_ai", "mistral-large@2407", "mistrallarge2407"),
        ("openai", "gpt-4.1", "gpt41"),
        # Rows that must not join their neighbors.
        ("deepinfra", "meta-llama/Llama-3.3-70B-Instruct-Turbo", "llama3370binstructturbo"),
        ("bedrock", "mistral.mistral-large-2402-v1:0", "mistrallarge2402"),
        ("vertex_ai", "mistral-large@latest", "mistrallarge"),
        ("vertex_ai", "mistral-large@2411-001", "mistrallarge2411"),
        ("vertex_ai", "mistral-large-2411", "mistrallarge2411"),
        ("openai", "low/1024-x-1024/gpt-image-1", "gptimage1"),
        ("openai", "gpt-4o-2024-08-06", "gpt4o"),
        ("openai", "high/1024-x-1024/gpt-image-1.5-2025-12-16", "gptimage15"),
        ("openai", "1024-x-1024/gpt-image-1.5", "gptimage15"),
        ("azure", "global-standard/gpt-4o-2024-08-06", "gpt4o"),
        # Only Bedrock version 1 goes; a later version is another model.
        ("bedrock", "anthropic.claude-3-5-sonnet-20240620-v1:0", "claude35sonnet"),
        ("bedrock", "anthropic.claude-3-5-sonnet-20241022-v2:0", "claude35sonnetv2"),
        ("bedrock", "cohere.embed-v4:0", "embedv4"),
        ("bedrock", "cohere.rerank-v3-5:0", "rerankv35"),
        ("bedrock", "amazon.titan-embed-text-v2:0", "titanembedtextv2"),
    ],
)
def test_family_of(provider, model_id, family):
    assert family_of(provider, model_id) == family


@pytest.mark.parametrize(
    ("provider", "model_id", "manufacturer"),
    [
        ("bedrock", "anthropic.claude-sonnet-4-6", "anthropic"),
        ("deepinfra", "Qwen/Qwen3-235B-A22B-Instruct-2507", "qwen"),
        ("deepinfra", "meta-llama/Llama-3.3-70B-Instruct", "meta"),
        ("vertex_ai", "deepseek-ai/deepseek-r1-0528-maas", "deepseek"),
        ("deepinfra", "mistralai/Mistral-Small-24B-Instruct-2501", "mistral"),
        ("bedrock", "moonshotai.kimi-k2.5", "moonshot"),
        ("deepinfra", "MiniMaxAI/MiniMax-M2.7", "minimax"),
        ("deepinfra", "zai-org/GLM-4.5", "zai"),
        ("vertex_ai", "claude-sonnet-4-6@default", None),
        ("openai", "gpt-4.1", None),
        ("openai", "low/1024-x-1024/gpt-image-1", None),
        ("openai", "1024-x-1024/gpt-image-1.5", None),
        ("azure", "us-gov/gpt-5.1", None),
        ("azure", "global-standard/gpt-4o-2024-08-06", None),
    ],
)
def test_manufacturer_of(provider, model_id, manufacturer):
    assert manufacturer_of(provider, model_id) == manufacturer


def test_fills_empty_values_keeps_set_ones_and_borrows_the_family_manufacturer(registry_db):
    bedrock = _model(registry_db, "bedrock", "anthropic.claude-sonnet-4-6")
    vertex = _model(registry_db, "vertex_ai", "claude-sonnet-4-6@default")
    gpt = _model(registry_db, "openai", "gpt-4.1", family="manual")
    oss = _model(registry_db, "deepinfra", "openai/gpt-oss-120b", manufacturer="manual-maker")

    stats = apply_family(registry_db)

    assert (bedrock.family, bedrock.manufacturer) == ("claudesonnet46", "anthropic")
    assert (vertex.family, vertex.manufacturer) == ("claudesonnet46", "anthropic")
    assert (gpt.family, gpt.manufacturer) == ("manual", None)
    assert (oss.family, oss.manufacturer) == ("gptoss120b", "manual-maker")
    assert stats == {"models": 4, "families": 3, "manufacturers": 2}


def test_no_borrow_when_the_family_has_two_manufacturers(registry_db):
    _model(registry_db, "bedrock", "anthropic.claude-sonnet-4-6")
    other = _model(registry_db, "deepinfra", "anthropic/claude-sonnet-4-6", manufacturer="other")
    vertex = _model(registry_db, "vertex_ai", "claude-sonnet-4-6@default")

    apply_family(registry_db)

    assert vertex.manufacturer is None
    assert other.manufacturer == "other"


def test_second_run_fills_nothing(registry_db):
    _model(registry_db, "bedrock", "anthropic.claude-sonnet-4-6")
    vertex = _model(registry_db, "vertex_ai", "claude-sonnet-4-6@default")
    registry_db.commit()

    first = run_family(registry_db)
    registry_db.commit()
    updated_at = vertex.updated_at
    second = run_family(registry_db)
    registry_db.commit()

    assert first["families"] == 2 and first["manufacturers"] == 2
    assert second["families"] == 0 and second["manufacturers"] == 0
    registry_db.refresh(vertex)
    assert vertex.updated_at == updated_at
    runs = registry_db.query(DBRegistryRun).filter_by(step="family").all()
    assert len(runs) == 2 and all(run.status == "ok" for run in runs)


def test_daily_job_runs_family_right_after_the_model_list():
    from scripts import trigger_registry_daily_job

    steps = [step for _, step in trigger_registry_daily_job.STEPS]
    assert steps.index(run_family) == steps.index(run_discovery) + 1
