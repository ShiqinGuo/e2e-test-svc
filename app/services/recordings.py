import asyncio
import copy
import uuid
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import schemas as s
from ..context import Context
from ..domain import Permission
from ..errors import APIError, ErrorCode
from ..models import Project, Resource, User, utcnow
from ..presenters import public_recording
from ..runtime import RecordingResult
from ..security import redact, secrets_of
from ..services.environments import env_data, validate_role
from ..store import create_resource, project_for, resource_for, timestamp, update_resource


async def start_recording(context, ident):
    env = {}
    try:
        async with context.db.sessions() as session:
            value = await session.get(Resource, ident)
            body = copy.deepcopy(value.body)
        env = context.vault.open(body["encryptedEnvironment"])
        directory = context.settings.data_dir / "recordings" / str(ident)
        await asyncio.to_thread(directory.mkdir, parents=True, exist_ok=True)
        await context.runtime.recording_start(
            {
                "id": str(ident),
                "workDir": str(directory.resolve()),
                "url": env["websites"][body["website"]],
                "website": body["website"],
                "environment": env,
                "role": body["role"],
                "expiresAt": body["expiresAt"],
                "platformOrigins": context.settings.trusted_origins,
            }
        )
        async with context.db.sessions() as session:
            value = await session.get(Resource, ident, with_for_update=True)
            if value.body["status"] == "starting":
                update_resource(
                    value,
                    {
                        "status": "ready",
                        "viewerUrl": f"/api/v1/projects/{value.project_id}/recordings/{ident}/viewer/index.html",
                    },
                )
                await session.commit()
            else:
                await session.rollback()
                await context.runtime.recording_stop(str(ident))
    except Exception as exc:
        async with context.db.sessions() as session:
            value = await session.get(Resource, ident, with_for_update=True)
            if value.body["status"] in {"starting", "ready"}:
                update_resource(
                    value, {"status": "error", "error": redact(str(exc), secrets_of(env))[:2000], "viewerUrl": None}
                )
                await session.commit()


async def finish_recording(context, session, value):
    # A start must settle before stop can flush its controller. Never wait with a DB lock.
    if value.body["status"] == "starting":
        task = context.jobs.tasks[str(value.id)]
        await session.commit()
        await asyncio.shield(task)
        value = await session.get(Resource, value.id, with_for_update=True, populate_existing=True)
    if value.body["status"] != "ready":
        await session.commit()
        return
    environment = context.vault.open(value.body["encryptedEnvironment"])
    update_resource(value, {"status": "stopping", "viewerUrl": None})
    await session.commit()

    async def flush_and_publish():
        try:
            raw = await context.runtime.recording_stop(str(value.id))
            result = RecordingResult.model_validate(raw).model_dump(mode="json")
            changes = {**redact(result, secrets_of(environment)), "status": "stopped", "viewerUrl": None}
        except Exception as exc:
            # Task boundary: retain the actual failure, including a malformed controller response.
            changes = {"status": "error", "error": redact(str(exc), secrets_of(environment))[:2000], "viewerUrl": None}
        async with context.db.sessions() as writer:
            current = await writer.get(Resource, value.id, with_for_update=True)
            update_resource(current, changes)
            await writer.commit()

    task = context.jobs.track(f"stop:{value.id}", flush_and_publish())
    await asyncio.shield(task)
    await session.refresh(value)


async def expire_recordings(context):
    while True:
        await asyncio.sleep(15)
        async with context.db.sessions() as session:
            values = (
                await session.scalars(
                    select(Resource).where(
                        Resource.kind == "recording", Resource.body["status"].astext.in_(["starting", "ready"])
                    )
                )
            ).all()
            for value in values:
                if value.body["expiresAt"] <= timestamp():
                    locked = await session.get(Resource, value.id, with_for_update=True, populate_existing=True)
                    await finish_recording(context, session, locked)


async def create_recording(
    projectId: uuid.UUID, body: s.RecordingInput, session: AsyncSession, context: Context, user: User
):
    env = env_data(context, await resource_for(session, "environment", projectId, body.environmentId))
    if body.scenarioId:
        await resource_for(session, "scenario", projectId, body.scenarioId)
    validate_role(env, body.role)
    if body.website not in env["websites"]:
        raise APIError(ErrorCode.INVALID_WEBSITE)
    await session.commit()
    capability = await context.runtime.available()
    if not capability["recorder"]["available"]:
        raise APIError(
            ErrorCode.RUNTIME_UNAVAILABLE, message=capability["recorder"].get("reason", "Recorder unavailable")
        )
    await project_for(session, projectId, user.id, Permission.EDIT)
    await session.execute(select(User).where(User.id == user.id).with_for_update())
    active = await session.scalar(
        select(func.count())
        .select_from(Resource)
        .join(Project, Resource.project_id == Project.id)
        .where(
            Resource.kind == "recording",
            Resource.body["createdBy"].astext == str(user.id),
            Resource.body["status"].astext.in_(["starting", "ready", "stopping"]),
        )
    )
    if active >= context.settings.max_recordings_per_user:
        raise APIError(ErrorCode.RECORDING_LIMIT)
    value = create_resource(
        session,
        "recording",
        projectId,
        {
            **body.model_dump(mode="json"),
            "createdBy": str(user.id),
            "status": "starting",
            "expiresAt": (utcnow() + timedelta(seconds=context.settings.recording_lifetime)).isoformat(),
            "viewerUrl": None,
            "code": "",
            "checks": [],
            "error": None,
            "encryptedEnvironment": context.vault.seal(env),
        },
    )
    await session.commit()
    context.jobs.track(str(value.id), start_recording(context, value.id))
    return public_recording(value.body)


async def get_recording(projectId: uuid.UUID, recordingId: uuid.UUID, session: AsyncSession, context: Context):
    value = await resource_for(session, "recording", projectId, recordingId)
    if value.body["status"] == "ready":
        environment = context.vault.open(value.body["encryptedEnvironment"])
        # Do not hold the DB lock while codegen responds. Re-read under lock before publishing the poll.
        await session.commit()
        error = None
        try:
            raw = await context.runtime.recording_read(str(recordingId))
            result = RecordingResult.model_validate(raw).model_dump(mode="json")
            target = await context.runtime.recording_target(str(recordingId))
            if not target:
                raise RuntimeError("Recorder is no longer running")
        except Exception as exc:
            error = redact(str(exc), secrets_of(environment))[:2000]
        value = await resource_for(session, "recording", projectId, recordingId, lock=True)
        if value.body["status"] == "ready":
            if error:
                update_resource(value, {"status": "error", "error": error, "viewerUrl": None})
            else:
                update_resource(value, redact(result, secrets_of(environment)))
        await session.commit()
    return public_recording(value.body)
