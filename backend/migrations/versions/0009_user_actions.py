"""Add authenticated cheer/bet route identifiers without changing user data."""
from pathlib import Path
from alembic import op

revision = "0009_user_actions"
down_revision = "0008_native_session_lookup"
branch_labels = None
depends_on = None


def upgrade():
    op.execute((Path(__file__).resolve().parents[2] / "app/db/user_actions.sql").read_text(encoding="utf-8"))


def downgrade():
    raise RuntimeError("Recorded action route identities cannot be downgraded safely.")
