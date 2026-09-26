import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import responses as out
from .. import schemas as s
from ..auth import current_user
from ..context import Context, get_context
from ..db import get_session
from ..domain import Permission
from ..models import RunEvent, User
from ..presenters import public_run
from ..services import runs as workflows
from ..store import list_resources, project_for, resource_for

P = "/api/v1/projects/{projectId}"
router = APIRouter()


@router.get(P + "/runs", tags=["Runs"], response_model=out.Page[out.RunView])
async def runs(
    projectId: uuid.UUID,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    await project_for(session, projectId, user.id)
    result = await list_resources(session, "run", projectId, limit, offset)
    result["items"] = [public_run(v) for v in result["items"]]
    return result


@router.post(P + "/runs", status_code=202, tags=["Runs"], response_model=out.RunView)
async def create_run(
    projectId: uuid.UUID,
    body: s.RunInput,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    context: Context = Depends(get_context),
):
    await project_for(session, projectId, user.id, Permission.EDIT)
    return await workflows.create_run(projectId, body, session, context, user.id)


@router.get(P + "/runs/{runId}", tags=["Runs"], response_model=out.RunView)
async def get_run(
    projectId: uuid.UUID,
    runId: uuid.UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    await project_for(session, projectId, user.id)
    return public_run((await resource_for(session, "run", projectId, runId)).body)


@router.post(P + "/runs/{runId}/rerun", status_code=202, tags=["Runs"], response_model=out.RunView)
async def rerun(
    projectId: uuid.UUID,
    runId: uuid.UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    context: Context = Depends(get_context),
):
    await project_for(session, projectId, user.id, Permission.EDIT)
    return await workflows.rerun(projectId, runId, session, context, user.id)


@router.post(P + "/runs/{runId}/cancel", tags=["Runs"], response_model=out.RunView)
async def cancel_run(
    projectId: uuid.UUID,
    runId: uuid.UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    context: Context = Depends(get_context),
):
    await project_for(session, projectId, user.id, Permission.EDIT)
    return await workflows.cancel_run(projectId, runId, session, context, user.id)


@router.get(P + "/runs/{runId}/events", tags=["Runs"], response_model=out.EventPage)
async def events(
    projectId: uuid.UUID,
    runId: uuid.UUID,
    after: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    await project_for(session, projectId, user.id)
    await resource_for(session, "run", projectId, runId)
    values = (
        await session.scalars(
            select(RunEvent).where(RunEvent.run_id == runId, RunEvent.id > after).order_by(RunEvent.id).limit(limit)
        )
    ).all()
    return {
        "items": [{"seq": v.id, "type": v.type, "timestamp": v.timestamp, "data": v.data} for v in values],
        "nextAfter": values[-1].id if values else after,
    }
