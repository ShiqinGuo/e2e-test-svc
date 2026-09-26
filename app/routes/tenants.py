from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from .. import responses as out
from .. import schemas as s
from ..auth import current_user
from ..context import Context, get_context
from ..db import get_session
from ..models import User
from ..services.tenants import Tenants, preview_invitation

router = APIRouter(prefix="/api/v1", tags=["Organizations"])


def get_tenants(
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
    context: Context = Depends(get_context),
) -> Tenants:
    return Tenants(session, user, context.settings.app_url)


TenantDep = Annotated[Tenants, Depends(get_tenants)]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0)]


@router.get("/organizations", response_model=out.Page[out.OrganizationView])
async def organizations(service: TenantDep, limit: Limit = 50, offset: Offset = 0):
    return await service.organizations(limit, offset)


@router.post("/organizations", response_model=out.OrganizationView, status_code=201)
async def create_organization(body: s.TenantName, service: TenantDep):
    return await service.create_organization(body.name)


@router.get("/organizations/{organizationId}", response_model=out.OrganizationView)
async def organization(organizationId: UUID, service: TenantDep):
    return await service.organization(organizationId)


@router.patch("/organizations/{organizationId}", response_model=out.OrganizationView)
async def rename_organization(organizationId: UUID, body: s.TenantName, service: TenantDep):
    return await service.rename_organization(organizationId, body.name)


@router.get("/organizations/{organizationId}/workspaces", response_model=out.Page[out.WorkspaceView])
async def workspaces(organizationId: UUID, service: TenantDep, limit: Limit = 50, offset: Offset = 0):
    return await service.workspaces(organizationId, limit, offset)


@router.post("/organizations/{organizationId}/workspaces", response_model=out.WorkspaceView, status_code=201)
async def create_workspace(organizationId: UUID, body: s.TenantName, service: TenantDep):
    return await service.create_workspace(organizationId, body.name)


@router.get("/workspaces/{workspaceId}", response_model=out.WorkspaceView)
async def workspace(workspaceId: UUID, service: TenantDep):
    return await service.workspace(workspaceId)


@router.patch("/workspaces/{workspaceId}", response_model=out.WorkspaceView)
async def rename_workspace(workspaceId: UUID, body: s.TenantName, service: TenantDep):
    return await service.rename_workspace(workspaceId, body.name)


@router.get("/organizations/{organizationId}/members", response_model=out.Page[out.MemberView])
async def members(organizationId: UUID, service: TenantDep, limit: Limit = 50, offset: Offset = 0):
    return await service.members(organizationId, limit, offset)


@router.patch("/organizations/{organizationId}/members/{userId}", response_model=out.MemberView)
async def change_member(organizationId: UUID, userId: UUID, body: s.MemberRoleInput, service: TenantDep):
    return await service.change_member(organizationId, userId, body.role)


@router.delete("/organizations/{organizationId}/members/{userId}", response_model=out.Success)
async def remove_member(organizationId: UUID, userId: UUID, service: TenantDep):
    return await service.change_member(organizationId, userId, None)


@router.get("/organizations/{organizationId}/invitations", response_model=out.Page[out.InvitationView])
async def invitations(organizationId: UUID, service: TenantDep, limit: Limit = 50, offset: Offset = 0):
    return await service.invitations(organizationId, limit, offset)


@router.post("/organizations/{organizationId}/invitations", response_model=out.InvitationCreated, status_code=201)
async def invite(organizationId: UUID, body: s.InvitationInput, service: TenantDep):
    return await service.invite(organizationId, str(body.email), body.role)


@router.delete("/organizations/{organizationId}/invitations/{invitationId}", response_model=out.Success)
async def revoke(organizationId: UUID, invitationId: UUID, service: TenantDep):
    return await service.revoke(organizationId, invitationId)


@router.post("/invitations/preview", response_model=out.InvitationPreview)
async def preview(body: s.InvitationToken, session: AsyncSession = Depends(get_session)):
    return await preview_invitation(session, body.token)


@router.post("/invitations/accept", response_model=out.OrganizationView)
async def accept(body: s.InvitationToken, service: TenantDep):
    return await service.accept(body.token)
