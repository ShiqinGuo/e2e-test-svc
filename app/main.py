import asyncio
import copy
import hashlib
import uuid
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from urllib.parse import quote, urlparse

import httpx
import websockets
from fastapi import Depends, FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.exceptions import HTTPException

from . import schemas as s
from .auth import current_user, public_user, resolve_user
from .auth import router as auth_router
from .config import ROOT, Settings
from .db import Database, get_session
from .errors import APIError, not_found
from .jobs import SUMMARY, TERMINAL, Jobs
from .models import Artifact, Project, Resource, RunEvent, User, utcnow
from .openapi import install_openapi
from .runtime import Runtime
from .security import Vault, public_environment, redact, secrets_of
from .store import create_resource, list_resources, project_for, resource_for, timestamp, update_resource

P = "/api/v1/projects/{projectId}"


def public_run(body):
    return {k: v for k, v in body.items() if k != "encryptedEnvironment"}


def public_recording(body):
    return {k: v for k, v in body.items() if k not in {"encryptedEnvironment", "target", "website", "role"}}


def envelope(items, total, limit, offset):
    return {"items": items, "total": total, "limit": limit, "offset": offset}


def env_data(app, row):
    return {**app.state.vault.open(row.body["encrypted"]), **{k: v for k, v in row.body.items() if k != "encrypted"}}


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
            raise APIError(400, "INVALID_TARGET", "Platform endpoints cannot be configured as test targets")
        if parsed.port in {2375, 2376, 5432, 55432}:
            raise APIError(400, "INVALID_TARGET", "Infrastructure endpoints cannot be configured as test targets")


def validate_role(environment, role):
    if role and role not in [r["name"] for r in environment.get("roles", [])]:
        raise APIError(400, "INVALID_ROLE", "Role does not exist in this environment")


async def add_version(session, scenario, body, user):
    # Caller locks the scenario; sequence+FK constraints independently protect concurrent version creation.
    number = (
        await session.scalar(select(func.max(Resource.version_number)).where(Resource.parent_id == scenario.id))
    ) or 0
    version = create_resource(
        session,
        "version",
        scenario.project_id,
        {**body, "scenarioId": str(scenario.id), "number": number + 1, "createdBy": str(user.id)},
        scenario.id,
        number + 1,
    )
    await session.flush()
    update_resource(scenario, {"currentVersionId": str(version.id)})
    await session.commit()
    return version.body


