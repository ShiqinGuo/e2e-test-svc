"""Real HTTP-session/project-boundary tests against disposable PostgreSQL schemas.

The runtime is bounded and does not execute imported code. Browser and container
acceptance is covered separately by the runtime integration checks.
"""

from __future__ import annotations

import asyncio
import json
import re
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4
from zipfile import ZipFile

import httpx
import pytest
import pytest_asyncio
from jsonschema import Draft202012Validator, FormatChecker
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import Settings
from app.main import create_app
from app.migrations import migrate

API = "/api/v1"
PASSWORD = "test-only-password-93827"
SECRET_VARIABLE = "test-secret-variable-7a821d"
SECRET_COOKIE = "test-secret-cookie-468abb"
SECRET_HEADER = "test-secret-header-ea27cc"
CODE = "import { test, expect } from '@playwright/test';\ntest('fixture', async ({ page }) => { await page.goto('/'); await expect(page).toHaveTitle('Fixture'); });\n"
CONTRACT = json.loads((Path(__file__).resolve().parents[1] / "docs" / "openapi.json").read_text(encoding="utf-8"))
RESPONSE_VALIDATORS: dict[tuple[str, str, int], Draft202012Validator] = {}


def validate_response_contract(method: str, path: str, status: int, body: Any) -> None:
    for pattern, operations in CONTRACT["paths"].items():
        if method.lower() not in operations or not re.fullmatch(
            re.sub(r"\{[^}]+\}", "[^/]+", pattern), urlsplit(path).path
        ):
            continue
        content = (
            operations[method.lower()]["responses"].get(str(status), {}).get("content", {}).get("application/json")
        )
        assert content is not None, f"Undocumented JSON response: {method} {pattern} {status}"
        key = (method, pattern, status)
        validator = RESPONSE_VALIDATORS.setdefault(
            key,
            Draft202012Validator(
                {**content["schema"], "components": CONTRACT["components"]}, format_checker=FormatChecker()
            ),
        )
        errors = sorted(validator.iter_errors(body), key=lambda error: str(error.path))
        assert not errors, f"Contract mismatch: {method} {pattern} {status}: " + "; ".join(
            f"{list(error.path)}: {error.message}" for error in errors[:5]
        )
        return
    raise AssertionError(f"Undocumented API operation {method} {path}")


class StubRuntime:
    """Deterministic execution handoff: tests release a result explicitly."""

    def __init__(self) -> None:
        self.runs: dict[str, dict[str, Any]] = {}
        self.pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self.emitters: dict[str, Any] = {}
        self.recordings: dict[str, dict[str, Any]] = {}

    async def available(self) -> dict[str, Any]:
        return {"runner": {"available": True}, "recorder": {"available": True}}

    async def run(self, input: dict[str, Any], emit: Any) -> dict[str, Any]:
        self.runs[input["id"]] = input
        self.emitters[input["id"]] = emit
        future = asyncio.get_running_loop().create_future()
        self.pending[input["id"]] = future
        await emit({"type": "suite.begin", "data": {"fixture": True}})
        try:
            return await future
        finally:
            self.pending.pop(input["id"], None)

    async def wait_started(self, run_id: str) -> dict[str, Any]:
        async with asyncio.timeout(5):
            while run_id not in self.pending:
                await asyncio.sleep(0.01)
        return self.runs[run_id]

    async def finish(self, run_id: str, **result: Any) -> None:
        await self.wait_started(run_id)
        self.pending[run_id].set_result(
            {
                "status": "passed",
                "verification": "verified",
                "summary": {"total": 1, "passed": 1, "failed": 0, "skipped": 0, "flaky": 0, "unverified": 0},
                "artifacts": [],
                **result,
            }
        )

    async def cancel(self, run_id: str) -> None:
        if run_id in self.pending and not self.pending[run_id].done():
            self.pending[run_id].set_result(
                {
                    "status": "cancelled",
                    "verification": "unverified",
                    "summary": {"total": 0, "passed": 0, "failed": 0, "skipped": 0, "flaky": 0, "unverified": 0},
                    "artifacts": [],
                }
            )

    async def recording_start(self, input: dict[str, Any], emit: Any = None) -> dict[str, Any]:
        self.recordings[input["id"]] = {
            "input": input,
            "code": CODE,
            "checks": [{"id": "title", "title": "Page title", "kind": "ui", "expected": "Fixture"}],
        }
        return {"host": "127.0.0.1", "port": 9, "token": "test-upstream-only-token"}

    async def recording_read(self, recording_id: str) -> dict[str, Any]:
        item = self.recordings[recording_id]
        return {"code": item["code"], "checks": item["checks"]}

    async def recording_stop(self, recording_id: str) -> dict[str, Any]:
        return await self.recording_read(recording_id)

    async def recording_target(self, recording_id: str) -> dict[str, Any] | None:
        return (
            {"host": "127.0.0.1", "port": 9, "token": "test-upstream-only-token"}
            if recording_id in self.recordings
            else None
        )

    async def close(self) -> None:
        for future in list(self.pending.values()):
            if not future.done():
                future.cancel()


