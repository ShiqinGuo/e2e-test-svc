import re
from typing import Any, Literal
from urllib.parse import urlparse
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


class Register(Input):
    name: str = Field(min_length=1, max_length=200)
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)


class Login(Input):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class ProjectInput(Input):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)


class ProjectPatch(Input):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def no_null(self):
        if any(getattr(self, field) is None for field in self.model_fields_set if field != "groupId"):
            raise ValueError("Explicit null is only allowed for groupId")
        return self


class ScenarioInput(ProjectInput):
    groupId: UUID | None = None


class ScenarioPatch(ProjectPatch):
    groupId: UUID | None = None


class Check(Input):
    id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=200)
    kind: Literal["ui", "api", "data"]
    expected: str | None = Field(default=None, max_length=4000)


class VersionInput(Input):
    code: str = Field(min_length=1, max_length=512000)
    source: Literal["human", "ai", "recording", "import"]
    changeNote: str = Field(default="", max_length=4000)
    checks: list[Check] = Field(default_factory=list, max_length=1000)
    modules: dict[str, str] = Field(default_factory=dict)

    @field_validator("modules")
    @classmethod
    def modules_valid(cls, value):
        if len(value) > 50 or any(
            not re.fullmatch(r"[A-Za-z0-9_-]+\.ts", k) or len(v) > 256000 for k, v in value.items()
        ):
            raise ValueError("Invalid module name, count or size")
        return value


class Role(Input):
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_-]*$")
    storageState: dict[str, Any] | None = None
    headers: dict[str, str] | None = None


class ApiAction(Input):
    name: str = Field(min_length=1, max_length=200)
    apiBase: str = Field(min_length=1)
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
    path: str = Field(min_length=1, max_length=4000)
    headers: dict[str, str] = Field(default_factory=dict)
    body: Any = None
    expectedStatus: int = Field(default=200, ge=100, le=599)
    capture: dict[str, str] = Field(default_factory=dict)

    @field_validator("path")
    @classmethod
    def relative(cls, value):
        if re.match(r"([a-z][a-z0-9+.-]*:|//)", value, re.I):
            raise ValueError("API action paths must be relative")
        return value

    @field_validator("headers")
    @classmethod
    def credential_references(cls, value):
        for key, val in value.items():
            if key.lower() in {"authorization", "cookie", "x-api-key", "proxy-authorization"} and not re.search(
                r"\{\{[A-Za-z_][A-Za-z0-9_]*\}\}", val
            ):
                raise ValueError("Sensitive action headers must reference a secret variable")
        return value


def validate_bases(value):
    for key, url in value.items():
        parsed = urlparse(url)
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", key):
            raise ValueError("Invalid named base key")
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.fragment
            or parsed.query
        ):
            raise ValueError("Use HTTP(S) URLs without credentials, query or fragment")
    return value


class EnvironmentInput(ProjectInput):
    websites: dict[str, str] = Field(min_length=1, max_length=100)
    apiBases: dict[str, str] = Field(default_factory=dict, max_length=100)
    variables: dict[str, str] = Field(default_factory=dict)
    secretVariables: dict[str, str] = Field(default_factory=dict)
    roles: list[Role] = Field(default_factory=list, max_length=50)
    setup: list[ApiAction] = Field(default_factory=list, max_length=100)
    cleanup: list[ApiAction] = Field(default_factory=list, max_length=100)
    allowedOrigins: list[str] = Field(default_factory=list, max_length=100)
    _bases = field_validator("websites", "apiBases")(validate_bases)

    @model_validator(mode="after")
    def validate_environment(self):
        if len({r.name for r in self.roles}) != len(self.roles):
            raise ValueError("Role names must be unique")
        for key in self.variables | self.secretVariables:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
                raise ValueError("Invalid variable name")
        if self.variables.keys() & self.secretVariables.keys():
            raise ValueError("Public and secret variable keys must be distinct")
        for action in self.setup + self.cleanup:
            if action.apiBase not in self.apiBases:
                raise ValueError("API action references an unknown API base")
        validate_bases({f"origin{i}": v for i, v in enumerate(self.allowedOrigins)})
        return self


class EnvironmentPatch(Input):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    websites: dict[str, str] | None = None
    apiBases: dict[str, str] | None = None
    variables: dict[str, str] | None = None
    secretVariables: dict[str, str] | None = None
    roles: list[Role] | None = None
    setup: list[ApiAction] | None = None
    cleanup: list[ApiAction] | None = None
    allowedOrigins: list[str] | None = None

    @model_validator(mode="after")
    def no_null(self):
        if any(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError("Environment fields cannot be null")
        return self


class RunInput(Input):
    environmentId: UUID
    scenarioId: UUID | None = None
    groupId: UUID | None = None
    versionId: UUID | None = None
    role: str | None = None
    retries: int = Field(default=0, ge=0, le=2)
    timeoutMs: int = Field(default=60000, ge=1000, le=600000)

    @model_validator(mode="after")
    def target(self):
        if bool(self.scenarioId) == bool(self.groupId) or (self.versionId and not self.scenarioId):
            raise ValueError("Choose exactly one scenario or group; versionId requires scenarioId")
        return self


class RecordingInput(Input):
    environmentId: UUID
    website: str = Field(min_length=1)
    role: str | None = None
    scenarioId: UUID | None = None


class SaveRecording(Input):
    scenarioId: UUID
    changeNote: str = Field(default="Saved recording", max_length=4000)
