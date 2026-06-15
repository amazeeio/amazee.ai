import os
from alembic.config import Config
from alembic.script import ScriptDirectory


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


# End of file