@pytest_asyncio.fixture
async def database_settings(tmp_path: Path, request):
    """Each test gets a schema and real migrations; no SQLite approximation."""
    settings = Settings()
    assert settings.database_url.startswith("postgresql"), "API isolation tests require PostgreSQL"
    schema = f"test_{uuid4().hex}"
    admin = create_async_engine(settings.database_url)
    async with admin.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    settings = settings.model_copy(
        update={
            "database_schema": schema,
            "data_dir": tmp_path,
            "base_url": "http://localhost:4100",
            "auth_rate_limit": 1000,
        }
    )
    try:
        await asyncio.to_thread(migrate, settings, getattr(request, "param", "head"))
        yield settings
    finally:
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()


@pytest_asyncio.fixture
async def api(database_settings: Settings):
    runtime = StubRuntime()
    app = create_app(settings=database_settings, runtime=runtime)
    runtime.app = app
    try:
        async with app.router.lifespan_context(app), AsyncExitStack() as stack:
            clients = [
                await stack.enter_async_context(
                    httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost:4100")
                )
                for _ in range(3)
            ]
            for name, client in zip(("Alice", "Bob"), clients[1:]):
                response = await client.post(
                    "/api/auth/sign-up/email",
                    json={"name": name, "email": f"{name.lower()}-{uuid4().hex}@example.com", "password": PASSWORD},
                )
                assert response.status_code == 201, response.text
                assert "httponly" in response.headers.get("set-cookie", "").lower()
                assert "samesite=lax" in response.headers.get("set-cookie", "").lower()
            yield (*clients, runtime)
    finally:
        await runtime.close()


async def expect_response(client: httpx.AsyncClient, method: str, path: str, status: int = 200, **kwargs: Any) -> Any:
    response = await client.request(method, path, **kwargs)
    assert response.status_code == status, (
        f"{method} {path}: expected {status}, got {response.status_code}; body={response.text[:1500]}"
    )
    body = response.json() if response.content else None
    validate_response_contract(method, path, status, body)
    return body


async def expect_hidden(client: httpx.AsyncClient, method: str, path: str, **kwargs: Any) -> None:
    result = await expect_response(client, method, path, 404, **kwargs)
    assert result["error"]["code"] == "NOT_FOUND"
    assert isinstance(result["requestId"], str)


@dataclass
class Resources:
    project: dict[str, Any]
    group: dict[str, Any]
    scenario: dict[str, Any]
    version: dict[str, Any]
    environment: dict[str, Any]

    @property
    def path(self) -> str:
        return f"{API}/projects/{self.project['id']}"


async def seed(client: httpx.AsyncClient, name: str = "fixture") -> Resources:
    organization = await expect_response(client, "POST", f"{API}/organizations", 201, json={"name": name})
    project = await expect_response(
        client,
        "POST",
        f"{API}/projects",
        201,
        json={
            "name": name,
            "description": "Isolated integration fixture",
            "workspaceId": organization["defaultWorkspaceId"],
        },
    )
    root = f"{API}/projects/{project['id']}"
    group = await expect_response(client, "POST", f"{root}/groups", 201, json={"name": "Checkout"})
    scenario = await expect_response(
        client, "POST", f"{root}/scenarios", 201, json={"name": "Place order", "groupId": group["id"]}
    )
    version = await expect_response(
        client,
        "POST",
        f"{root}/scenarios/{scenario['id']}/versions",
        201,
        json={
            "code": CODE,
            "source": "human",
            "changeNote": "Initial version",
            "checks": [{"id": "title", "title": "Page title is Fixture", "kind": "ui", "expected": "Fixture"}],
            "modules": {"checkout.ts": "export const marker = 'version-one';"},
        },
    )
    environment = await expect_response(
        client,
        "POST",
        f"{root}/environments",
        201,
        json={
            "name": "Isolated fixture",
            "websites": {"main": "https://fixture.example.test", "admin": "https://admin.example.test"},
            "apiBases": {"main": "https://api.example.test"},
            "variables": {"public": "visible"},
            "secretVariables": {"apiToken": SECRET_VARIABLE},
            "roles": [
                {
                    "name": "buyer",
                    "storageState": {
                        "cookies": [
                            {
                                "name": "session",
                                "value": SECRET_COOKIE,
                                "domain": "fixture.example.test",
                                "path": "/",
                                "expires": -1,
                                "httpOnly": True,
                                "secure": True,
                                "sameSite": "Lax",
                            }
                        ],
                        "origins": [],
                    },
                    "headers": {"Authorization": f"Bearer {SECRET_HEADER}"},
                }
            ],
            "setup": [
                {
                    "name": "Create test order",
                    "apiBase": "main",
                    "method": "POST",
                    "path": "/orders",
                    "body": {"fixture": True},
                    "expectedStatus": 201,
                    "capture": {"orderId": "id"},
                }
            ],
            "cleanup": [
                {
                    "name": "Delete test order",
                    "apiBase": "main",
                    "method": "DELETE",
                    "path": "/orders/{{orderId}}",
                    "expectedStatus": 204,
                }
            ],
        },
    )
    return Resources(project, group, scenario, version, environment)


