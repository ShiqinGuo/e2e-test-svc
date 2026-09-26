"""Independent lifecycles and the transitions the application can persist."""

from enum import StrEnum


class OrganizationRole(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"


class Permission(StrEnum):
    READ = "read"
    EDIT = "edit"
    MANAGE = "manage"
    OWN = "own"


class InvitationStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REVOKED = "revoked"
    EXPIRED = "expired"


ROLE_PERMISSIONS = {
    OrganizationRole.OWNER: frozenset(Permission),
    OrganizationRole.ADMIN: frozenset({Permission.READ, Permission.EDIT, Permission.MANAGE}),
    OrganizationRole.MEMBER: frozenset({Permission.READ, Permission.EDIT}),
    OrganizationRole.VIEWER: frozenset({Permission.READ}),
}


class ResourceKind(StrEnum):
    GROUP = "group"
    SCENARIO = "scenario"
    VERSION = "version"
    ENVIRONMENT = "environment"
    RUN = "run"
    RECORDING = "recording"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    ERROR = "error"


class RecordingStatus(StrEnum):
    STARTING = "starting"
    READY = "ready"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


class Verification(StrEnum):
    PENDING = "pending"
    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    PARTIAL = "partial"


TERMINAL_RUNS = frozenset(
    {RunStatus.PASSED, RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.TIMED_OUT, RunStatus.ERROR}
)
RUN_TRANSITIONS = {
    RunStatus.QUEUED: {RunStatus.RUNNING, RunStatus.CANCELLED, RunStatus.ERROR},
    RunStatus.RUNNING: TERMINAL_RUNS,
    **{status: frozenset() for status in TERMINAL_RUNS},
}
RECORDING_TRANSITIONS = {
    RecordingStatus.STARTING: {RecordingStatus.READY, RecordingStatus.STOPPING, RecordingStatus.ERROR},
    RecordingStatus.READY: {RecordingStatus.STOPPING, RecordingStatus.ERROR},
    RecordingStatus.STOPPING: {RecordingStatus.STOPPED, RecordingStatus.ERROR},
    RecordingStatus.STOPPED: frozenset(),
    RecordingStatus.ERROR: frozenset(),
}


def check_transition(kind: str, previous: str, current: str) -> None:
    transitions = RUN_TRANSITIONS if kind == ResourceKind.RUN else RECORDING_TRANSITIONS
    if current != previous and current not in transitions[previous]:
        raise ValueError(f"Illegal {kind} transition: {previous} -> {current}")