def create_app(settings: Settings | None = None, runtime=None):
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app):
        # One API controller per database/schema. Pg advisory lock survives request transactions.
        connection = await app.state.db.engine.connect()
        lock_id = int.from_bytes(hashlib.sha256((settings.database_schema or "public").encode()).digest()[:7], "big")
        acquired = await connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": lock_id})
        await connection.commit()
        if not acquired:
            await connection.close()
            raise RuntimeError("Another API controller already owns this database; use one Uvicorn worker")
        await app.state.jobs.recover()
        sweeper = asyncio.create_task(expire_recordings(app))
        try:
            yield
        finally:
            sweeper.cancel()
            await asyncio.gather(sweeper, return_exceptions=True)
            await app.state.jobs.close()
            await connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": lock_id})
            await connection.close()
            await app.state.db.close()

    app = FastAPI(
        title="Web business workflow test platform",
        version="1.0.0",
        lifespan=lifespan,
        openapi_url="/api/openapi.json",
        docs_url="/api/docs",
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.db = Database(settings)
    app.state.vault = Vault(settings.data_dir)
    app.state.runtime = runtime if runtime is not None else Runtime(settings)
    app.state.jobs = Jobs(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.trusted_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
        allow_headers=["Content-Type", "X-Request-ID"],
    )

    @app.middleware("http")
    async def request_boundary(request, call_next):
        request.state.request_id = str(uuid.uuid4())
        origin = request.headers.get("origin")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and origin and origin not in settings.trusted_origins:
            return error_response(request, APIError(403, "ORIGIN_FORBIDDEN", "Request origin is not trusted"))
        if request.method not in {"GET", "HEAD", "OPTIONS"} and request.headers.get("sec-fetch-site") == "cross-site":
            return error_response(request, APIError(403, "ORIGIN_FORBIDDEN", "Cross-site writes are forbidden"))
        if int(request.headers.get("content-length", "0") or 0) > 2 * 1024 * 1024:
            return error_response(request, APIError(413, "PAYLOAD_TOO_LARGE", "Request body exceeds 2 MiB"))
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    def error_response(request, exc):
        if request.url.path.startswith("/api/auth/"):
            body = {"code": exc.code, "message": exc.message}
        else:
            body = {
                "error": {"code": exc.code, "message": exc.message},
                "requestId": getattr(request.state, "request_id", ""),
            }
            if exc.details is not None:
                body["error"]["details"] = exc.details
        return JSONResponse(body, status_code=exc.status)

    @app.exception_handler(APIError)
    async def api_error(request, exc):
        return error_response(request, exc)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Do not echo Pydantic 'input': it can contain passwords or environment credentials.
        details = [
            {"field": ".".join(map(str, e["loc"])), "message": e["msg"], "type": e["type"]} for e in exc.errors()
        ]
        return error_response(request, APIError(400, "VALIDATION_ERROR", "Invalid request", details))

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return error_response(
            request,
            APIError(
                exc.status_code,
                "NOT_FOUND" if exc.status_code == 404 else "HTTP_ERROR",
                "Resource not found" if exc.status_code == 404 else str(exc.detail),
            ),
        )

    @app.exception_handler(IntegrityError)
    async def integrity_error(request, exc):
        return error_response(
            request, APIError(409, "CONFLICT", "A conflicting resource already exists; refresh and retry")
        )

    app.include_router(auth_router)

    @app.get("/api/health", tags=["System"])
    async def health():
        return {"status": "ok"}

    @app.get("/api/v1/me", tags=["Authentication"])
    async def me(user: User = Depends(current_user)):
        return {"user": public_user(user)}

    @app.get("/api/v1/capabilities", tags=["System"])
    async def capabilities(user: User = Depends(current_user)):
        return {"playwrightVersion": "1.63.0", **await app.state.runtime.available(), "databaseChecks": "api-only"}

    @app.get("/api/v1/projects", tags=["Projects"])
    async def projects(
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0),
        user: User = Depends(current_user),
        session: AsyncSession = Depends(get_session),
    ):
        statement = select(Project).where(Project.owner_id == user.id)
        total = await session.scalar(select(func.count()).select_from(statement.subquery()))
        values = (
            await session.scalars(statement.order_by(Project.created_at.desc(), Project.id).limit(limit).offset(offset))
        ).all()
        return envelope([v.body for v in values], total, limit, offset)

    @app.post("/api/v1/projects", status_code=201, tags=["Projects"])
    async def create_project(
        body: s.ProjectInput, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
    ):
        ident = uuid.uuid4()
        value = Project(
            id=ident,
            owner_id=user.id,
            body={
                **body.model_dump(),
                "id": str(ident),
                "ownerId": str(user.id),
                "createdAt": timestamp(),
                "updatedAt": timestamp(),
            },
        )
        session.add(value)
        await session.commit()
        return value.body

    @app.get(P, tags=["Projects"])
    async def get_project(
        projectId: uuid.UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
    ):
        return (await project_for(session, projectId, user.id)).body

    @app.patch(P, tags=["Projects"])
    async def patch_project(
        projectId: uuid.UUID,
        body: s.ProjectPatch,
        user: User = Depends(current_user),
        session: AsyncSession = Depends(get_session),
    ):
        value = await project_for(session, projectId, user.id)
        value.body = {**value.body, **body.model_dump(exclude_unset=True), "updatedAt": timestamp()}
        await session.commit()
        return value.body

    @app.get(P + "/groups", tags=["Groups"])
    async def groups(
        projectId: uuid.UUID,
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0),
        user: User = Depends(current_user),
        session: AsyncSession = Depends(get_session),
    ):
        await project_for(session, projectId, user.id)
        return await list_resources(session, "group", projectId, limit, offset)

    @app.post(P + "/groups", status_code=201, tags=["Groups"])
    async def create_group(
        projectId: uuid.UUID,
        body: s.ProjectInput,
        user: User = Depends(current_user),
        session: AsyncSession = Depends(get_session),
    ):
        await project_for(session, projectId, user.id)
        value = create_resource(session, "group", projectId, body.model_dump())
        await session.commit()
        return value.body

    @app.get(P + "/groups/{groupId}", tags=["Groups"])
    async def get_group(
        projectId: uuid.UUID,
        groupId: uuid.UUID,
        user: User = Depends(current_user),
        session: AsyncSession = Depends(get_session),
    ):
        await project_for(session, projectId, user.id)
        return (await resource_for(session, "group", projectId, groupId)).body

    @app.patch(P + "/groups/{groupId}", tags=["Groups"])
    async def patch_group(
        projectId: uuid.UUID,
        groupId: uuid.UUID,
        body: s.ProjectPatch,
        user: User = Depends(current_user),
        session: AsyncSession = Depends(get_session),
    ):
        await project_for(session, projectId, user.id)
        value = await resource_for(session, "group", projectId, groupId, lock=True)
        update_resource(value, body.model_dump(exclude_unset=True))
        await session.commit()
        return value.body

    @app.get(P + "/scenarios", tags=["Scenarios"])
    async def scenarios(
        projectId: uuid.UUID,
        groupId: uuid.UUID | None = None,
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0),
        user: User = Depends(current_user),
        session: AsyncSession = Depends(get_session),
    ):
        await project_for(session, projectId, user.id)
        if groupId:
            await resource_for(session, "group", projectId, groupId)
        return await list_resources(session, "scenario", projectId, limit, offset, groupId)

    @app.post(P + "/scenarios", status_code=201, tags=["Scenarios"])
    async def create_scenario(
        projectId: uuid.UUID,
        body: s.ScenarioInput,
        user: User = Depends(current_user),
        session: AsyncSession = Depends(get_session),
    ):
        await project_for(session, projectId, user.id)
        if body.groupId:
            await resource_for(session, "group", projectId, body.groupId)
        value = create_resource(
            session, "scenario", projectId, {**body.model_dump(mode="json"), "currentVersionId": None}, body.groupId
        )
        await session.commit()
        return value.body

    @app.get(P + "/scenarios/{scenarioId}", tags=["Scenarios"])
    async def get_scenario(
        projectId: uuid.UUID,
        scenarioId: uuid.UUID,
        user: User = Depends(current_user),
        session: AsyncSession = Depends(get_session),
    ):
        await project_for(session, projectId, user.id)
        return (await resource_for(session, "scenario", projectId, scenarioId)).body

    @app.patch(P + "/scenarios/{scenarioId}", tags=["Scenarios"])
    async def patch_scenario(
        projectId: uuid.UUID,
        scenarioId: uuid.UUID,
        body: s.ScenarioPatch,
        user: User = Depends(current_user),
        session: AsyncSession = Depends(get_session),
    ):
        await project_for(session, projectId, user.id)
        value = await resource_for(session, "scenario", projectId, scenarioId, lock=True)
        if "groupId" in body.model_fields_set:
            if body.groupId:
                await resource_for(session, "group", projectId, body.groupId)
            value.parent_id = body.groupId
        update_resource(value, body.model_dump(mode="json", exclude_unset=True))
        await session.commit()
        return value.body

    @app.get(P + "/scenarios/{scenarioId}/versions", tags=["Versions"])
    async def versions(
        projectId: uuid.UUID,
        scenarioId: uuid.UUID,
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0),
        user: User = Depends(current_user),
        session: AsyncSession = Depends(get_session),
    ):
        await project_for(session, projectId, user.id)
        await resource_for(session, "scenario", projectId, scenarioId)
        return await list_resources(session, "version", projectId, limit, offset, scenarioId)

    @app.post(P + "/scenarios/{scenarioId}/versions", status_code=201, tags=["Versions"])
    async def create_version(
        projectId: uuid.UUID,
        scenarioId: uuid.UUID,
        body: s.VersionInput,
        user: User = Depends(current_user),
        session: AsyncSession = Depends(get_session),
    ):
        await project_for(session, projectId, user.id)
        scenario = await resource_for(session, "scenario", projectId, scenarioId, lock=True)
        return await add_version(session, scenario, body.model_dump(mode="json", exclude_none=True), user)

    @app.get(P + "/scenarios/{scenarioId}/versions/{versionId}", tags=["Versions"])
    async def get_version(
        projectId: uuid.UUID,
        scenarioId: uuid.UUID,
        versionId: uuid.UUID,
        user: User = Depends(current_user),
        session: AsyncSession = Depends(get_session),
    ):
        await project_for(session, projectId, user.id)
        await resource_for(session, "scenario", projectId, scenarioId)
        return (await resource_for(session, "version", projectId, versionId, parent_id=scenarioId)).body

    @app.get(P + "/environments", tags=["Environments"])
    async def environments(
        projectId: uuid.UUID,
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0),
        user: User = Depends(current_user),
        session: AsyncSession = Depends(get_session),
    ):
        await project_for(session, projectId, user.id)
        result = await list_resources(session, "environment", projectId, limit, offset)
        result["items"] = [
            public_environment(
                {**app.state.vault.open(v["encrypted"]), **{k: val for k, val in v.items() if k != "encrypted"}}
            )
            for v in result["items"]
        ]
        return result

    @app.post(P + "/environments", status_code=201, tags=["Environments"])
    async def create_environment(
        projectId: uuid.UUID,
        body: s.EnvironmentInput,
        user: User = Depends(current_user),
        session: AsyncSession = Depends(get_session),
    ):
        await project_for(session, projectId, user.id)
        data = body.model_dump(mode="json", exclude_none=True)
        validate_targets(settings, data)
        value = create_resource(session, "environment", projectId, {"encrypted": app.state.vault.seal(data)})
        await session.commit()
        return public_environment(env_data(app, value))

    @app.get(P + "/environments/{environmentId}", tags=["Environments"])
    async def get_environment(
        projectId: uuid.UUID,
        environmentId: uuid.UUID,
        user: User = Depends(current_user),
        session: AsyncSession = Depends(get_session),
    ):
        await project_for(session, projectId, user.id)
        return public_environment(env_data(app, await resource_for(session, "environment", projectId, environmentId)))

    @app.patch(P + "/environments/{environmentId}", tags=["Environments"])
    async def patch_environment(
        projectId: uuid.UUID,
        environmentId: uuid.UUID,
        body: s.EnvironmentPatch,
        user: User = Depends(current_user),
        session: AsyncSession = Depends(get_session),
    ):
        await project_for(session, projectId, user.id)
        value = await resource_for(session, "environment", projectId, environmentId, lock=True)
        data = app.state.vault.open(value.body["encrypted"])
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
                400,
                "VALIDATION_ERROR",
                "Invalid environment update",
                [{"field": ".".join(map(str, e["loc"])), "message": e["msg"]} for e in exc.errors()],
            ) from exc
        validate_targets(settings, merged)
        update_resource(value, {"encrypted": app.state.vault.seal(merged)})
        await session.commit()
        return public_environment(env_data(app, value))

    @app.get(P + "/runs", tags=["Runs"])
    async def runs(
        projectId: uuid.UUID,
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0),
        user: User = Depends(current_user),
        session: AsyncSession = Depends(get_session),
    ):
        await project_for(session, projectId, user.id)
        result = await list_resources(session, "run", projectId, limit, offset)
        result["items"] = [public_run(v) for v in result["items"]]
        return result

    @app.post(P + "/runs", status_code=202, tags=["Runs"])
    async def create_run(
        projectId: uuid.UUID,
        body: s.RunInput,
        user: User = Depends(current_user),
        session: AsyncSession = Depends(get_session),
    ):
        await project_for(session, projectId, user.id)
        env = env_data(app, await resource_for(session, "environment", projectId, body.environmentId))
        validate_role(env, body.role)
        snapshots = []
        if body.scenarioId:
            scenarios = [await resource_for(session, "scenario", projectId, body.scenarioId)]
        else:
            await resource_for(session, "group", projectId, body.groupId)
            scenarios = (
                await session.scalars(
                    select(Resource)
                    .where(
                        Resource.kind == "scenario",
                        Resource.project_id == projectId,
                        Resource.parent_id == body.groupId,
                    )
                    .order_by(Resource.created_at)
                )
            ).all()
        if not scenarios:
            raise APIError(409, "EMPTY_GROUP", "This group has no scenarios")
        for scenario in scenarios:
            version_id = body.versionId or scenario.body["currentVersionId"]
            if not version_id:
                raise APIError(409, "MISSING_VERSION", "Every scenario must have a saved version")
            snapshots.append(
                (
                    await resource_for(session, "version", projectId, uuid.UUID(str(version_id)), parent_id=scenario.id)
                ).body
            )
        capability = await app.state.runtime.available()
        if not capability["runner"]["available"]:
            raise APIError(503, "RUNTIME_UNAVAILABLE", capability["runner"].get("reason", "Runner unavailable"))
        value = create_resource(
            session,
            "run",
            projectId,
            {
                **body.model_dump(mode="json", exclude={"versionId"}),
                "versions": snapshots,
                "environmentSnapshot": public_environment(env),
                "encryptedEnvironment": app.state.vault.seal(env),
                "status": "queued",
                "verification": "pending",
                "summary": dict(SUMMARY),
                "startedAt": None,
                "finishedAt": None,
                "error": None,
            },
        )
        await session.flush()
        session.add(RunEvent(run_id=value.id, type="run.queued", timestamp=timestamp(), data={"runId": str(value.id)}))
        await session.commit()
        app.state.jobs.schedule(value.id)
        return public_run(value.body)

    @app.get(P + "/runs/{runId}", tags=["Runs"])
    async def get_run(
        projectId: uuid.UUID,
        runId: uuid.UUID,
        user: User = Depends(current_user),
        session: AsyncSession = Depends(get_session),
    ):
        await project_for(session, projectId, user.id)
        return public_run((await resource_for(session, "run", projectId, runId)).body)

    @app.post(P + "/runs/{runId}/rerun", status_code=202, tags=["Runs"])
    async def rerun(
        projectId: uuid.UUID,
        runId: uuid.UUID,
        user: User = Depends(current_user),
        session: AsyncSession = Depends(get_session),
    ):
        await project_for(session, projectId, user.id)
        original = await resource_for(session, "run", projectId, runId)
        capability = await app.state.runtime.available()
        if not capability["runner"]["available"]:
            raise APIError(503, "RUNTIME_UNAVAILABLE", capability["runner"].get("reason", "Runner unavailable"))
        body = {k: copy.deepcopy(v) for k, v in original.body.items() if k not in {"id", "createdAt", "updatedAt"}}
        body.update(
            {
                "status": "queued",
                "verification": "pending",
                "summary": dict(SUMMARY),
                "startedAt": None,
                "finishedAt": None,
                "error": None,
                "rerunOf": str(runId),
                "sourceRunId": str(runId),
            }
        )
        value = create_resource(session, "run", projectId, body)
        await session.flush()
        session.add(
            RunEvent(
                run_id=value.id,
                type="run.queued",
                timestamp=timestamp(),
                data={"runId": str(value.id), "rerunOf": str(runId)},
            )
        )
        await session.commit()
        app.state.jobs.schedule(value.id)
        return public_run(value.body)

    @app.post(P + "/runs/{runId}/cancel", tags=["Runs"])
    async def cancel_run(
        projectId: uuid.UUID,
        runId: uuid.UUID,
        user: User = Depends(current_user),
        session: AsyncSession = Depends(get_session),
    ):
        await project_for(session, projectId, user.id)
        value = await resource_for(session, "run", projectId, runId, lock=True)
        if value.body["status"] not in TERMINAL:
            update_resource(value, {"status": "cancelled", "finishedAt": timestamp()})
            session.add(RunEvent(run_id=runId, type="run.cancelled", timestamp=timestamp(), data={"runId": str(runId)}))
            await session.commit()
            await app.state.runtime.cancel(str(runId))
        return public_run(value.body)

    @app.get(P + "/runs/{runId}/events", tags=["Runs"])
    async def events(
        projectId: uuid.UUID,
        runId: uuid.UUID,
        after: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=100),
        user: User = Depends(current_user),
        session: AsyncSession = Depends(get_session),
    ):
        await project_for(session, projectId, user.id)
        await resource_for(session, "run", projectId, runId)
        values = (
            await session.scalars(
                select(RunEvent).where(RunEvent.run_id == runId, RunEvent.id > after).order_by(RunEvent.id).limit(limit)
            )
        ).all()
        return {
            "items": [{"seq": v.id, "type": v.type, "timestamp": v.timestamp, "data": v.data} for v in values],
            "nextAfter": values[-1].id if values else after,
        }

    def artifact_view(value, projectId):
        return {
            "id": str(value.id),
            "runId": str(value.run_id),
            **{k: v for k, v in value.body.items() if k != "path"},
            "url": f"/api/v1/projects/{projectId}/runs/{value.run_id}/artifacts/{value.id}",
        }

    @app.get(P + "/runs/{runId}/artifacts", tags=["Artifacts"])
    async def artifacts(
        projectId: uuid.UUID,
        runId: uuid.UUID,
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0),
        user: User = Depends(current_user),
        session: AsyncSession = Depends(get_session),
    ):
        await project_for(session, projectId, user.id)
        await resource_for(session, "run", projectId, runId)
        statement = select(Artifact).where(Artifact.run_id == runId)
        total = await session.scalar(select(func.count()).select_from(statement.subquery()))
        values = (await session.scalars(statement.order_by(Artifact.id).limit(limit).offset(offset))).all()
        return envelope([artifact_view(v, projectId) for v in values], total, limit, offset)

    @app.get(P + "/runs/{runId}/artifacts/{artifactId}", tags=["Artifacts"])
    async def download_artifact(
        projectId: uuid.UUID,
        runId: uuid.UUID,
        artifactId: uuid.UUID,
        user: User = Depends(current_user),
        session: AsyncSession = Depends(get_session),
    ):
        await project_for(session, projectId, user.id)
        await resource_for(session, "run", projectId, runId)
        value = await session.scalar(select(Artifact).where(Artifact.id == artifactId, Artifact.run_id == runId))
        if value is None:
            raise not_found()
        filename = Path(value.body["path"])
        root = (settings.data_dir / "runs" / str(runId)).resolve()
        if not filename.exists() or filename.is_symlink() or not filename.resolve().is_relative_to(root):
            raise not_found()
        return FileResponse(
            filename,
            media_type=value.body["contentType"],
            filename=Path(value.body["name"]).name,
            headers={"Content-Security-Policy": "sandbox; default-src 'none'"},
        )

    @app.get(P + "/runs/{runId}/trace", tags=["Artifacts"])
    async def trace(
        projectId: uuid.UUID,
        runId: uuid.UUID,
        user: User = Depends(current_user),
        session: AsyncSession = Depends(get_session),
    ):
        await project_for(session, projectId, user.id)
        await resource_for(session, "run", projectId, runId)
        values = (await session.scalars(select(Artifact).where(Artifact.run_id == runId))).all()
        traces = [v for v in values if v.body["kind"] == "trace"]
        if not traces:
            raise APIError(404, "TRACE_NOT_FOUND", "No trace was produced for this run")
        params = "&".join("trace=" + quote(artifact_view(v, projectId)["url"], safe="") for v in traces)
        return RedirectResponse(f"/api/v1/projects/{projectId}/runs/{runId}/trace/index.html?{params}")

    @app.get(P + "/runs/{runId}/trace/{asset:path}", include_in_schema=False)
    async def trace_asset(
        projectId: uuid.UUID,
        runId: uuid.UUID,
        asset: str,
        user: User = Depends(current_user),
        session: AsyncSession = Depends(get_session),
    ):
        await project_for(session, projectId, user.id)
        await resource_for(session, "run", projectId, runId)
        root = (ROOT / "node_modules/playwright-core/lib/vite/traceViewer").resolve()
        filename = (root / asset).resolve()
        if not filename.is_relative_to(root) or not filename.is_file():
            raise not_found()
        return FileResponse(
            filename,
            headers={
                "Content-Security-Policy": "default-src 'self' blob: data:; script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; frame-ancestors 'self'"
            },
        )

    @app.get(P + "/recordings", tags=["Recordings"])
    async def recordings(
        projectId: uuid.UUID,
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0),
        user: User = Depends(current_user),
        session: AsyncSession = Depends(get_session),
    ):
        await project_for(session, projectId, user.id)
        result = await list_resources(session, "recording", projectId, limit, offset)
        result["items"] = [public_recording(v) for v in result["items"]]
        return result

    @app.post(P + "/recordings", status_code=202, tags=["Recordings"])
    async def create_recording(
        projectId: uuid.UUID,
        body: s.RecordingInput,
        user: User = Depends(current_user),
        session: AsyncSession = Depends(get_session),
    ):
        await project_for(session, projectId, user.id)
        env = env_data(app, await resource_for(session, "environment", projectId, body.environmentId))
        if body.scenarioId:
            await resource_for(session, "scenario", projectId, body.scenarioId)
        validate_role(env, body.role)
        if body.website not in env["websites"]:
            raise APIError(400, "INVALID_WEBSITE", "Named website does not exist in this environment")
        capability = await app.state.runtime.available()
        if not capability["recorder"]["available"]:
            raise APIError(503, "RUNTIME_UNAVAILABLE", capability["recorder"].get("reason", "Recorder unavailable"))
        await session.execute(select(User).where(User.id == user.id).with_for_update())
        active = await session.scalar(
            select(func.count())
            .select_from(Resource)
            .join(Project, Resource.project_id == Project.id)
            .where(
                Resource.kind == "recording",
                Project.owner_id == user.id,
                Resource.body["status"].astext.in_(["starting", "ready"]),
            )
        )
        if active >= settings.max_recordings_per_user:
            raise APIError(409, "RECORDING_LIMIT", "Stop an existing recording before starting another")
        value = create_resource(
            session,
            "recording",
            projectId,
            {
                **body.model_dump(mode="json"),
                "status": "starting",
                "expiresAt": (utcnow() + timedelta(seconds=settings.recording_lifetime)).isoformat(),
                "viewerUrl": None,
                "code": "",
                "checks": [],
                "error": None,
                "encryptedEnvironment": app.state.vault.seal(env),
            },
        )
        await session.commit()
        task = asyncio.create_task(start_recording(app, value.id))
        app.state.jobs.tasks[str(value.id)] = task
        task.add_done_callback(lambda _: app.state.jobs.tasks.pop(str(value.id), None))
        return public_recording(value.body)

    @app.get(P + "/recordings/{recordingId}", tags=["Recordings"])
    async def get_recording(
        projectId: uuid.UUID,
        recordingId: uuid.UUID,
        user: User = Depends(current_user),
        session: AsyncSession = Depends(get_session),
    ):
        await project_for(session, projectId, user.id)
        value = await resource_for(session, "recording", projectId, recordingId)
        if value.body["status"] == "ready":
            environment = app.state.vault.open(value.body["encryptedEnvironment"])
            # Do not hold the DB lock while codegen responds. Re-read under lock before publishing the poll.
            await session.commit()
            error = None
            try:
                result = await app.state.runtime.recording_read(str(recordingId))
                target = await app.state.runtime.recording_target(str(recordingId))
                if not target:
                    raise RuntimeError("Recorder is no longer running")
            except Exception:
                error = "Recorder is unavailable"
            value = await resource_for(session, "recording", projectId, recordingId, lock=True)
            if value.body["status"] == "ready":
                if error:
                    update_resource(value, {"status": "error", "error": error, "viewerUrl": None})
                else:
                    update_resource(value, redact(result, secrets_of(environment)))
            await session.commit()
        return public_recording(value.body)

    @app.post(P + "/recordings/{recordingId}/stop", tags=["Recordings"])
    async def stop_recording(
        projectId: uuid.UUID,
        recordingId: uuid.UUID,
        user: User = Depends(current_user),
        session: AsyncSession = Depends(get_session),
    ):
        await project_for(session, projectId, user.id)
        value = await resource_for(session, "recording", projectId, recordingId, lock=True)
        await finish_recording(app, session, value)
        return public_recording(value.body)

    @app.post(P + "/recordings/{recordingId}/save", status_code=201, tags=["Recordings"])
    async def save_recording(
        projectId: uuid.UUID,
        recordingId: uuid.UUID,
        body: s.SaveRecording,
        user: User = Depends(current_user),
        session: AsyncSession = Depends(get_session),
    ):
        await project_for(session, projectId, user.id)
        value = await resource_for(session, "recording", projectId, recordingId, lock=True)
        scenario = await resource_for(session, "scenario", projectId, body.scenarioId, lock=True)
        await finish_recording(app, session, value)
        if value.body["status"] != "stopped":
            raise APIError(409, "RECORDING_FLUSH_FAILED", "Recording did not stop and flush successfully; no version was created")
        if not value.body["code"]:
            raise APIError(409, "EMPTY_RECORDING", "Recording has no generated code")
        version = s.VersionInput(
            code=value.body["code"], source="recording", changeNote=body.changeNote, checks=value.body["checks"]
        )
        # finish_recording commits; reacquire lock for atomic version numbering.
        scenario = await resource_for(session, "scenario", projectId, body.scenarioId, lock=True)
        return await add_version(session, scenario, version.model_dump(mode="json", exclude_none=True), user)

    @app.get(P + "/recordings/{recordingId}/viewer", include_in_schema=False)
    async def recording_viewer_redirect(
        projectId: uuid.UUID,
        recordingId: uuid.UUID,
        user: User = Depends(current_user),
        session: AsyncSession = Depends(get_session),
    ):
        await project_for(session, projectId, user.id)
        await resource_for(session, "recording", projectId, recordingId)
        return RedirectResponse(f"/api/v1/projects/{projectId}/recordings/{recordingId}/viewer/index.html")

    @app.get(P + "/recordings/{recordingId}/viewer/{asset:path}", include_in_schema=False)
    async def recording_asset(
        projectId: uuid.UUID,
        recordingId: uuid.UUID,
        asset: str,
        user: User = Depends(current_user),
        session: AsyncSession = Depends(get_session),
    ):
        await project_for(session, projectId, user.id)
        value = await resource_for(session, "recording", projectId, recordingId)
        if value.body["status"] != "ready":
            raise APIError(409, "RECORDING_NOT_READY", "Recording is not ready")
        if asset not in {"index.html", "viewer.js"}:
            raise not_found()
        if ".." in Path(asset).parts or "\\" in asset:
            raise not_found()
        target = await app.state.runtime.recording_target(str(recordingId))
        if not target:
            raise APIError(409, "RECORDING_NOT_READY", "Recording is no longer running")
        async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
            response = await client.get(
                f"http://{target['host']}:{target['port']}/{asset}", headers={"x-recorder-token": target["token"]}
            )
        if response.status_code != 200:
            raise not_found()
        return Response(
            response.content,
            media_type=response.headers.get("content-type", "application/octet-stream"),
            headers={
                "Content-Security-Policy": "default-src 'self' blob: data:; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self' ws: wss:; frame-ancestors 'self'"
            },
        )

    @app.websocket(P + "/recordings/{recordingId}/viewer/websockify")
    async def recording_socket(socket: WebSocket, projectId: uuid.UUID, recordingId: uuid.UUID):
        origin = socket.headers.get("origin")
        if origin not in settings.trusted_origins:
            await socket.close(code=1008)
            return
        async with app.state.db.sessions() as session:
            user = await resolve_user(socket, session)
            if user is None:
                await socket.close(code=1008)
                return
            try:
                await project_for(session, projectId, user.id)
                value = await resource_for(session, "recording", projectId, recordingId)
                if value.body["status"] != "ready":
                    raise not_found()
            except APIError:
                await socket.close(code=1008)
                return
        target = await app.state.runtime.recording_target(str(recordingId))
        if not target:
            await socket.close(code=1008)
            return
        try:
            async with websockets.connect(
                f"ws://{target['host']}:{target['port']}/websockify",
                additional_headers={"x-recorder-token": target["token"]},
                max_size=16 * 1024 * 1024,
                proxy=None,
                subprotocols=["binary"],
            ) as upstream:
                await socket.accept(
                    subprotocol="binary" if "binary" in socket.headers.get("sec-websocket-protocol", "") else None
                )

                async def send_upstream():
                    while True:
                        message = await socket.receive()
                        if message["type"] == "websocket.disconnect":
                            break
                        await upstream.send(message.get("bytes") or message.get("text") or b"")

                async def send_browser():
                    async for message in upstream:
                        if isinstance(message, bytes):
                            await socket.send_bytes(message)
                        else:
                            await socket.send_text(message)

                async def reauthorize():
                    while True:
                        await asyncio.sleep(5)
                        async with app.state.db.sessions() as session:
                            active_user = await resolve_user(socket, session)
                            active_recording = await session.get(Resource, recordingId)
                            if (
                                active_user is None
                                or not active_recording
                                or active_recording.body["status"] != "ready"
                            ):
                                return

                tasks = [asyncio.create_task(f()) for f in (send_upstream, send_browser, reauthorize)]
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        except (WebSocketDisconnect, websockets.ConnectionClosed, OSError):
            pass
        finally:
            try:
                await socket.close()
            except (RuntimeError, WebSocketDisconnect):
                pass

    install_openapi(app)
    return app