async def queue(client: httpx.AsyncClient, resources: Resources, **extra: Any) -> dict[str, Any]:
    return await expect_response(
        client,
        "POST",
        f"{resources.path}/runs",
        202,
        json={
            "scenarioId": resources.scenario["id"],
            "environmentId": resources.environment["id"],
            "role": "buyer",
            **extra,
        },
    )


def assert_redacted(value: Any) -> None:
    rendered = str(value)
    for secret in (SECRET_VARIABLE, SECRET_COOKIE, SECRET_HEADER):
        assert secret not in rendered, "API response exposed a secret from the execution environment"


@pytest.mark.asyncio
async def test_authentication_cookie_lifecycle(api: Any) -> None:
    anonymous, alice, bob, _runtime = api
    assert await expect_response(anonymous, "GET", "/api/health") == {"status": "ok"}
    published = await expect_response(anonymous, "GET", "/api/openapi.json")
    run_response = published["paths"]["/api/v1/projects/{projectId}/runs/{runId}"]["get"]["responses"]["200"]
    model = run_response["content"]["application/json"]["schema"]["$ref"].rsplit("/", 1)[-1]
    assert published["components"]["schemas"][model]["properties"]["environmentSnapshot"]
    assert published["components"]["securitySchemes"]["sessionCookie"]["name"] == "e2e_session"
    await expect_response(alice, "GET", f"{API}/capabilities")
    result = await expect_response(anonymous, "GET", f"{API}/me", 401)
    assert result["error"]["code"] == "UNAUTHENTICATED"
    assert result["requestId"]
    assert await expect_response(anonymous, "GET", "/api/auth/get-session") is None
    first = await expect_response(alice, "GET", f"{API}/me")
    second = await expect_response(bob, "GET", f"{API}/me")
    assert first["user"]["id"] != second["user"]["id"]
    session = await expect_response(alice, "GET", "/api/auth/get-session")
    assert session["user"]["id"] == first["user"]["id"]
    assert "token" not in session and "token" not in session["session"]
    await expect_response(
        anonymous, "GET", f"{API}/me", 401, headers={"cookie": f"e2e_session={session['session']['id']}"}
    )
    old_cookie = "; ".join(f"{cookie.name}={cookie.value}" for cookie in alice.cookies.jar)
    assert old_cookie
    await expect_response(alice, "POST", "/api/auth/sign-out", json={})
    await expect_response(alice, "GET", f"{API}/me", 401)
    await expect_response(anonymous, "GET", f"{API}/me", 401, headers={"cookie": old_cookie})
    await expect_response(
        alice, "POST", "/api/auth/sign-in/email", json={"email": first["user"]["email"], "password": PASSWORD}
    )
    assert (await expect_response(alice, "GET", f"{API}/me"))["user"]["id"] == first["user"]["id"]
    bad_login = await expect_response(
        anonymous,
        "POST",
        "/api/auth/sign-in/email",
        401,
        json={"email": first["user"]["email"], "password": "wrong-password"},
    )
    assert bad_login["code"] == "INVALID_CREDENTIALS"


