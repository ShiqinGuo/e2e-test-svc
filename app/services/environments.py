import uuid
from urllib.parse import urlparse

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from .. import schemas as s
from ..context import Context
from ..errors import APIError, ErrorCode
from ..security import public_environment
from ..store import resource_for, update_resource


def env_data(context, row):
    return {**context.vault.open(row.body["encrypted"]), **{k: v for k, v in row.body.items() if k != "encrypted"}}


def validate_targets(settings, environment):
    blocked_ports = {
        urlparse(origin).port or (443 if origin.startswith("https") else 80)
        for origin in settings.trusted_origins + [settings.base_url]
    }
    targets = (
        list(environment["websites"].values())
        + list(environment["apiBases"].values())
        + environment.get("allowedOrigins", [])
    )
    for url in targets:
        parsed = urlparse(url)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if (
            parsed.hostname in {"localhost", "127.0.0.1", "::1", "host.docker.internal", "gateway.docker.internal"}
            and port in blocked_ports
        ):
            raise APIError(ErrorCode.INVALID_TARGET)
        if parsed.port in {2375, 2376, 5432, 55432}:
            raise APIError(
                ErrorCode.INVALID_TARGET, message="Infrastructure endpoints cannot be configured as test targets"
            )


def validate_role(environment, role):
    if role and role not in [r["name"] for r in environment.get("roles", [])]:
        raise APIError(ErrorCode.INVALID_ROLE)


async def patch_environment(
    projectId: uuid.UUID, environmentId: uuid.UUID, body: s.EnvironmentPatch, session: AsyncSession, context: Context
):
    value = await resource_for(session, "environment", projectId, environmentId, lock=True)
    data = context.vault.open(value.body["encrypted"])
    changes = body.model_dump(mode="json", exclude_unset=True)
    if "secretVariables" in changes:
        changes["secretVariables"] = {**data.get("secretVariables", {}), **changes["secretVariables"]}
    if "roles" in changes:
        previous = {r["name"]: r for r in data.get("roles", [])}
        changes["roles"] = [{**previous.get(role["name"], {}), **role} for role in changes["roles"]]
    try:
        merged = s.EnvironmentInput.model_validate({**data, **changes}).model_dump(mode="json", exclude_none=True)
    except ValidationError as exc:
        raise APIError(
            ErrorCode.VALIDATION_ERROR,
            message="Invalid environment update",
            details=[{"field": ".".join(map(str, e["loc"])), "message": e["msg"]} for e in exc.errors()],
        ) from exc
    validate_targets(context.settings, merged)
    update_resource(value, {"encrypted": context.vault.seal(merged)})
    await session.commit()
    return public_environment(env_data(context, value))
