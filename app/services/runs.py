import copy
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import schemas as s
from ..context import Context
from ..domain import Permission
from ..errors import APIError, ErrorCode
from ..jobs import SUMMARY, TERMINAL
from ..models import Resource, RunEvent
from ..presenters import public_run
from ..security import public_environment
from ..services.environments import env_data, validate_role
from ..store import create_resource, project_for, resource_for, timestamp, update_resource


async def create_run(
    projectId: uuid.UUID, body: s.RunInput, session: AsyncSession, context: Context, user_id: uuid.UUID
):
    env = env_data(context, await resource_for(session, "environment", projectId, body.environmentId))
    validate_role(env, body.role)
    snapshots = []
    if body.scenarioId:
        scenarios = [await resource_for(session, "scenario", projectId, body.scenarioId)]
    else:
        await resource_for(session, "group", projectId, body.groupId)
        scenarios = (
            await session.scalars(
                select(Resource)
                .where(
                    Resource.kind == "scenario",
                    Resource.project_id == projectId,
                    Resource.parent_id == body.groupId,
                )
                .order_by(Resource.created_at)
            )
        ).all()
    if not scenarios:
        raise APIError(ErrorCode.EMPTY_GROUP)
    for scenario in scenarios:
        version_id = body.versionId or scenario.body["currentVersionId"]
        if not version_id:
            raise APIError(ErrorCode.MISSING_VERSION)
        snapshots.append(
            (await resource_for(session, "version", projectId, uuid.UUID(str(version_id)), parent_id=scenario.id)).body
        )
    await session.commit()
    capability = await context.runtime.available()
    if not capability["runner"]["available"]:
        raise APIError(ErrorCode.RUNTIME_UNAVAILABLE, message=capability["runner"].get("reason", "Runner unavailable"))
    await project_for(session, projectId, user_id, Permission.EDIT)
    value = create_resource(
        session,
        "run",
        projectId,
        {
            **body.model_dump(mode="json", exclude={"versionId"}),
            "versions": snapshots,
            "environmentSnapshot": public_environment(env),
            "encryptedEnvironment": context.vault.seal(env),
            "status": "queued",
            "verification": "pending",
            "summary": dict(SUMMARY),
            "startedAt": None,
            "finishedAt": None,
            "error": None,
        },
    )
    await session.flush()
    session.add(RunEvent(run_id=value.id, type="run.queued", timestamp=timestamp(), data={"runId": str(value.id)}))
    await session.commit()
    context.jobs.schedule(value.id)
    return public_run(value.body)


async def rerun(projectId: uuid.UUID, runId: uuid.UUID, session: AsyncSession, context: Context, user_id: uuid.UUID):
    original = await resource_for(session, "run", projectId, runId)
    await session.commit()
    capability = await context.runtime.available()
    if not capability["runner"]["available"]:
        raise APIError(ErrorCode.RUNTIME_UNAVAILABLE, message=capability["runner"].get("reason", "Runner unavailable"))
    body = {k: copy.deepcopy(v) for k, v in original.body.items() if k not in {"id", "createdAt", "updatedAt"}}
    body.update(
        {
            "status": "queued",
            "verification": "pending",
            "summary": dict(SUMMARY),
            "startedAt": None,
            "finishedAt": None,
            "error": None,
            "rerunOf": str(runId),
        }
    )
    await project_for(session, projectId, user_id, Permission.EDIT)
    value = create_resource(session, "run", projectId, body)
    await session.flush()
    session.add(
        RunEvent(
            run_id=value.id,
            type="run.queued",
            timestamp=timestamp(),
            data={"runId": str(value.id), "rerunOf": str(runId)},
        )
    )
    await session.commit()
    context.jobs.schedule(value.id)
    return public_run(value.body)


async def cancel_run(
    projectId: uuid.UUID, runId: uuid.UUID, session: AsyncSession, context: Context, user_id: uuid.UUID
):
    value = await resource_for(session, "run", projectId, runId, lock=True)
    if value.body["status"] not in TERMINAL:
        update_resource(value, {"status": "cancelled", "finishedAt": timestamp()})
        session.add(RunEvent(run_id=runId, type="run.cancelled", timestamp=timestamp(), data={"runId": str(runId)}))
        await session.commit()
        await context.runtime.cancel(str(runId))
    return public_run(value.body)