async def start_recording(app, ident):
    env = {}
    try:
        async with app.state.db.sessions() as session:
            value = await session.get(Resource, ident)
            body = copy.deepcopy(value.body)
        env = app.state.vault.open(body["encryptedEnvironment"])
        directory = app.state.settings.data_dir / "recordings" / str(ident)
        directory.mkdir(parents=True, exist_ok=True)
        await app.state.runtime.recording_start(
            {
                "id": str(ident),
                "workDir": str(directory.resolve()),
                "url": env["websites"][body["website"]],
                "website": body["website"],
                "environment": env,
                "role": body["role"],
                "expiresAt": body["expiresAt"],
                "platformOrigins": app.state.settings.trusted_origins,
            }
        )
        async with app.state.db.sessions() as session:
            value = await session.get(Resource, ident, with_for_update=True)
            if value.body["status"] == "starting":
                update_resource(
                    value,
                    {
                        "status": "ready",
                        "viewerUrl": f"/api/v1/projects/{value.project_id}/recordings/{ident}/viewer/index.html",
                    },
                )
                await session.commit()
            else:
                await app.state.runtime.recording_stop(str(ident))
    except Exception as exc:
        async with app.state.db.sessions() as session:
            value = await session.get(Resource, ident, with_for_update=True)
            if value.body["status"] in {"starting", "ready"}:
                update_resource(
                    value, {"status": "error", "error": redact(str(exc), secrets_of(env))[:2000], "viewerUrl": None}
                )
                await session.commit()


async def finish_recording(app, session, value):
    if value.body["status"] in {"ready", "starting"}:
        try:
            result = await app.state.runtime.recording_stop(str(value.id))
            env = app.state.vault.open(value.body["encryptedEnvironment"])
            update_resource(value, {**redact(result, secrets_of(env)), "status": "stopped", "viewerUrl": None})
        except Exception:
            update_resource(
                value, {"status": "error", "error": "Recording could not be stopped cleanly", "viewerUrl": None}
            )
        await session.commit()


async def expire_recordings(app):
    while True:
        await asyncio.sleep(15)
        async with app.state.db.sessions() as session:
            values = (
                await session.scalars(
                    select(Resource).where(
                        Resource.kind == "recording", Resource.body["status"].astext.in_(["starting", "ready"])
                    )
                )
            ).all()
            for value in values:
                if value.body["expiresAt"] <= timestamp():
                    locked = await session.get(Resource, value.id, with_for_update=True, populate_existing=True)
                    await finish_recording(app, session, locked)


app = create_app()