@pytest.mark.asyncio
async def test_every_project_resource_is_authorized_by_server(api: Any) -> None:
    anonymous, alice, bob, _runtime = api
    own = await seed(alice, "Alice")
    foreign = await seed(bob, "Bob")
    root = own.path
    run = await queue(alice, own)
    recording = await expect_response(
        alice,
        "POST",
        f"{root}/recordings",
        202,
        json={
            "environmentId": own.environment["id"],
            "website": "main",
            "scenarioId": own.scenario["id"],
            "role": "buyer",
        },
    )
    for client in (bob,):
        reads = [
            root,
            f"{root}/groups",
            f"{root}/groups/{own.group['id']}",
            f"{root}/scenarios",
            f"{root}/scenarios/{own.scenario['id']}",
            f"{root}/scenarios/{own.scenario['id']}/versions",
            f"{root}/scenarios/{own.scenario['id']}/versions/{own.version['id']}",
            f"{root}/environments",
            f"{root}/environments/{own.environment['id']}",
            f"{root}/runs",
            f"{root}/runs/{run['id']}",
            f"{root}/runs/{run['id']}/events",
            f"{root}/runs/{run['id']}/artifacts",
            f"{root}/runs/{run['id']}/trace",
            f"{root}/recordings",
            f"{root}/recordings/{recording['id']}",
        ]
        for path in reads:
            await expect_hidden(client, "GET", path)
        writes = [
            ("PATCH", root, {"name": "stolen"}),
            ("POST", f"{root}/groups", {"name": "stolen"}),
            ("PATCH", f"{root}/groups/{own.group['id']}", {"name": "stolen"}),
            ("POST", f"{root}/scenarios", {"name": "stolen"}),
            ("PATCH", f"{root}/scenarios/{own.scenario['id']}", {"name": "stolen"}),
            (
                "POST",
                f"{root}/scenarios/{own.scenario['id']}/versions",
                {"code": CODE, "source": "ai", "changeNote": "stolen", "checks": []},
            ),
            ("POST", f"{root}/environments", {"name": "stolen", "websites": {"main": "https://fixture.example.test"}}),
            ("PATCH", f"{root}/environments/{own.environment['id']}", {"name": "stolen"}),
            ("POST", f"{root}/runs", {"scenarioId": own.scenario["id"], "environmentId": own.environment["id"]}),
            ("POST", f"{root}/runs/{run['id']}/cancel", {}),
            ("POST", f"{root}/runs/{run['id']}/rerun", {}),
            ("POST", f"{root}/recordings", {"environmentId": own.environment["id"], "website": "main"}),
            ("POST", f"{root}/recordings/{recording['id']}/stop", {}),
            (
                "POST",
                f"{root}/recordings/{recording['id']}/save",
                {"scenarioId": own.scenario["id"], "changeNote": "stolen"},
            ),
        ]
        for method, path, payload in writes:
            await expect_hidden(client, method, path, json=payload)
    assert (await expect_response(alice, "GET", f"{API}/projects?workspaceId={own.project['workspaceId']}"))[
        "total"
    ] == 1
    assert (await expect_response(bob, "GET", f"{API}/projects?workspaceId={foreign.project['workspaceId']}"))["items"][
        0
    ]["id"] == foreign.project["id"]
    for path in (root, f"{root}/runs/{run['id']}", f"{root}/recordings/{recording['id']}"):
        await expect_response(anonymous, "GET", path, 401)


@pytest.mark.asyncio
async def test_parent_resource_ids_cannot_be_mixed_even_for_same_owner(api: Any) -> None:
    _anonymous, alice, _bob, _runtime = api
    first, second = await seed(alice, "First"), await seed(alice, "Second")
    for segment, key in (("groups", "group"), ("scenarios", "scenario"), ("environments", "environment")):
        await expect_hidden(alice, "GET", f"{first.path}/{segment}/{getattr(second, key)['id']}")
        await expect_hidden(
            alice, "PATCH", f"{first.path}/{segment}/{getattr(second, key)['id']}", json={"name": "invalid"}
        )
    await expect_hidden(
        alice, "POST", f"{first.path}/scenarios", json={"name": "cross-project", "groupId": second.group["id"]}
    )
    await expect_hidden(alice, "GET", f"{first.path}/scenarios/{first.scenario['id']}/versions/{second.version['id']}")
    for extra in (
        {"environmentId": second.environment["id"]},
        {"scenarioId": second.scenario["id"]},
        {"versionId": second.version["id"]},
    ):
        await expect_hidden(
            alice,
            "POST",
            f"{first.path}/runs",
            json={"scenarioId": first.scenario["id"], "environmentId": first.environment["id"], **extra},
        )
    await expect_hidden(
        alice, "POST", f"{first.path}/recordings", json={"environmentId": second.environment["id"], "website": "main"}
    )
    await expect_hidden(
        alice,
        "POST",
        f"{first.path}/recordings",
        json={"environmentId": first.environment["id"], "website": "main", "scenarioId": second.scenario["id"]},
    )


