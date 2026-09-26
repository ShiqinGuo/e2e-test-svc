"""Move personal projects into organizations and shared workspaces without changing their IDs."""

from datetime import datetime, timezone
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "organizations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "memberships",
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organizations.id"), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("user.id"), primary_key=True),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(sa.column("role").in_(["owner", "admin", "member", "viewer"]), name="ck_membership_role"),
    )
    op.create_index("ix_memberships_user_id", "memberships", ["user_id"])
    op.create_table(
        "workspaces",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_workspaces_organization_id", "workspaces", ["organization_id"])
    op.create_table(
        "invitations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("accepted_by", sa.Uuid(), sa.ForeignKey("user.id")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(sa.column("role").in_(["admin", "member", "viewer"]), name="ck_invitation_role"),
        sa.CheckConstraint(sa.column("status").in_(["pending", "accepted", "revoked"]), name="ck_invitation_status"),
    )
    op.create_index("ix_invitations_organization_id", "invitations", ["organization_id"])
    op.create_index(
        "uq_pending_invitation",
        "invitations",
        ["organization_id", "email"],
        unique=True,
        postgresql_where=sa.column("status") == "pending",
    )
    op.add_column("projects", sa.Column("workspace_id", sa.Uuid(), nullable=True))
    op.create_foreign_key("fk_projects_workspace", "projects", "workspaces", ["workspace_id"], ["id"])
    connection = op.get_bind()
    metadata = sa.MetaData()
    projects = sa.Table("projects", metadata, autoload_with=connection)
    users = sa.Table("user", metadata, autoload_with=connection)
    organizations = sa.Table("organizations", metadata, autoload_with=connection)
    workspaces = sa.Table("workspaces", metadata, autoload_with=connection)
    memberships = sa.Table("memberships", metadata, autoload_with=connection)
    owners = connection.execute(
        sa.select(users.c.id, users.c.name).join(projects, projects.c.owner_id == users.c.id).distinct()
    ).all()
    for user_id, name in owners:
        organization_id, workspace_id = uuid4(), uuid4()
        at = datetime.now(timezone.utc)
        connection.execute(
            organizations.insert().values(id=organization_id, name=f"{name[:180]} 的组织", created_at=at, updated_at=at)
        )
        connection.execute(
            workspaces.insert().values(
                id=workspace_id, organization_id=organization_id, name="默认工作区", created_at=at, updated_at=at
            )
        )
        connection.execute(
            memberships.insert().values(organization_id=organization_id, user_id=user_id, role="owner", joined_at=at)
        )
        # JSONB snapshots and child resources are preserved, including immutable versions.
        fields = sa.cast(
            {"workspaceId": str(workspace_id), "organizationId": str(organization_id), "createdBy": str(user_id)},
            JSONB(),
        )
        connection.execute(
            projects.update()
            .where(projects.c.owner_id == user_id)
            .values(workspace_id=workspace_id, body=projects.c.body.op("-")("ownerId").op("||")(fields))
        )
    op.alter_column("projects", "workspace_id", nullable=False)
    resources = sa.Table("resources", metadata, autoload_with=connection)
    creator = sa.select(projects.c.owner_id).where(projects.c.id == resources.c.project_id).scalar_subquery()
    connection.execute(
        resources.update()
        .where(resources.c.kind == "recording")
        .values(body=resources.c.body.op("||")(sa.func.jsonb_build_object("createdBy", sa.cast(creator, sa.String()))))
    )
    op.alter_column("projects", "owner_id", new_column_name="created_by")
    op.drop_index("ix_projects_owner_id", table_name="projects")
    op.create_index("ix_projects_created_by", "projects", ["created_by"])
    op.create_index("ix_projects_workspace_id", "projects", ["workspace_id"])


def downgrade():
    raise RuntimeError(
        "Organization memberships cannot be represented by the personal-project schema; restore the pre-migration backup to revert"
    )
