"""Real FastAPI -> PostgreSQL -> Docker -> UI/API fixture acceptance.

Requires the API at 4100 and shared technical fixture at 18080/18081.
Creates an isolated account/project and retains run records as evidence.
No credentials are printed or saved into the evidence report.
"""

import asyncio
import json
import secrets
from pathlib import Path
from uuid import uuid4

import httpx
import websockets

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:4100"
CODE = """import {test, expect} from '@playwright/test';
test('create order through UI and verify the same backend entity', async ({page, platform}) => {
  await page.goto('/');
  await expect(page.getByRole('heading', {name: platform.get('fixtureLabel')})).toBeVisible();
  await page.getByRole('textbox', {name: 'Order', exact: true}).fill('api-platform-acceptance');
  await page.getByRole('button', {name: 'Submit order'}).click();
  await expect(page.locator('#order-id')).not.toHaveText('');
  const id = await page.locator('#order-id').innerText();
  platform.set('createdOrderId', id);
  const response = await platform.api('main', '/orders/' + id);
  expect(response.status()).toBe(200);
  const order = await response.json();
  expect(order.reference).toBe('api-platform-acceptance');
  expect(order.label).toBe(platform.get('fixtureLabel'));
});
"""


async def main():
    async with httpx.AsyncClient(base_url=BASE, timeout=30) as client:

        async def call(method, path, expected=200, **kwargs):
            response = await client.request(method, path, **kwargs)
            if response.status_code != expected:
                raise AssertionError(f"{method} {path} returned {response.status_code}: {response.text[:500]}")
            return response.json()

        await call(
            "POST",
            "/api/auth/sign-up/email",
            201,
            json={
                "name": "Isolated technical acceptance",
                "email": f"acceptance-{uuid4().hex}@example.com",
                "password": secrets.token_urlsafe(30),
            },
        )
        project = await call("POST", "/api/v1/projects", 201, json={"name": "Technical runtime API acceptance"})
        prefix = f"/api/v1/projects/{project['id']}"
        scenario = await call("POST", prefix + "/scenarios", 201, json={"name": "UI creates order, API checks same ID"})
        version = await call(
            "POST",
            prefix + f"/scenarios/{scenario['id']}/versions",
            201,
            json={
                "code": CODE,
                "source": "import",
                "changeNote": "Technical fixture acceptance",
                "checks": [{"id": "same-order", "title": "UI/API order identity", "kind": "api"}],
            },
        )
        environment = await call(
            "POST",
            prefix + "/environments",
            201,
            json={
                "name": "Fixture A",
                "websites": {"main": "http://host.docker.internal:18080/"},
                "apiBases": {"main": "http://host.docker.internal:18080/"},
                "variables": {"fixtureLabel": "Fixture A"},
                "setup": [
                    {
                        "name": "Create seed",
                        "apiBase": "main",
                        "method": "POST",
                        "path": "/orders",
                        "body": {"reference": "seed"},
                        "expectedStatus": 201,
                        "capture": {"seedId": "id"},
                    }
                ],
                "cleanup": [
                    {
                        "name": "Remove UI order",
                        "apiBase": "main",
                        "method": "DELETE",
                        "path": "/orders/{{createdOrderId}}",
                        "expectedStatus": 204,
                    },
                    {
                        "name": "Remove seed",
                        "apiBase": "main",
                        "method": "DELETE",
                        "path": "/orders/{{seedId}}",
                        "expectedStatus": 204,
                    },
                ],
            },
        )

        async def wait_run(run):
            async with asyncio.timeout(150):
                while run["status"] in {"queued", "running"}:
                    await asyncio.sleep(0.3)
                    run = await call("GET", prefix + f"/runs/{run['id']}")
            events = await call("GET", prefix + f"/runs/{run['id']}/events?limit=100")
            assert run["status"] == "passed", (run["status"], run.get("error"), events)
            assert run["verification"] == "verified", run
            artifacts = await call("GET", prefix + f"/runs/{run['id']}/artifacts")
            assert any(v["kind"] == "trace" for v in artifacts["items"]), artifacts
            assert any(v["type"] == "assertion" for v in events["items"]), events
            print(f"Run {run['id']}: passed, verified; {artifacts['total']} private artifacts", flush=True)
            return run, events, artifacts

        first, first_events, first_artifacts = await wait_run(
            await call(
                "POST", prefix + "/runs", 202, json={"scenarioId": scenario["id"], "environmentId": environment["id"]}
            )
        )
        await call(
            "PATCH",
            prefix + f"/environments/{environment['id']}",
            json={
                "name": "Fixture B",
                "websites": {"main": "http://host.docker.internal:18081/"},
                "apiBases": {"main": "http://host.docker.internal:18081/"},
                "variables": {"fixtureLabel": "Fixture B"},
            },
        )
        replay, _, _ = await wait_run(await call("POST", prefix + f"/runs/{first['id']}/rerun", 202, json={}))
        assert replay["environmentSnapshot"] == first["environmentSnapshot"]
        assert replay["versions"] == first["versions"] and replay["id"] != first["id"]
        current, _, _ = await wait_run(
            await call(
                "POST", prefix + "/runs", 202, json={"scenarioId": scenario["id"], "environmentId": environment["id"]}
            )
        )
        assert current["environmentSnapshot"]["variables"]["fixtureLabel"] == "Fixture B"
        trace = await client.get(prefix + f"/runs/{first['id']}/trace", follow_redirects=True)
        assert trace.status_code == 200 and "<html" in trace.text.lower()
        artifact_url = first_artifacts["items"][0]["url"]
        assert (await client.get(artifact_url)).status_code == 200
        async with httpx.AsyncClient(base_url=BASE) as stranger:
            assert (await stranger.get(artifact_url)).status_code == 401
        recording = await call(
            "POST",
            prefix + "/recordings",
            202,
            json={"environmentId": environment["id"], "website": "main", "scenarioId": scenario["id"]},
        )
        async with asyncio.timeout(120):
            while recording["status"] == "starting":
                await asyncio.sleep(0.5)
                recording = await call("GET", prefix + f"/recordings/{recording['id']}")
        assert recording["status"] == "ready", recording
        viewer = await client.get(recording["viewerUrl"])
        assert viewer.status_code == 200
        cookie = "; ".join(f"{key}={value}" for key, value in client.cookies.items())
        ws_url = BASE.replace("http:", "ws:") + recording["viewerUrl"].replace("index.html", "websockify")
        async with websockets.connect(ws_url, origin=BASE, additional_headers={"Cookie": cookie}, proxy=None) as socket:
            assert (await asyncio.wait_for(socket.recv(), 10)).startswith(b"RFB ")
        stopped = await call("POST", prefix + f"/recordings/{recording['id']}/stop", json={})
        assert stopped["status"] == "stopped" and "E2E_WEBSITE_main" in stopped["code"]
        report = {
            "projectId": project["id"],
            "runIds": [first["id"], replay["id"], current["id"]],
            "versionId": version["id"],
            "recordingId": recording["id"],
            "checks": [
                "UI creates real order",
                "API validates same order ID",
                "setup captures seed ID",
                "cleanup uses dynamic variables",
                "original snapshot replay",
                "current environment B",
                "private trace/assets",
                "authenticated recording HTTP and RFB websocket",
                "recording exports named environment binding",
            ],
            "status": "passed",
        }
        destination = ROOT / "data/api-runtime-acceptance.json"
        destination.write_text(json.dumps(report, indent=2), encoding="utf8")
        print(f"API runtime acceptance passed; report: {destination}")


if __name__ == "__main__":
    asyncio.run(main())