@pytest.mark.asyncio
async def test_versions_and_run_environment_are_immutable(api: Any) -> None:
    _anonymous, alice, _bob, _runtime = api
    resources = await seed(alice)
    first_run = await queue(alice, resources)
    versions = f"{resources.path}/scenarios/{resources.scenario['id']}/versions"
    replacement = await expect_response(
        alice,
        "POST",
        versions,
        201,
        json={
            "code": CODE.replace("Fixture", "Changed"),
            "source": "ai",
            "changeNote": "A separate revision",
            "checks": [],
            "modules": {"checkout.ts": "export const marker = 'version-two';"},
        },
    )
    assert replacement["id"] != resources.version["id"]
    assert replacement["number"] == resources.version["number"] + 1
    for method in ("PATCH", "PUT", "DELETE"):
        response = await alice.request(method, f"{versions}/{resources.version['id']}", json={"code": "overwritten"})
        assert response.status_code in (404, 405)
    original = await expect_response(alice, "GET", f"{versions}/{resources.version['id']}")
    assert original == resources.version
    await expect_response(
        alice,
        "PATCH",
        f"{resources.path}/environments/{resources.environment['id']}",
        json={
            "name": "Changed environment",
            "websites": {"main": "https://changed.example.test"},
            "variables": {"public": "changed"},
        },
    )
    snapshot = await expect_response(alice, "GET", f"{resources.path}/runs/{first_run['id']}")
    assert snapshot["versions"][0]["id"] == original["id"]
    assert snapshot["versions"][0]["code"] == original["code"]
    assert snapshot["versions"][0]["modules"] == original["modules"]
    assert snapshot["environmentSnapshot"]["websites"]["main"] == "https://fixture.example.test"
    assert snapshot["environmentSnapshot"]["variables"]["public"] == "visible"
    assert_redacted(snapshot)
    second_run = await queue(alice, resources)
    assert second_run["id"] != first_run["id"]
    assert second_run["versions"][0]["id"] == replacement["id"]
    assert second_run["environmentSnapshot"]["websites"]["main"] == "https://changed.example.test"
    replay = await expect_response(alice, "POST", f"{resources.path}/runs/{first_run['id']}/rerun", 202, json={})
    assert replay["id"] not in {first_run["id"], second_run["id"]}
    assert replay["rerunOf"] == first_run["id"]
    assert replay["versions"] == snapshot["versions"]
    assert replay["environmentSnapshot"] == snapshot["environmentSnapshot"]
    assert replay["role"] == snapshot["role"]
    assert replay["status"] == "queued"
    assert replay["verification"] == "pending"
    assert_redacted(replay)


@pytest.mark.asyncio
async def test_environment_secrets_never_return_in_resource_responses(api: Any) -> None:
    _anonymous, alice, _bob, _runtime = api
    resources = await seed(alice)
    assert_redacted(resources.environment)
    env = resources.environment
    assert env["secretVariableKeys"] == ["apiToken"]
    assert env["roles"] == [{"name": "buyer", "hasStorageState": True, "hasHeaders": True}]
    for path in (f"{resources.path}/environments", f"{resources.path}/environments/{env['id']}"):
        assert_redacted(await expect_response(alice, "GET", path))
    patched = await expect_response(
        alice,
        "PATCH",
        f"{resources.path}/environments/{env['id']}",
        json={"name": "Renamed", "roles": [{"name": "buyer"}]},
    )
    assert patched["roles"] == env["roles"]
    assert patched["secretVariableKeys"] == ["apiToken"]
    assert_redacted(patched)
    run = await queue(alice, resources)
    assert_redacted(run)
    assert_redacted(await expect_response(alice, "GET", f"{resources.path}/runs"))
    private_input = await _runtime.wait_started(run["id"])
    assert private_input["environment"]["secretVariables"]["apiToken"] == SECRET_VARIABLE
    assert private_input["environment"]["roles"][0]["storageState"]["cookies"][0]["value"] == SECRET_COOKIE
    assert private_input["environment"]["roles"][0]["headers"]["Authorization"] == f"Bearer {SECRET_HEADER}"


@pytest.mark.asyncio
async def test_pagination_origin_and_run_validation(api: Any) -> None:
    _anonymous, alice, _bob, _runtime = api
    resources = await seed(alice)
    for query in ("limit=0", "limit=101", "limit=-1", "limit=bad", "offset=-1", "offset=bad"):
        result = await expect_response(
            alice, "GET", f"{API}/projects?workspaceId={resources.project['workspaceId']}&{query}", 400
        )
        assert result["error"]["code"] == "VALIDATION_ERROR"
    page = await expect_response(
        alice, "GET", f"{API}/projects?workspaceId={resources.project['workspaceId']}&limit=1&offset=1"
    )
    assert page == {"items": [], "total": 1, "limit": 1, "offset": 1}
    forbidden = await expect_response(
        alice,
        "POST",
        f"{API}/projects",
        403,
        headers={"Origin": "https://untrusted.example"},
        json={"name": "must-not-exist"},
    )
    assert forbidden["error"]["code"]
    await expect_response(
        alice,
        "POST",
        f"{API}/projects",
        201,
        headers={"Origin": "http://localhost:5173"},
        json={"name": "Trusted UI", "workspaceId": resources.project["workspaceId"]},
    )
    for body in (
        {"environmentId": resources.environment["id"]},
        {
            "environmentId": resources.environment["id"],
            "scenarioId": resources.scenario["id"],
            "groupId": resources.group["id"],
        },
        {
            "environmentId": resources.environment["id"],
            "groupId": resources.group["id"],
            "versionId": resources.version["id"],
        },
        {"environmentId": resources.environment["id"], "scenarioId": resources.scenario["id"], "retries": 3},
        {"environmentId": resources.environment["id"], "scenarioId": resources.scenario["id"], "timeoutMs": 999},
        {"environmentId": resources.environment["id"], "scenarioId": resources.scenario["id"], "role": "nonexistent"},
    ):
        result = await expect_response(alice, "POST", f"{resources.path}/runs", 400, json=body)
        assert result["error"]["code"] == ("INVALID_ROLE" if body.get("role") == "nonexistent" else "VALIDATION_ERROR")
    for modules in ({"../escape.ts": "bad"}, {"nested/file.ts": "bad"}, {"script.js": "bad"}):
        await expect_response(
            alice,
            "POST",
            f"{resources.path}/scenarios/{resources.scenario['id']}/versions",
            400,
            json={"code": CODE, "source": "import", "changeNote": "bad module path", "checks": [], "modules": modules},
        )


