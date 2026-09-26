import uuid
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import responses as out
from ..auth import current_user
from ..config import ROOT
from ..context import Context, get_context
from ..db import get_session
from ..errors import APIError, ErrorCode, not_found
from ..models import Artifact, User
from ..presenters import envelope
from ..store import project_for, resource_for

P = "/api/v1/projects/{projectId}"
router = APIRouter()


def artifact_view(value, projectId):
    return {
        "id": str(value.id),
        "runId": str(value.run_id),
        **{k: v for k, v in value.body.items() if k != "path"},
        "url": f"/api/v1/projects/{projectId}/runs/{value.run_id}/artifacts/{value.id}",
    }


@router.get(P + "/runs/{runId}/artifacts", tags=["Artifacts"], response_model=out.Page[out.ArtifactView])
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


@router.get(P + "/runs/{runId}/artifacts/{artifactId}", tags=["Artifacts"], response_class=FileResponse)
async def download_artifact(
    projectId: uuid.UUID,
    runId: uuid.UUID,
    artifactId: uuid.UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    context: Context = Depends(get_context),
):
    await project_for(session, projectId, user.id)
    await resource_for(session, "run", projectId, runId)
    value = await session.scalar(select(Artifact).where(Artifact.id == artifactId, Artifact.run_id == runId))
    if value is None:
        raise not_found()
    filename = Path(value.body["path"])
    root = (context.settings.data_dir / "runs" / str(runId)).resolve()
    if not filename.exists() or filename.is_symlink() or not filename.resolve().is_relative_to(root):
        raise not_found()
    return FileResponse(
        filename,
        media_type=value.body["contentType"],
        filename=Path(value.body["name"]).name,
        headers={"Content-Security-Policy": "sandbox; default-src 'none'"},
    )


@router.get(P + "/runs/{runId}/trace", tags=["Artifacts"], response_class=RedirectResponse, status_code=307)
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
        raise APIError(ErrorCode.TRACE_NOT_FOUND)
    params = "&".join("trace=" + quote(artifact_view(v, projectId)["url"], safe="") for v in traces)
    return RedirectResponse(f"/api/v1/projects/{projectId}/runs/{runId}/trace/index.html?{params}")


@router.get(P + "/runs/{runId}/trace/{asset:path}", include_in_schema=False)
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
