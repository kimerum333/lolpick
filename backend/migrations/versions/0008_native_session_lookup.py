"""Current baseline for empty databases or already-current 0008 installations.

Existing databases at this revision run no upgrade DDL. Earlier conversion
revisions are kept exclusively in the separate maintenance backup.
"""
from alembic import op
from app.db.ddl import install_sql, native_session_sql

revision = "0008_native_session_lookup"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.execute(install_sql())
    op.execute(native_session_sql())
    op.execute("ALTER TABLE lolpick.lolpick_alembic_version ENABLE ROW LEVEL SECURITY")


def downgrade():
    raise RuntimeError("Current financial and authentication identities cannot be downgraded safely.")
