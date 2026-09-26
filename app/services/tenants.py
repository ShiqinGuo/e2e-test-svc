"""Organization membership is the single authority for all workspace/project access."""

import hashlib
import secrets
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..domain import InvitationStatus, OrganizationRole, Permission
from ..errors import APIError, ErrorCode, not_found
from ..models import Invitation, Membership, Organization, User, Workspace, utcnow
from ..observability import Event, after_commit_event
from ..permissions import organization_for, workspace_for
from ..presenters import envelope
from ..responses import InvitationView, MemberView, OrganizationView, WorkspaceView

INVITATION_LIFETIME = timedelta(days=7)


def workspace_view(value: Workspace, membership: Membership) -> WorkspaceView:
    return WorkspaceView(
        id=str(value.id),
        organizationId=str(value.organization_id),
        name=value.name,
        role=membership.role,
        createdAt=value.created_at.isoformat(),
        updatedAt=value.updated_at.isoformat(),
    )


def member_view(membership: Membership, user: User) -> MemberView:
    return MemberView(
        userId=str(user.id),
        name=user.name,
        email=user.email,
        role=membership.role,
        joinedAt=membership.joined_at.isoformat(),
    )


def invitation_view(value: Invitation) -> InvitationView:
    status = (
        InvitationStatus.EXPIRED
        if value.status == InvitationStatus.PENDING and value.expires_at <= utcnow()
        else value.status
    )
    return InvitationView(
        id=str(value.id),
        organizationId=str(value.organization_id),
        email=value.email,
        role=value.role,
        status=status,
        expiresAt=value.expires_at.isoformat(),
        createdAt=value.created_at.isoformat(),
    )


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def require_manage_role(actor: Membership, role: str) -> None:
    if actor.role == OrganizationRole.OWNER:
        return
    if actor.role != OrganizationRole.ADMIN or role in {OrganizationRole.OWNER, OrganizationRole.ADMIN}:
        raise APIError(ErrorCode.FORBIDDEN)