async def wait_run(client: httpx.AsyncClient, root: str, run_id: str, status: str) -> dict[str, Any]:
    async with asyncio.timeout(5):
        while True:
            result = await expect_response(client, "GET", f"{root}/runs/{run_id}")
            if result["status"] == status:
                return result
            await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_durable_events_retry_history_and_private_artifacts(api: Any) -> None:
    anonymous, alice, bob, runtime = api
    resources = await seed(alice)
    run = await queue(alice, resources, retries=1)
    input = await runtime.wait_started(run["id"])
    emit = runtime.emitters[run["id"]]
    await emit(
        {
            "type": "test.end",
            "data": {"title": "fixture", "retry": 0, "status": "failed", "expected": "Fixture", "actual": "Loading"},
        }
    )
    await emit({"type": "test.end", "data": {"title": "fixture", "retry": 1, "status": "passed"}})
    await emit(
        {
            "type": "stdout",
            "data": {"message": f"Authorization: {SECRET_VARIABLE}; cookie={SECRET_COOKIE}; token={SECRET_HEADER}"},
        }
    )
    artifact_dir = Path(input["workDir"]) / "execution" / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    attachment = artifact_dir / "assertion.txt"
    attachment.write_text("Expected Fixture; actual Loading; attempt 0 failed.", encoding="utf-8")
    trace = artifact_dir / "trace.zip"
    with ZipFile(trace, "w") as archive:
        archive.writestr("test.trace", "{}\n")
    outside = Path(input["workDir"]).parent / "outside-run-directory.txt"
    outside.write_text("This file must never be registered as a run artifact.", encoding="utf-8")
    await runtime.finish(
        run["id"],
        summary={"total": 1, "passed": 1, "failed": 0, "skipped": 0, "flaky": 1, "unverified": 0},
        artifacts=[
            {"name": attachment.name, "path": str(attachment), "contentType": "text/plain", "kind": "attachment"},
            {"name": trace.name, "path": str(trace), "contentType": "application/zip", "kind": "trace"},
            {"name": outside.name, "path": str(outside), "contentType": "text/plain", "kind": "attachment"},
        ],
    )
    result = await wait_run(alice, resources.path, run["id"], "passed")
    assert result["summary"]["flaky"] == 1
    events = await expect_response(alice, "GET", f"{resources.path}/runs/{run['id']}/events?after=0&limit=100")
    assert_redacted(events)
    seqs = [event["seq"] for event in events["items"]]
    assert seqs == sorted(set(seqs))
    assert events["nextAfter"] == seqs[-1]
    attempts = [event["data"] for event in events["items"] if event["type"] == "test.end"]
    assert [(attempt["retry"], attempt["status"]) for attempt in attempts] == [(0, "failed"), (1, "passed")]
    tail = await expect_response(alice, "GET", f"{resources.path}/runs/{run['id']}/events?after={events['nextAfter']}")
    assert tail["items"] == []
    for query in ("after=-1", "after=bad", "limit=0", "limit=101"):
        await expect_response(alice, "GET", f"{resources.path}/runs/{run['id']}/events?{query}", 400)
    artifacts = await expect_response(alice, "GET", f"{resources.path}/runs/{run['id']}/artifacts")
    assert artifacts["total"] == 2
    assert str(artifact_dir) not in str(artifacts)
    for artifact in artifacts["items"]:
        response = await alice.get(artifact["url"])
        assert response.status_code == 200
        assert response.content
        assert "attachment" in response.headers.get("content-disposition", "").lower()
        await expect_response(anonymous, "GET", artifact["url"], 401)
        await expect_hidden(bob, "GET", artifact["url"])
    trace_view = await alice.get(f"{resources.path}/runs/{run['id']}/trace", follow_redirects=True)
    assert trace_view.status_code == 200
    assert "text/html" in trace_view.headers["content-type"]
    assert str(artifact_dir) not in trace_view.text
    await expect_response(anonymous, "GET", f"{resources.path}/runs/{run['id']}/trace", 401)
    await expect_hidden(bob, "GET", f"{resources.path}/runs/{run['id']}/trace")
    another_run = await queue(alice, resources)
    for artifact in artifacts["items"]:
        await expect_hidden(alice, "GET", f"{resources.path}/runs/{another_run['id']}/artifacts/{artifact['id']}")


