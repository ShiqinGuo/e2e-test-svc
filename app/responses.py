"""Public response contracts. Private persistence fields never enter these models."""

from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, Field

from .domain import InvitationStatus, OrganizationRole, RecordingStatus, RunStatus, Verification
from .schemas import ApiAction, Check

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


class UserView(BaseModel):
    id: str
    name: str
    email: str


class SessionView(BaseModel):
    id: str
    userId: str
    expiresAt: str


class AuthSession(BaseModel):
    user: UserView
    session: SessionView


class Success(BaseModel):
    success: Literal[True]


class CurrentUser(BaseModel):
    user: UserView


class Health(BaseModel):
    status: Literal["ok"]


class Availability(BaseModel):
    available: bool
    reason: str | None = None


class RuntimeAvailability(BaseModel):
    runner: Availability
    recorder: Availability


class Capabilities(RuntimeAvailability):
    playwrightVersion: str
    databaseChecks: Literal["api-only"]


class Identity(BaseModel):
    id: str
    createdAt: str
    updatedAt: str


class Named(BaseModel):
    name: str
    description: str


class ProjectView(Identity, Named):
    organizationId: str
    workspaceId: str
    createdBy: str


class OrganizationView(Identity):
    name: str
    role: OrganizationRole
    defaultWorkspaceId: str


class WorkspaceView(Identity):
    organizationId: str
    name: str
    role: OrganizationRole


class MemberView(BaseModel):
    userId: str
    name: str
    email: str
    role: OrganizationRole
    joinedAt: str


class InvitationView(BaseModel):
    id: str
    organizationId: str
    email: str
    role: Literal["admin", "member", "viewer"]
    status: InvitationStatus
    expiresAt: str
    createdAt: str


class InvitationCreated(BaseModel):
    invitation: InvitationView
    inviteUrl: str


class InvitationPreview(BaseModel):
    organizationId: str
    organizationName: str
    email: str
    role: Literal["admin", "member", "viewer"]
    status: InvitationStatus
    expiresAt: str


class ResourceView(Identity):
    projectId: str


class GroupView(ResourceView, Named):
    pass


class ScenarioView(GroupView):
    groupId: str | None
    currentVersionId: str | None


class VersionView(ResourceView):
    scenarioId: str
    number: int
    code: str
    source: Literal["human", "ai", "recording", "import"]
    changeNote: str
    checks: list[Check]
    modules: dict[str, str]
    createdBy: str


class RoleView(BaseModel):
    name: str
    hasStorageState: bool
    hasHeaders: bool


class EnvironmentView(ResourceView, Named):
    websites: dict[str, str]
    apiBases: dict[str, str]
    variables: dict[str, str]
    secretVariableKeys: list[str]
    roles: list[RoleView]
    setup: list[ApiAction]
    cleanup: list[ApiAction]
    allowedOrigins: list[str]


class RunSummary(BaseModel):
    total: int = Field(ge=0)
    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    skipped: int = Field(ge=0)
    flaky: int = Field(ge=0)
    unverified: int = Field(ge=0)


class RunView(ResourceView):
    environmentId: str
    scenarioId: str | None
    groupId: str | None
    role: str | None
    retries: int
    timeoutMs: int
    versions: list[VersionView]
    environmentSnapshot: EnvironmentView
    status: RunStatus
    verification: Verification
    summary: RunSummary
    startedAt: str | None
    finishedAt: str | None
    error: str | None
    rerunOf: str | None = None


class RecordingView(ResourceView):
    environmentId: str
    scenarioId: str | None
    status: RecordingStatus
    expiresAt: str
    viewerUrl: str | None
    code: str
    checks: list[Check]
    error: str | None


class EventView(BaseModel):
    seq: int
    type: str
    timestamp: str
    data: Any


class EventPage(BaseModel):
    items: list[EventView]
    nextAfter: int


class ArtifactView(BaseModel):
    id: str
    runId: str
    name: str
    contentType: str
    kind: str
    size: int
    url: str


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: Any = None


class ErrorResponse(BaseModel):
    error: ErrorDetail
    requestId: str


class AuthError(BaseModel):
    code: str
    message: str
