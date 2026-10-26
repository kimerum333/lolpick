"""Concurrent seasons and file-backed audit context; preserve all user data."""
from pathlib import Path
from alembic import op

revision = "0010_concurrent_seasons"
down_revision = "0009_user_actions"
branch_labels = None
depends_on = None


def upgrade():
    op.execute((Path(__file__).resolve().parents[2] / "app/db/concurrent_seasons.sql").read_text(encoding="utf-8"))


def downgrade():
    raise RuntimeError("Concurrent season balances and recorded audit routes cannot be downgraded safely.")
