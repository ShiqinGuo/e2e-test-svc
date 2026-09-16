import copy
import uuid

from sqlalchemy import func, select

from .errors import not_found
from .models import Project, Resource, utcnow


def timestamp():
    return utcnow().isoformat()


async def project_for(session, project_id, owner_id):
    project = await session.scalar(select(Project).where(Project.id == project_id, Project.owner_id == owner_id))
    if project is None:
        raise not_found()
    return project


async def resource_for(session, kind, project_id, ident, *, parent_id=None, lock=False):
    statement = select(Resource).where(Resource.id == ident, Resource.kind == kind, Resource.project_id == project_id)
    if parent_id:
        statement = statement.where(Resource.parent_id == parent_id)
    if lock:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    value = await session.scalar(statement)
    if value is None:
        raise not_found()
    return value


def create_resource(session, kind, project_id, body, parent_id=None, version_number=None):
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
    return value


def update_resource(value, changes):
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
