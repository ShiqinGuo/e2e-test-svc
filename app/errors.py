"""Stable error identity, status and default message are maintained together."""

from enum import Enum
from typing import Any


class ErrorCode(Enum):
    FORBIDDEN = (403, "Your organization role does not permit this action")
    LAST_OWNER = (409, "The organization must retain at least one owner")
    ALREADY_MEMBER = (409, "This account is already a member of the organization")
    INVITATION_EMAIL_MISMATCH = (403, "Sign in with the email address named in the invitation")
    INVITATION_UNAVAILABLE = (409, "This invitation is no longer available")
    CONFLICT = (409, "A conflicting resource already exists; refresh and retry")
    EMPTY_GROUP = (409, "This group has no scenarios")
    EMPTY_RECORDING = (409, "Recording has no generated code")
    HTTP_ERROR = (400, "HTTP request failed")
    INVALID_CREDENTIALS = (401, "Invalid email or password")
    INVALID_ROLE = (400, "Role does not exist in this environment")
    INVALID_TARGET = (400, "Platform endpoints cannot be configured as test targets")
    INVALID_WEBSITE = (400, "Named website does not exist in this environment")
    MISSING_VERSION = (409, "Every scenario must have a saved version")
    NOT_FOUND = (404, "Resource not found")
    ORIGIN_FORBIDDEN = (403, "Request origin is not trusted")
    PAYLOAD_TOO_LARGE = (413, "Request body exceeds 2 MiB")
    RATE_LIMITED = (429, "Too many authentication attempts; retry in five minutes")
    RECORDING_FLUSH_FAILED = (409, "Recording did not stop and flush successfully; no version was created")
    RECORDING_LIMIT = (409, "Stop an existing recording before starting another")
    RECORDING_NOT_READY = (409, "Recording is not ready")
    RUNTIME_UNAVAILABLE = (503, "Runtime unavailable")
    TRACE_NOT_FOUND = (404, "No trace was produced for this run")
    UNAUTHENTICATED = (401, "Sign in to continue")
    USER_ALREADY_EXISTS = (409, "An account with this email already exists")
    VALIDATION_ERROR = (400, "Invalid request")


class APIError(Exception):
    def __init__(self, error: ErrorCode, *, message: str | None = None, details: Any = None, status: int | None = None):
        default_status, default_message = error.value
        self.status = default_status if status is None else status
        self.code = error.name
        self.message = default_message if message is None else message
        self.details = details
        super().__init__(self.message)


def not_found() -> APIError:
    return APIError(ErrorCode.NOT_FOUND)