class Tenants:
    def __init__(self, session: AsyncSession, user: User, app_url: str):
        self.session, self.user, self.app_url = session, user, app_url

    async def organization_view(self, value: Organization, membership: Membership) -> OrganizationView:
        default_id = await self.session.scalar(
            select(Workspace.id)
            .where(Workspace.organization_id == value.id)
            .order_by(Workspace.created_at, Workspace.id)
            .limit(1)
        )
        return OrganizationView(
            id=str(value.id),
            name=value.name,
            role=membership.role,
            defaultWorkspaceId=str(default_id),
            createdAt=value.created_at.isoformat(),
            updatedAt=value.updated_at.isoformat(),
        )

    async def organizations(self, limit: int, offset: int):
        statement = (
            select(Organization, Membership)
            .join(Membership, Membership.organization_id == Organization.id)
            .where(Membership.user_id == self.user.id)
        )
        total = await self.session.scalar(select(func.count()).select_from(statement.subquery()))
        rows = (
            await self.session.execute(
                statement.order_by(Organization.created_at, Organization.id).limit(limit).offset(offset)
            )
        ).all()
        return envelope([await self.organization_view(org, member) for org, member in rows], total, limit, offset)

    async def create_organization(self, name: str):
        organization = Organization(id=uuid4(), name=name)
        self.session.add(organization)
        await self.session.flush()
        member = Membership(organization_id=organization.id, user_id=self.user.id, role=OrganizationRole.OWNER)
        workspace = Workspace(organization_id=organization.id, name="默认工作区")
        self.session.add_all([member, workspace])
        after_commit_event(
            self.session.sync_session,
            Event.ORGANIZATION_CREATED,
            organizationId=str(organization.id),
            actorId=str(self.user.id),
        )
        await self.session.commit()
        return await self.organization_view(organization, member)

    async def organization(self, ident: UUID):
        org, member = await organization_for(self.session, ident, self.user.id)
        return await self.organization_view(org, member)

    async def rename_organization(self, ident: UUID, name: str):
        org, member = await organization_for(self.session, ident, self.user.id, Permission.OWN, lock=True)
        org.name, org.updated_at = name, utcnow()
        await self.session.commit()
        return await self.organization_view(org, member)

    async def workspaces(self, ident: UUID, limit: int, offset: int):
        _, member = await organization_for(self.session, ident, self.user.id)
        statement = select(Workspace).where(Workspace.organization_id == ident)
        total = await self.session.scalar(select(func.count()).select_from(statement.subquery()))
        values = (
            await self.session.scalars(
                statement.order_by(Workspace.created_at, Workspace.id).limit(limit).offset(offset)
            )
        ).all()
        return envelope([workspace_view(v, member) for v in values], total, limit, offset)

    async def create_workspace(self, ident: UUID, name: str):
        _, member = await organization_for(self.session, ident, self.user.id, Permission.MANAGE, lock=True)
        workspace = Workspace(organization_id=ident, name=name)
        self.session.add(workspace)
        await self.session.commit()
        return workspace_view(workspace, member)

    async def workspace(self, ident: UUID):
        workspace, member = await workspace_for(self.session, ident, self.user.id)
        return workspace_view(workspace, member)

    async def rename_workspace(self, ident: UUID, name: str):
        workspace, _ = await workspace_for(self.session, ident, self.user.id)
        _, member = await organization_for(
            self.session, workspace.organization_id, self.user.id, Permission.MANAGE, lock=True
        )
        workspace.name, workspace.updated_at = name, utcnow()
        await self.session.commit()
        return workspace_view(workspace, member)

    async def members(self, ident: UUID, limit: int, offset: int):
        await organization_for(self.session, ident, self.user.id)
        statement = (
            select(Membership, User)
            .join(User, User.id == Membership.user_id)
            .where(Membership.organization_id == ident)
        )
        total = await self.session.scalar(select(func.count()).select_from(statement.subquery()))
        rows = (
            await self.session.execute(statement.order_by(Membership.joined_at, User.id).limit(limit).offset(offset))
        ).all()
        return envelope([member_view(member, user) for member, user in rows], total, limit, offset)

    async def change_member(self, organization_id: UUID, user_id: UUID, role: OrganizationRole | None):
        _, actor = await organization_for(self.session, organization_id, self.user.id, lock=True)
        member = await self.session.get(Membership, (organization_id, user_id), populate_existing=True)
        if member is None:
            raise not_found()
        if role is not None or user_id != self.user.id:
            require_manage_role(actor, member.role)
            if role is not None:
                require_manage_role(actor, role)
        if member.role == OrganizationRole.OWNER and role != OrganizationRole.OWNER:
            owners = await self.session.scalar(
                select(func.count())
                .select_from(Membership)
                .where(Membership.organization_id == organization_id, Membership.role == OrganizationRole.OWNER)
            )
            if owners == 1:
                raise APIError(ErrorCode.LAST_OWNER)
        if role is None:
            await self.session.delete(member)
            after_commit_event(
                self.session.sync_session,
                Event.MEMBERSHIP_CHANGED,
                organizationId=str(organization_id),
                userId=str(user_id),
                actorId=str(self.user.id),
                role=None,
            )
            await self.session.commit()
            return {"success": True}
        member.role = role
        after_commit_event(
            self.session.sync_session,
            Event.MEMBERSHIP_CHANGED,
            organizationId=str(organization_id),
            userId=str(user_id),
            actorId=str(self.user.id),
            role=role,
        )
        user = await self.session.get(User, user_id)
        await self.session.commit()
        return member_view(member, user)

    async def invitations(self, ident: UUID, limit: int, offset: int):
        await organization_for(self.session, ident, self.user.id, Permission.MANAGE)
        statement = select(Invitation).where(Invitation.organization_id == ident)
        total = await self.session.scalar(select(func.count()).select_from(statement.subquery()))
        values = (
            await self.session.scalars(
                statement.order_by(Invitation.created_at.desc(), Invitation.id).limit(limit).offset(offset)
            )
        ).all()
        return envelope([invitation_view(v) for v in values], total, limit, offset)

    async def invite(self, ident: UUID, email: str, role: str):
        _, actor = await organization_for(self.session, ident, self.user.id, Permission.MANAGE, lock=True)
        require_manage_role(actor, role)
        email = email.casefold()
        existing = await self.session.scalar(
            select(Membership)
            .join(User, User.id == Membership.user_id)
            .where(Membership.organization_id == ident, func.lower(User.email) == email)
        )
        if existing:
            raise APIError(ErrorCode.ALREADY_MEMBER)
        previous = await self.session.scalar(
            select(Invitation).where(
                Invitation.organization_id == ident,
                Invitation.email == email,
                Invitation.status == InvitationStatus.PENDING,
            )
        )
        if previous:
            require_manage_role(actor, previous.role)
            previous.status = InvitationStatus.REVOKED
            await self.session.flush()
        token = secrets.token_urlsafe(32)
        value = Invitation(
            id=uuid4(),
            organization_id=ident,
            email=email,
            role=role,
            token_hash=token_hash(token),
            created_by=self.user.id,
            status=InvitationStatus.PENDING,
            expires_at=utcnow() + INVITATION_LIFETIME,
        )
        self.session.add(value)
        after_commit_event(
            self.session.sync_session,
            Event.INVITATION_CREATED,
            organizationId=str(ident),
            invitationId=str(value.id),
            actorId=str(self.user.id),
            role=role,
        )
        await self.session.commit()
        return {"invitation": invitation_view(value), "inviteUrl": f"{self.app_url.rstrip('/')}/join#token={token}"}

    async def revoke(self, organization_id: UUID, invitation_id: UUID):
        _, actor = await organization_for(self.session, organization_id, self.user.id, Permission.MANAGE, lock=True)
        value = await self.session.scalar(
            select(Invitation).where(Invitation.id == invitation_id, Invitation.organization_id == organization_id)
        )
        if value is None:
            raise not_found()
        require_manage_role(actor, value.role)
        if value.status == InvitationStatus.ACCEPTED:
            raise APIError(ErrorCode.INVITATION_UNAVAILABLE)
        if value.status == InvitationStatus.REVOKED:
            return {"success": True}
        value.status = InvitationStatus.REVOKED
        after_commit_event(
            self.session.sync_session,
            Event.INVITATION_REVOKED,
            organizationId=str(organization_id),
            invitationId=str(value.id),
            actorId=str(self.user.id),
        )
        await self.session.commit()
        return {"success": True}

    async def accept(self, token: str):
        value = await invitation_for(self.session, token)
        organization = await self.session.scalar(
            select(Organization).where(Organization.id == value.organization_id).with_for_update()
        )
        await self.session.refresh(value, with_for_update=True)
        if self.user.email.casefold() != value.email:
            raise APIError(ErrorCode.INVITATION_EMAIL_MISMATCH)
        member = await self.session.get(Membership, (organization.id, self.user.id), populate_existing=True)
        if value.status == InvitationStatus.ACCEPTED and value.accepted_by == self.user.id and member:
            return await self.organization_view(organization, member)
        if value.status != InvitationStatus.PENDING or value.expires_at <= utcnow():
            raise APIError(ErrorCode.INVITATION_UNAVAILABLE)
        if member:
            raise APIError(ErrorCode.ALREADY_MEMBER)
        member = Membership(organization_id=organization.id, user_id=self.user.id, role=value.role)
        self.session.add(member)
        value.status, value.accepted_by = InvitationStatus.ACCEPTED, self.user.id
        after_commit_event(
            self.session.sync_session,
            Event.INVITATION_ACCEPTED,
            organizationId=str(organization.id),
            invitationId=str(value.id),
            userId=str(self.user.id),
            role=value.role,
        )
        await self.session.commit()
        return await self.organization_view(organization, member)


async def invitation_for(session: AsyncSession, token: str) -> Invitation:
    value = await session.scalar(select(Invitation).where(Invitation.token_hash == token_hash(token)))
    if value is None:
        raise not_found()
    return value


async def preview_invitation(session: AsyncSession, token: str):
    value = await invitation_for(session, token)
    organization = await session.get(Organization, value.organization_id)
    return {
        "organizationId": str(organization.id),
        "organizationName": organization.name,
        "email": value.email,
        "role": value.role,
        "status": invitation_view(value).status,
        "expiresAt": value.expires_at.isoformat(),
    }
