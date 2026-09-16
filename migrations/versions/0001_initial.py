"""Initial PostgreSQL platform and FastAPI Users schema."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "user",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("hashed_password", sa.String(1024), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("is_superuser", sa.Boolean(), nullable=False),
        sa.Column("is_verified", sa.Boolean(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
    )
    op.create_index("ix_user_email", "user", ["email"], unique=True)
    op.create_table(
        "accesstoken",
        sa.Column("token", sa.String(43), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("user.id", ondelete="cascade"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_accesstoken_created_at", "accesstoken", ["created_at"])
    op.create_table(
        "projects",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("body", pg.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_projects_owner_id", "projects", ["owner_id"])
    op.create_table(
        "resources",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("parent_id", sa.Uuid(), sa.ForeignKey("resources.id")),
        sa.Column("version_number", sa.Integer()),
        sa.Column("body", pg.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("parent_id", "version_number", name="uq_scenario_version"),
    )
    op.create_index("ix_resources_project_kind", "resources", ["project_id", "kind"])
    op.create_index("ix_resources_parent_id", "resources", ["parent_id"])
    op.execute("""CREATE FUNCTION forbid_version_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN IF OLD.kind='version' THEN RAISE EXCEPTION 'Scenario versions are immutable'; END IF;
      IF TG_OP='DELETE' THEN RETURN OLD; END IF; RETURN NEW; END $$""")
    op.execute(
        "CREATE TRIGGER immutable_versions BEFORE UPDATE OR DELETE ON resources FOR EACH ROW EXECUTE FUNCTION forbid_version_mutation()"
    )
    op.create_table(
        "run_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("resources.id"), nullable=False),
        sa.Column("type", sa.String(100), nullable=False),
        sa.Column("timestamp", sa.String(40), nullable=False),
        sa.Column("data", pg.JSONB(), nullable=True),
    )
    op.create_index("ix_run_events_run_id", "run_events", ["run_id"])
    op.create_table(
        "artifacts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("resources.id"), nullable=False),
        sa.Column("body", pg.JSONB(), nullable=False),
    )
    op.create_index("ix_artifacts_run_id", "artifacts", ["run_id"])
    op.create_table(
        "auth_attempts",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    for table in ("auth_attempts", "artifacts", "run_events"):
        op.drop_table(table)
    op.execute("DROP TRIGGER immutable_versions ON resources")
    op.execute("DROP FUNCTION forbid_version_mutation()")
    for table in ("resources", "projects", "accesstoken", "user"):
        op.drop_table(table)
