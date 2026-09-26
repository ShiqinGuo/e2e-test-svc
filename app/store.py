import copy
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import object_session

from .domain import Permission, ResourceKind, check_transition
from .errors import not_found
from .models import Project, Resource, utcnow
from .observability import Event, after_commit_event
from .permissions import workspace_for


def timestamp() -> str:
    return utcnow().isoformat()


async def project_for(
    session: AsyncSession, project_id: uuid.UUID, user_id: uuid.UUID, permission: Permission = Permission.READ
) -> Project:
    project = await session.get(Project, project_id)
    if project is None:
        raise not_found()
    await workspace_for(session, project.workspace_id, user_id, permission)
    return project


async def resource_for(
    session: AsyncSession,
    kind: str,
    project_id: uuid.UUID,
    ident: uuid.UUID,
    *,
    parent_id: uuid.UUID | None = None,
    lock: bool = False,
) -> Resource:
    statement = select(Resource).where(Resource.id == ident, Resource.kind == kind, Resource.project_id == project_id)
    if parent_id:
        statement = statement.where(Resource.parent_id == parent_id)
    if lock:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    value = await session.scalar(statement)
    if value is None:
        raise not_found()
    return value


def create_resource(
    session: AsyncSession,
    kind: str,
    project_id: uuid.UUID,
    body: dict[str, Any],
    parent_id: uuid.UUID | None = None,
    version_number: int | None = None,
) -> Resource:
    ident = uuid.uuid4()
    value = Resource(
        id=ident,
        kind=kind,
        project_id=project_id,
        parent_id=parent_id,
        version_number=version_number,
        body={
            **body,
            "id": str(ident),
            "projectId": str(project_id),
            "createdAt": timestamp(),
            "updatedAt": timestamp(),
        },
    )
    session.add(value)
    after_commit_event(
        session.sync_session, Event.RESOURCE_CREATED, kind=kind, resourceId=str(ident), projectId=str(project_id)
    )
    return value


def update_resource(value: Resource, changes: dict[str, Any]) -> Resource:
    if value.kind == ResourceKind.VERSION:
        raise ValueError("Scenario versions are immutable")
    if value.kind in {ResourceKind.RUN, ResourceKind.RECORDING} and "status" in changes:
        check_transition(value.kind, value.body["status"], changes["status"])
        if value.body["status"] != changes["status"]:
            after_commit_event(
                object_session(value),
                Event.RESOURCE_TRANSITIONED,
                kind=value.kind,
                resourceId=str(value.id),
                previous=value.body["status"],
                status=changes["status"],
            )
    value.body = {**copy.deepcopy(value.body), **changes, "updatedAt": timestamp()}
    return value


async def list_resources(session, kind, project_id, limit, offset, parent_id=None):
    statement = select(Resource).where(Resource.kind == kind, Resource.project_id == project_id)
    if parent_id:
        statement = statement.where(Resource.parent_id == parent_id)
    total = await session.scalar(select(func.count()).select_from(statement.subquery()))
    values = (
        await session.scalars(statement.order_by(Resource.created_at.desc(), Resource.id).limit(limit).offset(offset))
    ).all()
    return {"items": [v.body for v in values], "total": total, "limit": limit, "offset": offset}
