import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from .. import responses as out
from .. import schemas as s
from ..auth import current_user
from ..db import get_session
from ..domain import Permission
from ..models import User
from ..services.versions import add_version
from ..store import create_resource, list_resources, project_for, resource_for, update_resource

P = "/api/v1/projects/{projectId}"
router = APIRouter()


@router.get(P + "/scenarios", tags=["Scenarios"], response_model=out.Page[out.ScenarioView])
async def scenarios(
    projectId: uuid.UUID,
    groupId: uuid.UUID | None = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    await project_for(session, projectId, user.id)
    if groupId:
        await resource_for(session, "group", projectId, groupId)
    return await list_resources(session, "scenario", projectId, limit, offset, groupId)


@router.post(P + "/scenarios", status_code=201, tags=["Scenarios"], response_model=out.ScenarioView)
async def create_scenario(
    projectId: uuid.UUID,
    body: s.ScenarioInput,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    await project_for(session, projectId, user.id, Permission.EDIT)
    if body.groupId:
        await resource_for(session, "group", projectId, body.groupId)
    value = create_resource(
        session, "scenario", projectId, {**body.model_dump(mode="json"), "currentVersionId": None}, body.groupId
    )
    await session.commit()
    return value.body


@router.get(P + "/scenarios/{scenarioId}", tags=["Scenarios"], response_model=out.ScenarioView)
async def get_scenario(
    projectId: uuid.UUID,
    scenarioId: uuid.UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    await project_for(session, projectId, user.id)
    return (await resource_for(session, "scenario", projectId, scenarioId)).body


@router.patch(P + "/scenarios/{scenarioId}", tags=["Scenarios"], response_model=out.ScenarioView)
async def patch_scenario(
    projectId: uuid.UUID,
    scenarioId: uuid.UUID,
    body: s.ScenarioPatch,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    await project_for(session, projectId, user.id, Permission.EDIT)
    value = await resource_for(session, "scenario", projectId, scenarioId, lock=True)
    if "groupId" in body.model_fields_set:
        if body.groupId:
            await resource_for(session, "group", projectId, body.groupId)
        value.parent_id = body.groupId
    update_resource(value, body.model_dump(mode="json", exclude_unset=True))
    await session.commit()
    return value.body


@router.get(P + "/scenarios/{scenarioId}/versions", tags=["Versions"], response_model=out.Page[out.VersionView])
async def versions(
    projectId: uuid.UUID,
    scenarioId: uuid.UUID,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    await project_for(session, projectId, user.id)
    await resource_for(session, "scenario", projectId, scenarioId)
    return await list_resources(session, "version", projectId, limit, offset, scenarioId)


@router.post(P + "/scenarios/{scenarioId}/versions", status_code=201, tags=["Versions"], response_model=out.VersionView)
async def create_version(
    projectId: uuid.UUID,
    scenarioId: uuid.UUID,
    body: s.VersionInput,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    await project_for(session, projectId, user.id, Permission.EDIT)
    scenario = await resource_for(session, "scenario", projectId, scenarioId, lock=True)
    return await add_version(session, scenario, body.model_dump(mode="json", exclude_none=True), user)


@router.get(P + "/scenarios/{scenarioId}/versions/{versionId}", tags=["Versions"], response_model=out.VersionView)
async def get_version(
    projectId: uuid.UUID,
    scenarioId: uuid.UUID,
    versionId: uuid.UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    await project_for(session, projectId, user.id)
    await resource_for(session, "scenario", projectId, scenarioId)
    return (await resource_for(session, "version", projectId, versionId, parent_id=scenarioId)).body
