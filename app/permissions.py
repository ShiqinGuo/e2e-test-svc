"""Tenant visibility and role checks shared by HTTP and long-lived recorder connections."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .domain import ROLE_PERMISSIONS, OrganizationRole, Permission
from .errors import APIError, ErrorCode, not_found
from .models import Membership, Organization, Workspace


def require_role(role: str, permission: Permission) -> None:
    if permission not in ROLE_PERMISSIONS[OrganizationRole(role)]:
        raise APIError(ErrorCode.FORBIDDEN)


async def organization_for(
    session: AsyncSession,
    organization_id: UUID,
    user_id: UUID,
    permission: Permission = Permission.READ,
    *,
    lock: bool = False,
) -> tuple[Organization, Membership]:
    # All membership mutations acquire this lock before reading memberships. It serializes
    # last-owner checks, invitation acceptance and concurrent administrator changes.
    statement = select(Organization).where(Organization.id == organization_id)
    if lock:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    organization = await session.scalar(statement)
    membership = await session.get(Membership, (organization_id, user_id), populate_existing=True)
    if organization is None or membership is None:
        raise not_found()
    require_role(membership.role, permission)
    return organization, membership


async def workspace_for(
    session: AsyncSession,
    workspace_id: UUID,
    user_id: UUID,
    permission: Permission = Permission.READ,
) -> tuple[Workspace, Membership]:
    workspace = await session.get(Workspace, workspace_id)
    if workspace is None:
        raise not_found()
    _, membership = await organization_for(session, workspace.organization_id, user_id, permission)
    return workspace, membership
