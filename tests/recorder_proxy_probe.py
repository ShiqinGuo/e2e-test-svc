"""Real Python API + Node bridge + Docker + browser noVNC acceptance in a disposable schema."""

import asyncio
import socket
import tempfile
from pathlib import Path
from uuid import uuid4

import uvicorn
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import ROOT, Settings
from app.main import create_app
from app.migrations import migrate


async def main():
    settings = Settings()
    schema = "recorder_proxy_" + uuid4().hex
    admin = create_async_engine(settings.database_url)
    async with admin.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    base = f"http://127.0.0.1:{listener.getsockname()[1]}"

    async def fixture(reader, writer):
        await reader.read(8192)
        body = b"<!doctype html><title>Private proxy fixture</title><h1>Python authenticated noVNC proxy fixture</h1>"
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: "
            + str(len(body)).encode()
            + b"\r\nConnection: close\r\n\r\n"
            + body
        )
        await writer.drain()
        writer.close()

    fixture_server = await asyncio.start_server(fixture, "0.0.0.0", 0)
    fixture_url = f"http://host.docker.internal:{fixture_server.sockets[0].getsockname()[1]}/"
    try:
        with tempfile.TemporaryDirectory(prefix="e2e-recorder-proxy-") as temp:
            probe = settings.model_copy(
                update={
                    "database_schema": schema,
                    "data_dir": Path(temp),
                    "base_url": base,
                    "trusted_origins": [base],
                    "recording_lifetime": 120,
                }
            )
            await asyncio.to_thread(migrate, probe)
            app = create_app(probe)
            server = uvicorn.Server(uvicorn.Config(app, log_level="warning", access_log=False))
            serving = asyncio.create_task(server.serve(sockets=[listener]))
            try:
                while not server.started:
                    if serving.done():
                        await serving
                    await asyncio.sleep(0.05)
                process = await asyncio.create_subprocess_exec(
                    "node",
                    str(ROOT / "tests/recorder_proxy_browser.mjs"),
                    base,
                    fixture_url,
                    str(ROOT / "runtime/recorder/evidence"),
                    cwd=ROOT,
                )
                assert await asyncio.wait_for(process.wait(), 120) == 0
            finally:
                server.should_exit = True
                await serving
    finally:
        fixture_server.close()
        await fixture_server.wait_closed()
        listener.close()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()


if __name__ == "__main__":
    asyncio.run(main())
