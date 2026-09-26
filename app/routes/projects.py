import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import responses as out
from .. import schemas as s
from ..auth import current_user
from ..db import get_session
from ..domain import Permission
from ..models import Project, User
from ..permissions import workspace_for
from ..presenters import envelope
from ..store import create_resource, list_resources, project_for, resource_for, timestamp, update_resource

P = "/api/v1/projects/{projectId}"
router = APIRouter()


@router.get("/api/v1/projects", tags=["Projects"], response_model=out.Page[out.ProjectView])
async def projects(
    workspaceId: uuid.UUID,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    await workspace_for(session, workspaceId, user.id)
    statement = select(Project).where(Project.workspace_id == workspaceId)
    total = await session.scalar(select(func.count()).select_from(statement.subquery()))
    values = (
        await session.scalars(statement.order_by(Project.created_at.desc(), Project.id).limit(limit).offset(offset))
    ).all()
    return envelope([v.body for v in values], total, limit, offset)


@router.post("/api/v1/projects", status_code=201, tags=["Projects"], response_model=out.ProjectView)
async def create_project(
    body: s.ProjectCreate, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
):
    workspace, _ = await workspace_for(session, body.workspaceId, user.id, Permission.EDIT)
    ident = uuid.uuid4()
    value = Project(
        id=ident,
        created_by=user.id,
        workspace_id=workspace.id,
        body={
            **body.model_dump(mode="json"),
            "id": str(ident),
            "createdBy": str(user.id),
            "organizationId": str(workspace.organization_id),
            "createdAt": timestamp(),
            "updatedAt": timestamp(),
        },
    )
    session.add(value)
    await session.commit()
    return value.body


@router.get(P, tags=["Projects"], response_model=out.ProjectView)
async def get_project(
    projectId: uuid.UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
):
    return (await project_for(session, projectId, user.id)).body


@router.patch(P, tags=["Projects"], response_model=out.ProjectView)
async def patch_project(
    projectId: uuid.UUID,
    body: s.ProjectPatch,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    value = await project_for(session, projectId, user.id, Permission.EDIT)
    value.body = {**value.body, **body.model_dump(exclude_unset=True), "updatedAt": timestamp()}
    await session.commit()
    return value.body


@router.get(P + "/groups", tags=["Groups"], response_model=out.Page[out.GroupView])
async def groups(
    projectId: uuid.UUID,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    await project_for(session, projectId, user.id)
    return await list_resources(session, "group", projectId, limit, offset)


@router.post(P + "/groups", status_code=201, tags=["Groups"], response_model=out.GroupView)
async def create_group(
    projectId: uuid.UUID,
    body: s.ProjectInput,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    await project_for(session, projectId, user.id, Permission.EDIT)
    value = create_resource(session, "group", projectId, body.model_dump())
    await session.commit()
    return value.body


@router.get(P + "/groups/{groupId}", tags=["Groups"], response_model=out.GroupView)
async def get_group(
    projectId: uuid.UUID,
    groupId: uuid.UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    await project_for(session, projectId, user.id)
    return (await resource_for(session, "group", projectId, groupId)).body


@router.patch(P + "/groups/{groupId}", tags=["Groups"], response_model=out.GroupView)
async def patch_group(
    projectId: uuid.UUID,
    groupId: uuid.UUID,
    body: s.ProjectPatch,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    await project_for(session, projectId, user.id, Permission.EDIT)
    value = await resource_for(session, "group", projectId, groupId, lock=True)
    update_resource(value, body.model_dump(exclude_unset=True))
    await session.commit()
    return value.body
