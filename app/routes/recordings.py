import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from .. import responses as out
from .. import schemas as s
from ..auth import current_user
from ..context import Context, get_context
from ..db import get_session
from ..domain import Permission
from ..errors import APIError, ErrorCode
from ..models import User
from ..presenters import public_recording
from ..services import recordings as workflows
from ..services.recordings import finish_recording
from ..services.versions import add_version
from ..store import list_resources, project_for, resource_for

P = "/api/v1/projects/{projectId}"
router = APIRouter()


@router.get(P + "/recordings", tags=["Recordings"], response_model=out.Page[out.RecordingView])
async def recordings(
    projectId: uuid.UUID,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    await project_for(session, projectId, user.id)
    result = await list_resources(session, "recording", projectId, limit, offset)
    result["items"] = [public_recording(v) for v in result["items"]]
    return result


@router.post(P + "/recordings", status_code=202, tags=["Recordings"], response_model=out.RecordingView)
async def create_recording(
    projectId: uuid.UUID,
    body: s.RecordingInput,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    context: Context = Depends(get_context),
):
    await project_for(session, projectId, user.id, Permission.EDIT)
    return await workflows.create_recording(projectId, body, session, context, user)


@router.get(P + "/recordings/{recordingId}", tags=["Recordings"], response_model=out.RecordingView)
async def get_recording(
    projectId: uuid.UUID,
    recordingId: uuid.UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    context: Context = Depends(get_context),
):
    await project_for(session, projectId, user.id)
    return await workflows.get_recording(projectId, recordingId, session, context)


@router.post(P + "/recordings/{recordingId}/stop", tags=["Recordings"], response_model=out.RecordingView)
async def stop_recording(
    projectId: uuid.UUID,
    recordingId: uuid.UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    context: Context = Depends(get_context),
):
    await project_for(session, projectId, user.id, Permission.EDIT)
    value = await resource_for(session, "recording", projectId, recordingId, lock=True)
    await finish_recording(context, session, value)
    return public_recording(value.body)


@router.post(P + "/recordings/{recordingId}/save", status_code=201, tags=["Recordings"], response_model=out.VersionView)
async def save_recording(
    projectId: uuid.UUID,
    recordingId: uuid.UUID,
    body: s.SaveRecording,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    context: Context = Depends(get_context),
):
    await project_for(session, projectId, user.id, Permission.EDIT)
    value = await resource_for(session, "recording", projectId, recordingId, lock=True)
    scenario = await resource_for(session, "scenario", projectId, body.scenarioId, lock=True)
    await finish_recording(context, session, value)
    if value.body["status"] != "stopped":
        raise APIError(ErrorCode.RECORDING_FLUSH_FAILED)
    if not value.body["code"]:
        raise APIError(ErrorCode.EMPTY_RECORDING)
    version = s.VersionInput(
        code=value.body["code"], source="recording", changeNote=body.changeNote, checks=value.body["checks"]
    )
    # finish_recording commits; reacquire lock for atomic version numbering.
    scenario = await resource_for(session, "scenario", projectId, body.scenarioId, lock=True)
    return await add_version(session, scenario, version.model_dump(mode="json", exclude_none=True), user)
