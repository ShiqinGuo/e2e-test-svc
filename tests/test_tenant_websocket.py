"""A real WebSocket handshake/relay must obey live organization membership."""

import asyncio
import socket

import pytest
import uvicorn
import websockets
from test_api import seed
from test_tenants import join
from websockets.exceptions import ConnectionClosed, InvalidStatus

pytest_plugins = ("test_api",)


@pytest.mark.asyncio
async def test_recorder_socket_rejects_viewer_and_disconnects_revoked_membership(api):
    _, owner, member, runtime = api
    resources = await seed(owner)
    organization_id = resources.project["organizationId"]
    _, _, account = await join(owner, organization_id, member, "viewer")
    recording = (
        await owner.post(
            resources.path + "/recordings", json={"environmentId": resources.environment["id"], "website": "main"}
        )
    ).json()
    url = resources.path + "/recordings/" + recording["id"]
    async with asyncio.timeout(5):
        while (await owner.get(url)).json()["status"] != "ready":
            await asyncio.sleep(0.01)

    async def echo(connection):
        assert "Cookie" not in connection.request.headers
        assert connection.request.headers["x-recorder-token"] == "private-upstream-token"
        async for data in connection:
            await connection.send(data)

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    server = uvicorn.Server(uvicorn.Config(runtime.app, lifespan="off", log_level="warning", access_log=False))
    serving = asyncio.create_task(server.serve(sockets=[listener]))
    async with websockets.serve(echo, "127.0.0.1", 0, subprotocols=["binary"]) as upstream:

        async def target(_ident):
            return {
                "host": "127.0.0.1",
                "port": upstream.sockets[0].getsockname()[1],
                "token": "private-upstream-token",
            }

        runtime.recording_target = target
        try:
            async with asyncio.timeout(5):
                while not server.started:
                    await asyncio.sleep(0.01)
            endpoint = f"ws://127.0.0.1:{listener.getsockname()[1]}{url}/viewer/websockify"

            def connect():
                return websockets.connect(
                    endpoint,
                    origin="http://localhost:4100",
                    proxy=None,
                    additional_headers={"Cookie": f"e2e_session={member.cookies['e2e_session']}"},
                    subprotocols=["binary"],
                )

            with pytest.raises(InvalidStatus) as error:
                async with connect():
                    pytest.fail("Viewer opened a controlling recorder connection")
            assert error.value.response.status_code == 403
            membership_url = f"/api/v1/organizations/{organization_id}/members/{account['id']}"
            assert (await owner.patch(membership_url, json={"role": "member"})).status_code == 200
            async with connect() as connection:
                await connection.send(b"authorized browser input")
                assert await connection.recv() == b"authorized browser input"
                assert (await owner.delete(membership_url)).status_code == 200
                with pytest.raises(ConnectionClosed):
                    await asyncio.wait_for(connection.recv(), timeout=8)
        finally:
            server.should_exit = True
            await serving
            listener.close()