@pytest.mark.asyncio
async def test_cancel_and_unverified_outcome_remain_visible(api: Any) -> None:
    _anonymous, alice, _bob, runtime = api
    resources = await seed(alice)
    cancelled = await queue(alice, resources)
    await runtime.wait_started(cancelled["id"])
    await expect_response(alice, "POST", f"{resources.path}/runs/{cancelled['id']}/cancel", json={})
    result = await wait_run(alice, resources.path, cancelled["id"], "cancelled")
    assert result["finishedAt"]
    repeat = await expect_response(alice, "POST", f"{resources.path}/runs/{cancelled['id']}/cancel", json={})
    assert repeat["status"] == "cancelled"
    unverified = await queue(alice, resources)
    await runtime.finish(
        unverified["id"],
        verification="unverified",
        summary={"total": 1, "passed": 1, "failed": 0, "skipped": 0, "flaky": 0, "unverified": 1},
    )
    result = await wait_run(alice, resources.path, unverified["id"], "passed")
    assert result["verification"] == "unverified"
    assert result["summary"]["unverified"] == 1


@pytest.mark.asyncio
async def test_recording_code_saves_as_new_version_with_authorized_parent(api: Any) -> None:
    anonymous, alice, bob, _runtime = api
    first, second = await seed(alice, "First"), await seed(alice, "Second")
    recording = await expect_response(
        alice,
        "POST",
        f"{first.path}/recordings",
        202,
        json={"environmentId": first.environment["id"], "website": "main", "scenarioId": first.scenario["id"]},
    )
    recording_path = f"{first.path}/recordings/{recording['id']}"
    async with asyncio.timeout(5):
        while True:
            current = await expect_response(alice, "GET", recording_path)
            if current["status"] != "starting":
                break
            await asyncio.sleep(0.01)
    assert current["status"] == "ready"
    assert current["viewerUrl"].startswith("/api/")
    assert "test-upstream-only-token" not in str(current)
    for client, status in ((anonymous, 401), (bob, 404)):
        response = await client.get(current["viewerUrl"])
        assert response.status_code == status
        assert response.json()["error"]["code"] == ("UNAUTHENTICATED" if status == 401 else "NOT_FOUND")
    socket_path = current["viewerUrl"].removesuffix("index.html") + "websockify"
    alice_cookie = "; ".join(f"{item.name}={item.value}" for item in alice.cookies.jar)
    bob_cookie = "; ".join(f"{item.name}={item.value}" for item in bob.cookies.jar)
    for cookie, origin in (
        ("", "http://localhost:5173"),
        (bob_cookie, "http://localhost:5173"),
        (alice_cookie, "https://untrusted.example"),
    ):
        messages = []

        async def receive():
            return {"type": "websocket.connect"}

        async def send(message):
            messages.append(message)

        scope = {
            "type": "websocket",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "scheme": "ws",
            "path": socket_path,
            "raw_path": socket_path.encode(),
            "query_string": b"",
            "root_path": "",
            "headers": [(b"origin", origin.encode()), (b"cookie", cookie.encode())],
            "client": ("127.0.0.1", 50000),
            "server": ("localhost", 4100),
            "subprotocols": ["binary"],
            "state": {},
        }
        async with asyncio.timeout(5):
            await _runtime.app(scope, receive, send)
        assert messages == [{"type": "websocket.close", "code": 1008, "reason": ""}]
    await expect_hidden(alice, "GET", f"{second.path}/recordings/{recording['id']}")
    await expect_hidden(
        alice,
        "POST",
        f"{recording_path}/save",
        json={"scenarioId": second.scenario["id"], "changeNote": "wrong parent"},
    )
    stopped = await expect_response(alice, "POST", f"{recording_path}/stop", json={})
    assert stopped["status"] == "stopped"
    assert stopped["code"] == CODE
    saved = await expect_response(
        alice,
        "POST",
        f"{recording_path}/save",
        201,
        json={"scenarioId": first.scenario["id"], "changeNote": "Recorded UI flow"},
    )
    assert saved["source"] == "recording"
    assert saved["scenarioId"] == first.scenario["id"]
    assert saved["id"] != first.version["id"]
    assert saved["code"] == CODE
    assert saved["checks"]


