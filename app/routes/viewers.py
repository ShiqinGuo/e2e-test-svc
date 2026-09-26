import asyncio
import uuid
from pathlib import Path

import httpx
import websockets
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import current_user, resolve_user
from ..context import Context, get_context
from ..db import get_session
from ..domain import Permission
from ..errors import APIError, ErrorCode, not_found
from ..models import Resource, User
from ..store import project_for, resource_for

P = "/api/v1/projects/{projectId}"
router = APIRouter()


@router.get(P + "/recordings/{recordingId}/viewer", include_in_schema=False)
async def recording_viewer_redirect(
    projectId: uuid.UUID,
    recordingId: uuid.UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    await project_for(session, projectId, user.id, Permission.EDIT)
    await resource_for(session, "recording", projectId, recordingId)
    return RedirectResponse(f"/api/v1/projects/{projectId}/recordings/{recordingId}/viewer/index.html")


@router.get(P + "/recordings/{recordingId}/viewer/{asset:path}", include_in_schema=False)
async def recording_asset(
    projectId: uuid.UUID,
    recordingId: uuid.UUID,
    asset: str,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    context: Context = Depends(get_context),
):
    await project_for(session, projectId, user.id, Permission.EDIT)
    value = await resource_for(session, "recording", projectId, recordingId)
    if value.body["status"] != "ready":
        raise APIError(ErrorCode.RECORDING_NOT_READY)
    if asset not in {"index.html", "viewer.js"}:
        raise not_found()
    if ".." in Path(asset).parts or "\\" in asset:
        raise not_found()
    await session.commit()
    target = await context.runtime.recording_target(str(recordingId))
    if not target:
        raise APIError(ErrorCode.RECORDING_NOT_READY, message="Recording is no longer running")
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


@router.websocket(P + "/recordings/{recordingId}/viewer/websockify")
async def recording_socket(
    socket: WebSocket,
    projectId: uuid.UUID,
    recordingId: uuid.UUID,
    context: Context = Depends(get_context),
):
    origin = socket.headers.get("origin")
    if origin not in context.settings.trusted_origins:
        await socket.close(code=1008)
        return
    async with context.db.sessions() as session:
        user = await resolve_user(socket, session)
        if user is None:
            await socket.close(code=1008)
            return
        try:
            await project_for(session, projectId, user.id, Permission.EDIT)
            value = await resource_for(session, "recording", projectId, recordingId)
            if value.body["status"] != "ready":
                raise not_found()
        except APIError:
            await socket.close(code=1008)
            return
    target = await context.runtime.recording_target(str(recordingId))
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
                    async with context.db.sessions() as session:
                        active_user = await resolve_user(socket, session)
                        active_recording = await session.get(Resource, recordingId)
                        if active_user is None or not active_recording or active_recording.body["status"] != "ready":
                            return
                        try:
                            await project_for(session, projectId, active_user.id, Permission.EDIT)
                        except APIError:
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
