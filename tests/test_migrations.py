import os
from collections import defaultdict

from alembic.config import Config
from alembic.script import ScriptDirectory

# ponytail: the only fork we ever had (Feb 2025, merged by 20250219_merge_heads).
# Grandfathered so the linearity test can guard everything after it.
LEGACY_FORK_POINTS = {"00c5de5fd13f"}
LEGACY_MERGES = {"20250219_merge_heads"}


# Helper to get the alembic config
def get_alembic_config():
    config_path = os.path.join(
        os.path.dirname(__file__), "..", "app", "migrations", "alembic.ini"
    )
    cfg = Config(config_path)
    cfg.set_main_option(
        "script_location",
        os.path.join(os.path.dirname(__file__), "..", "app", "migrations"),
    )
    return cfg


def test_single_alembic_head():
    """Verify that there are no split heads/branches in the Alembic migration history."""
    cfg = get_alembic_config()
    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()
    assert len(heads) == 1, (
        f"Alembic detected multiple head revisions: {heads}. "
        "Please resolve split heads by creating a merge migration or linearizing your changes."
    )


def test_migration_history_is_linear():
    """No forks: every revision has at most one parent and at most one child.

    ``test_single_alembic_head`` only catches forks that are still open. A fork
    closed with a merge revision passes it, but still means two branches were
    developed in parallel against the same parent. Reject both.
    """
    script = ScriptDirectory.from_config(get_alembic_config())

    children = defaultdict(list)
    merges = []
    for rev in script.walk_revisions():
        parents = rev.down_revision
        parents = parents if isinstance(parents, tuple) else (parents,)
        if len(parents) > 1 and rev.revision not in LEGACY_MERGES:
            merges.append(rev.revision)
        for parent in filter(None, parents):
            children[parent].append(rev.revision)

    forks = {
        parent: revs
        for parent, revs in children.items()
        if len(revs) > 1 and parent not in LEGACY_FORK_POINTS
    }

    assert not forks, (
        f"Alembic migration history forks: {forks}. Rebase your migration onto the "
        "current head (`alembic heads`) and update its down_revision instead of "
        "branching off an older revision."
    )
    assert not merges, (
        f"Merge revisions found: {merges}. Keep migration history linear — rebase "
        "onto the current head rather than merging two branches."
    )


# End of file
