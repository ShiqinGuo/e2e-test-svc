"""Constrain resource lifecycles and remove the redundant run source field."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

resources = sa.table("resources", sa.column("kind", sa.String()), sa.column("body", JSONB()))
kind = resources.c.kind
status = resources.c.body["status"].astext


def upgrade():
    op.execute(sa.update(resources).where(kind == "run").values(body=resources.c.body.op("-")("sourceRunId")))
    op.create_check_constraint(
        "ck_resource_kind", "resources", kind.in_(["group", "scenario", "version", "environment", "run", "recording"])
    )
    op.create_check_constraint(
        "ck_run_status",
        "resources",
        sa.or_(
            kind != "run",
            sa.and_(
                status.is_not(None),
                status.in_(["queued", "running", "passed", "failed", "cancelled", "timed_out", "error"]),
            ),
        ),
    )
    op.create_check_constraint(
        "ck_recording_status",
        "resources",
        sa.or_(
            kind != "recording",
            sa.and_(status.is_not(None), status.in_(["starting", "ready", "stopping", "stopped", "error"])),
        ),
    )


def downgrade():
    # An in-flight stop cannot be represented by the old controller.
    active = op.get_bind().scalar(
        sa.select(sa.func.count()).select_from(resources).where(kind == "recording", status == "stopping")
    )
    if active:
        raise RuntimeError("Finish active recording stops before downgrading")
    for name in ("ck_recording_status", "ck_run_status", "ck_resource_kind"):
        op.drop_constraint(name, "resources", type_="check")
    op.execute(
        sa.update(resources)
        .where(kind == "run", resources.c.body["rerunOf"].astext.is_not(None))
        .values(body=resources.c.body.op("||")(sa.func.jsonb_build_object("sourceRunId", resources.c.body["rerunOf"])))
    )
