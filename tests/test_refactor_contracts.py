"""Regression boundaries introduced by the architecture refactor, using PostgreSQL."""

import asyncio
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from test_api import seed

from app import observability
from app.models import Resource
from app.store import create_resource, update_resource

pytest_plugins = ("test_api",)


@pytest.mark.asyncio
@pytest.mark.parametrize("database_settings", ["0001"], indirect=True)
async def test_existing_run_snapshot_survives_upgrade(database_settings):
    from uuid import uuid4

    import sqlalchemy as sa
    from sqlalchemy.dialects.postgresql import JSONB

    from app.db import Database
    from app.migrations import migrate
    from app.models import Membership, Project, User, Workspace, utcnow

    database = Database(database_settings)
    try:
        async with database.sessions() as session:
            user = User(id=uuid4(), name="migration", email="migration@example.com", hashed_password="fixture")
            session.add(user)
            await session.flush()
            project_id = uuid4()
            projects = sa.table(
                "projects",
                sa.column("id", sa.Uuid()),
                sa.column("owner_id", sa.Uuid()),
                sa.column("body", JSONB()),
                sa.column("created_at", sa.DateTime(timezone=True)),
            )
            await session.execute(
                projects.insert().values(
                    id=project_id,
                    owner_id=user.id,
                    body={"name": "migration", "ownerId": str(user.id)},
                    created_at=utcnow(),
                )
            )
            source = str(uuid4())
            run = create_resource(
                session,
                "run",
                project_id,
                {
                    "status": "passed",
                    "rerunOf": source,
                    "sourceRunId": source,
                    "encryptedEnvironment": "opaque-existing-ciphertext",
                    "versions": [{"code": "original"}],
                },
            )
            ident = run.id
            await session.commit()
        await asyncio.to_thread(migrate, database_settings)
        await database.engine.dispose()
        async with database.sessions() as session:
            run = await session.get(Resource, ident)
            assert run.body["rerunOf"] == source
            assert "sourceRunId" not in run.body
            assert run.body["encryptedEnvironment"] == "opaque-existing-ciphertext"
            assert run.body["versions"] == [{"code": "original"}]
            project = await session.get(Project, project_id)
            workspace = await session.get(Workspace, project.workspace_id)
            membership = await session.get(Membership, (workspace.organization_id, project.created_by))
            assert membership.role == "owner"
            assert "ownerId" not in project.body
            assert project.body["workspaceId"] == str(workspace.id)
            assert project.body["organizationId"] == str(workspace.organization_id)

    finally:
        await database.close()


@pytest.mark.asyncio
async def test_invalid_runtime_result_is_an_explicit_error(api):
    _, owner, _, runtime = api
    resources = await seed(owner)
    created = await owner.post(
        resources.path + "/runs",
        json={
            "scenarioId": resources.scenario["id"],
            "environmentId": resources.environment["id"],
        },
    )
    run = created.json()
    await runtime.wait_started(run["id"])
    runtime.pending[run["id"]].set_result({"status": "passed", "artifacts": []})
    async with asyncio.timeout(5):
        while (result := (await owner.get(resources.path + "/runs/" + run["id"])).json())["status"] in {
            "queued",
            "running",
        }:
            await asyncio.sleep(0.01)
    assert result["status"] == "error"
    assert result["verification"] == "pending"
    assert "verification" in result["error"]
    assert "summary" in result["error"]


@pytest.mark.asyncio
async def test_business_events_require_commit_and_logging_failure_does_not_rollback(api, monkeypatch):
    _, owner, _, runtime = api
    resources = await seed(owner)
    emitted = []
    original = observability.emit
    monkeypatch.setattr(observability, "emit", lambda name, **fields: emitted.append((name, fields)))
    async with runtime.app.state.context.db.sessions() as session:
        create_resource(session, "group", UUID(resources.project["id"]), {"name": "rolled back", "description": ""})
        await session.flush()
        assert not emitted
        await session.rollback()
        assert not emitted
        saved = create_resource(
            session, "group", UUID(resources.project["id"]), {"name": "committed", "description": ""}
        )
        await session.commit()
        assert [fields["resourceId"] for _, fields in emitted] == [str(saved.id)]
    monkeypatch.setattr(observability, "emit", original)

    def failed_sink(*args, **kwargs):
        raise OSError("log sink unavailable")

    monkeypatch.setattr(observability.logger, "info", failed_sink)
    response = await owner.post(resources.path + "/groups", json={"name": "saved despite logging failure"})
    assert response.status_code == 201
    assert (await owner.get(resources.path + "/groups/" + response.json()["id"])).status_code == 200


@pytest.mark.asyncio
async def test_database_rejects_invalid_lifecycle_even_outside_repository(api):
    _, owner, _, runtime = api
    resources = await seed(owner)
    async with runtime.app.state.context.db.sessions() as session:
        row = create_resource(session, "recording", UUID(resources.project["id"]), {"status": "ready"})
        await session.commit()
        ident = row.id
        row.body = {**row.body, "status": "invented"}
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()
        current = await session.scalar(select(Resource).where(Resource.id == ident))
        assert current.body["status"] == "ready"
        with pytest.raises(ValueError, match="Illegal recording transition"):
            update_resource(current, {"status": "stopped"})
