import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from .. import responses as out
from .. import schemas as s
from ..auth import current_user
from ..context import Context, get_context
from ..db import get_session
from ..domain import Permission
from ..models import User
from ..security import public_environment
from ..services import environments as workflows
from ..services.environments import env_data, validate_targets
from ..store import create_resource, list_resources, project_for, resource_for

P = "/api/v1/projects/{projectId}"
router = APIRouter()


@router.get(P + "/environments", tags=["Environments"], response_model=out.Page[out.EnvironmentView])
async def environments(
    projectId: uuid.UUID,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    context: Context = Depends(get_context),
):
    await project_for(session, projectId, user.id)
    result = await list_resources(session, "environment", projectId, limit, offset)
    result["items"] = [
        public_environment(
            {**context.vault.open(v["encrypted"]), **{k: val for k, val in v.items() if k != "encrypted"}}
        )
        for v in result["items"]
    ]
    return result


@router.post(P + "/environments", status_code=201, tags=["Environments"], response_model=out.EnvironmentView)
async def create_environment(
    projectId: uuid.UUID,
    body: s.EnvironmentInput,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    context: Context = Depends(get_context),
):
    await project_for(session, projectId, user.id, Permission.EDIT)
    data = body.model_dump(mode="json", exclude_none=True)
    validate_targets(context.settings, data)
    value = create_resource(session, "environment", projectId, {"encrypted": context.vault.seal(data)})
    await session.commit()
    return public_environment(env_data(context, value))


@router.get(P + "/environments/{environmentId}", tags=["Environments"], response_model=out.EnvironmentView)
async def get_environment(
    projectId: uuid.UUID,
    environmentId: uuid.UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    context: Context = Depends(get_context),
):
    await project_for(session, projectId, user.id)
    return public_environment(env_data(context, await resource_for(session, "environment", projectId, environmentId)))


@router.patch(P + "/environments/{environmentId}", tags=["Environments"], response_model=out.EnvironmentView)
async def patch_environment(
    projectId: uuid.UUID,
    environmentId: uuid.UUID,
    body: s.EnvironmentPatch,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    context: Context = Depends(get_context),
):
    await project_for(session, projectId, user.id, Permission.EDIT)
    return await workflows.patch_environment(projectId, environmentId, body, session, context)
