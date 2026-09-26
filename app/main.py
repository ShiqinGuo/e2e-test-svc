"""Application composition only; HTTP and workflow implementations live in their modules."""

import asyncio
import hashlib
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from sqlalchemy import func, select

from .auth import router as auth_router
from .config import Settings
from .context import Context
from .db import Database
from .http import install_http
from .jobs import Jobs
from .observability import configure_tracing
from .openapi import install_openapi
from .routes import artifacts, environments, projects, recordings, runs, scenarios, system, tenants, viewers
from .runtime import Runtime, RuntimePort
from .security import Vault
from .services.recordings import expire_recordings


def create_app(settings: Settings | None = None, runtime: RuntimePort | None = None) -> FastAPI:
    settings = settings or Settings()
    tracer_provider = configure_tracing()
    db = Database(settings)
    vault = Vault(settings.data_dir)
    runtime = runtime if runtime is not None else Runtime(settings)
    context = Context(settings, db, vault, runtime, Jobs(settings, db, vault, runtime))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        lock_id = int.from_bytes(hashlib.sha256((settings.database_schema or "public").encode()).digest()[:7], "big")
        sweeper = None
        try:
            async with db.engine.connect() as connection:
                acquired = await connection.scalar(select(func.pg_try_advisory_lock(lock_id)))
                await connection.commit()
                if not acquired:
                    raise RuntimeError("Another API controller already owns this database; use one Uvicorn worker")
                try:
                    await context.jobs.recover()
                    sweeper = asyncio.create_task(expire_recordings(context))
                    yield
                finally:
                    if sweeper:
                        sweeper.cancel()
                        await asyncio.gather(sweeper, return_exceptions=True)
                    await context.jobs.close()
                    await connection.execute(select(func.pg_advisory_unlock(lock_id)))
        finally:
            await db.close()

    app = FastAPI(
        title="Web business workflow test platform",
        version="1.0.0",
        lifespan=lifespan,
        openapi_url="/api/openapi.json",
        docs_url="/api/docs",
        redoc_url=None,
    )
    app.state.context = context
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.trusted_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-Request-ID"],
    )
    install_http(app, settings)
    for router in (
        auth_router,
        tenants.router,
        system.router,
        projects.router,
        scenarios.router,
        environments.router,
        runs.router,
        artifacts.router,
        recordings.router,
        viewers.router,
    ):
        app.include_router(router)
    install_openapi(app)
    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=tracer_provider,
        http_capture_headers_server_request=[],
        http_capture_headers_server_response=[],
    )
    return app


app = create_app()