@pytest.mark.asyncio
async def test_restart_preserves_session_versions_and_reports_interrupted_run(database_settings: Settings) -> None:
    runtime = StubRuntime()
    first_app = create_app(settings=database_settings, runtime=runtime)
    async with (
        first_app.router.lifespan_context(first_app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=first_app), base_url="http://localhost:4100") as client,
    ):
        await expect_response(
            client,
            "POST",
            "/api/auth/sign-up/email",
            201,
            json={"name": "Restart fixture", "email": f"restart-{uuid4().hex}@example.com", "password": PASSWORD},
        )
        resources = await seed(client)
        run = await queue(client, resources)
        await runtime.wait_started(run["id"])
        cookie = "; ".join(f"{item.name}={item.value}" for item in client.cookies.jar)
    second_app = create_app(settings=database_settings, runtime=StubRuntime())
    async with (
        second_app.router.lifespan_context(second_app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=second_app), base_url="http://localhost:4100", headers={"cookie": cookie}
        ) as client,
    ):
        await expect_response(client, "GET", f"{API}/me")
        version = await expect_response(
            client,
            "GET",
            f"{resources.path}/scenarios/{resources.scenario['id']}/versions/{resources.version['id']}",
        )
        assert version == resources.version
        retained = await expect_response(client, "GET", f"{resources.path}/runs/{run['id']}")
        assert retained["status"] == "error"
        assert retained["finishedAt"]
        assert retained["error"]
        assert retained["versions"][0]["id"] == resources.version["id"]
        events = await expect_response(client, "GET", f"{resources.path}/runs/{run['id']}/events")
        assert any(event["data"].get("code") == "PROCESS_INTERRUPTED" for event in events["items"])


@pytest.mark.asyncio
async def test_environment_target_binding_and_role_without_credentials(api: Any) -> None:
    _anonymous, alice, _bob, runtime = api
    resources = await seed(alice)
    valid = {"name": "Target binding fixture", "websites": {"main": "https://fixture.example.test"}}
    cases = [
        {"websites": {}},
        {"websites": {"main": "file:///etc/passwd"}},
        {"websites": {"main": "https://user:password@example.com"}},
        {"variables": {"token": "public"}, "secretVariables": {"token": "secret"}},
        {"roles": [{"name": "guest"}, {"name": "guest"}]},
        {"setup": [{"name": "Wrong environment", "apiBase": "missing", "method": "GET", "path": "/status"}]},
        {
            "apiBases": {"main": "https://api.example.test"},
            "setup": [
                {"name": "Other host", "apiBase": "main", "method": "GET", "path": "https://other.example.test/status"}
            ],
        },
        {
            "apiBases": {"main": "https://api.example.test"},
            "cleanup": [
                {"name": "Other host", "apiBase": "main", "method": "DELETE", "path": "//other.example.test/orders"}
            ],
        },
        {
            "apiBases": {"main": "https://api.example.test"},
            "setup": [
                {
                    "name": "Literal credential",
                    "apiBase": "main",
                    "method": "GET",
                    "path": "/status",
                    "headers": {"Authorization": "Bearer should-use-secret-variable"},
                }
            ],
        },
    ]
    for invalid in cases:
        response = await expect_response(
            alice, "POST", f"{resources.path}/environments", 400, json={**valid, **invalid}
        )
        assert response["error"]["code"] == "VALIDATION_ERROR"
    environment = await expect_response(
        alice, "POST", f"{resources.path}/environments", 201, json={**valid, "roles": [{"name": "guest"}]}
    )
    assert environment["roles"] == [{"name": "guest", "hasStorageState": False, "hasHeaders": False}]
    run = await queue(alice, resources, environmentId=environment["id"], role="guest")
    input = await runtime.wait_started(run["id"])
    assert input["role"] == "guest"
    assert input["environment"]["websites"]["main"] == valid["websites"]["main"]
    await runtime.finish(run["id"])
    await wait_run(alice, resources.path, run["id"], "passed")


@pytest.mark.asyncio
async def test_group_run_freezes_membership_and_each_version(api: Any) -> None:
    _anonymous, alice, _bob, _runtime = api
    resources = await seed(alice)
    second = await expect_response(
        alice,
        "POST",
        f"{resources.path}/scenarios",
        201,
        json={"name": "Second group scenario", "groupId": resources.group["id"]},
    )
    version = await expect_response(
        alice,
        "POST",
        f"{resources.path}/scenarios/{second['id']}/versions",
        201,
        json={"code": CODE, "source": "import", "changeNote": "Second group member", "checks": []},
    )
    run = await expect_response(
        alice,
        "POST",
        f"{resources.path}/runs",
        202,
        json={"environmentId": resources.environment["id"], "groupId": resources.group["id"]},
    )
    assert run["scenarioId"] is None
    assert run["groupId"] == resources.group["id"]
    assert {item["id"] for item in run["versions"]} == {resources.version["id"], version["id"]}
    await expect_response(alice, "PATCH", f"{resources.path}/scenarios/{second['id']}", json={"groupId": None})
    await expect_response(
        alice,
        "POST",
        f"{resources.path}/scenarios/{second['id']}/versions",
        201,
        json={
            "code": CODE + "// New detached version\n",
            "source": "human",
            "changeNote": "Detached scenario",
            "checks": [],
        },
    )
    snapshot = await expect_response(alice, "GET", f"{resources.path}/runs/{run['id']}")
    assert {item["id"] for item in snapshot["versions"]} == {resources.version["id"], version["id"]}
