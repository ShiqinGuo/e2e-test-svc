"""Real PostgreSQL membership, permission and invitation regressions."""

import asyncio
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import pytest
from sqlalchemy import func, select
from test_api import expect_response, queue, seed, wait_run

from app.models import Invitation, Membership, utcnow

pytest_plugins = ("test_api",)


async def invite(owner, organization_id, target, role="member"):
    account = (await target.get("/api/v1/me")).json()["user"]
    result = await expect_response(
        owner,
        "POST",
        f"/api/v1/organizations/{organization_id}/invitations",
        201,
        json={"email": account["email"], "role": role},
    )
    token = parse_qs(urlsplit(result["inviteUrl"]).fragment)["token"][0]
    return result["invitation"], token, account


async def join(owner, organization_id, target, role="member"):
    invitation, token, account = await invite(owner, organization_id, target, role)
    await expect_response(target, "POST", "/api/v1/invitations/accept", json={"token": token})
    return invitation, token, account


@pytest.mark.asyncio
async def test_onboarding_and_workspace_scoped_projects(api):
    _, owner, other, _ = api
    assert (await expect_response(owner, "GET", "/api/v1/organizations"))["total"] == 0
    resource = await seed(owner)
    organization_id = resource.project["organizationId"]
    workspace = await expect_response(
        owner, "POST", f"/api/v1/organizations/{organization_id}/workspaces", 201, json={"name": "Second workspace"}
    )
    assert workspace["role"] == "owner"
    assert (await expect_response(owner, "GET", f"/api/v1/projects?workspaceId={workspace['id']}"))["total"] == 0
    await expect_response(other, "GET", f"/api/v1/projects?workspaceId={workspace['id']}", 404)
    await expect_response(
        other, "POST", "/api/v1/projects", 404, json={"name": "intrusion", "workspaceId": workspace["id"]}
    )
    await expect_response(owner, "POST", "/api/v1/projects", 400, json={"name": "missing workspace"})
    await expect_response(owner, "PATCH", resource.path, 400, json={"workspaceId": workspace["id"]})
    assert "ownerId" not in resource.project


@pytest.mark.asyncio
async def test_invitation_email_binding_and_concurrent_acceptance(api):
    anonymous, owner, invited, runtime = api
    resource = await seed(owner)
    organization_id = resource.project["organizationId"]
    invitation, token, account = await invite(owner, organization_id, invited)
    preview = await expect_response(anonymous, "POST", "/api/v1/invitations/preview", json={"token": token})
    assert preview["email"] == account["email"]
    assert preview["status"] == "pending"
    await expect_response(owner, "POST", "/api/v1/invitations/accept", 403, json={"token": token})
    results = await asyncio.gather(
        *(invited.post("/api/v1/invitations/accept", json={"token": token}) for _ in range(2))
    )
    assert [r.status_code for r in results] == [200, 200]
    assert all(r.json()["role"] == "member" for r in results)
    async with runtime.app.state.context.db.sessions() as session:
        stored = await session.get(Invitation, UUID(invitation["id"]))
        assert stored.token_hash != token and len(stored.token_hash) == 64
        count = await session.scalar(
            select(func.count())
            .select_from(Membership)
            .where(Membership.organization_id == UUID(organization_id), Membership.user_id == UUID(account["id"]))
        )
        assert count == 1
    await expect_response(invited, "PATCH", resource.path, json={"name": "Member edit"})
    await expect_response(
        invited, "POST", f"/api/v1/organizations/{organization_id}/workspaces", 403, json={"name": "Forbidden"}
    )
    await expect_response(owner, "DELETE", f"/api/v1/organizations/{organization_id}/members/{account['id']}")
    await expect_response(invited, "GET", resource.path, 404)
    await expect_response(invited, "POST", "/api/v1/invitations/accept", 409, json={"token": token})


@pytest.mark.asyncio
async def test_revoked_expired_and_superseded_invitations(api):
    _, owner, invited, runtime = api
    resource = await seed(owner)
    organization_id = resource.project["organizationId"]
    invitation, first, _ = await invite(owner, organization_id, invited)
    _, second, _ = await invite(owner, organization_id, invited)
    await expect_response(invited, "POST", "/api/v1/invitations/accept", 409, json={"token": first})
    async with runtime.app.state.context.db.sessions() as session:
        pending = await session.scalar(
            select(Invitation).where(
                Invitation.organization_id == UUID(organization_id), Invitation.status == "pending"
            )
        )
        pending.expires_at = utcnow() - timedelta(seconds=1)
        await session.commit()
    preview = await expect_response(invited, "POST", "/api/v1/invitations/preview", json={"token": second})
    assert preview["status"] == "expired"
    await expect_response(invited, "POST", "/api/v1/invitations/accept", 409, json={"token": second})
    invitation, third, _ = await invite(owner, organization_id, invited)
    await expect_response(owner, "DELETE", f"/api/v1/organizations/{organization_id}/invitations/{invitation['id']}")
    await expect_response(invited, "POST", "/api/v1/invitations/accept", 409, json={"token": third})


@pytest.mark.asyncio
async def test_viewer_reads_evidence_but_cannot_write_or_control_recorder(api):
    _, owner, viewer, runtime = api
    resource = await seed(owner)
    await join(owner, resource.project["organizationId"], viewer, "viewer")
    run = await queue(owner, resource)
    execution = await runtime.wait_started(run["id"])
    trace = Path(execution["workDir"]) / "trace.zip"
    trace.write_bytes(b"private trace bytes")
    await runtime.finish(
        run["id"],
        artifacts=[{"path": str(trace), "name": "trace.zip", "contentType": "application/zip", "kind": "trace"}],
    )
    await wait_run(owner, resource.path, run["id"], "passed")
    run_url = f"{resource.path}/runs/{run['id']}"
    await expect_response(viewer, "GET", resource.path)
    await expect_response(viewer, "GET", f"{resource.path}/environments/{resource.environment['id']}")
    artifacts = await expect_response(viewer, "GET", run_url + "/artifacts")
    assert (await viewer.get(artifacts["items"][0]["url"])).content == b"private trace bytes"
    assert (await viewer.get(run_url + "/trace")).status_code == 307
    recording = await expect_response(
        owner,
        "POST",
        resource.path + "/recordings",
        202,
        json={"environmentId": resource.environment["id"], "website": "main"},
    )
    recording_url = resource.path + "/recordings/" + recording["id"]
    paths = [
        ("PATCH", resource.path, {"name": "forbidden"}),
        ("POST", resource.path + "/groups", {"name": "forbidden"}),
        ("POST", resource.path + "/scenarios", {"name": "forbidden"}),
        ("PATCH", resource.path + "/environments/" + resource.environment["id"], {"name": "forbidden"}),
        (
            "POST",
            resource.path + "/runs",
            {"scenarioId": resource.scenario["id"], "environmentId": resource.environment["id"]},
        ),
        ("POST", run_url + "/rerun", {}),
        ("POST", run_url + "/cancel", {}),
        ("POST", resource.path + "/recordings", {"environmentId": resource.environment["id"], "website": "main"}),
        ("POST", recording_url + "/stop", {}),
        ("POST", recording_url + "/save", {"scenarioId": resource.scenario["id"]}),
    ]
    for method, path, data in paths:
        await expect_response(viewer, method, path, 403, json=data)
    assert (await viewer.get(recording_url + "/viewer/index.html")).status_code == 403


@pytest.mark.asyncio
async def test_admin_cannot_change_owner_or_create_privileged_invitation(api):
    _, owner, admin, _ = api
    resource = await seed(owner)
    organization_id = resource.project["organizationId"]
    await join(owner, organization_id, admin, "admin")
    owner_id = (await owner.get("/api/v1/me")).json()["user"]["id"]
    admin_id = (await admin.get("/api/v1/me")).json()["user"]["id"]
    await expect_response(
        admin, "POST", f"/api/v1/organizations/{organization_id}/workspaces", 201, json={"name": "Admin workspace"}
    )
    await expect_response(admin, "PATCH", f"/api/v1/organizations/{organization_id}", 403, json={"name": "forbidden"})
    await expect_response(
        admin, "PATCH", f"/api/v1/organizations/{organization_id}/members/{owner_id}", 403, json={"role": "viewer"}
    )
    await expect_response(
        admin, "PATCH", f"/api/v1/organizations/{organization_id}/members/{admin_id}", 403, json={"role": "owner"}
    )
    await expect_response(
        admin,
        "POST",
        f"/api/v1/organizations/{organization_id}/invitations",
        403,
        json={"email": "future@example.com", "role": "admin"},
    )


@pytest.mark.asyncio
async def test_concurrent_owners_cannot_both_leave(api):
    _, first, second, runtime = api
    resource = await seed(first)
    organization_id = resource.project["organizationId"]
    _, _, account = await join(first, organization_id, second)
    first_id = (await first.get("/api/v1/me")).json()["user"]["id"]
    root = f"/api/v1/organizations/{organization_id}/members/"
    await expect_response(first, "PATCH", root + first_id, 409, json={"role": "member"})
    await expect_response(first, "PATCH", root + account["id"], json={"role": "owner"})
    results = await asyncio.gather(first.delete(root + first_id), second.delete(root + account["id"]))
    assert sorted(r.status_code for r in results) == [200, 409]
    async with runtime.app.state.context.db.sessions() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(Membership)
            .where(Membership.organization_id == UUID(organization_id), Membership.role == "owner")
        )
        assert count == 1


@pytest.mark.asyncio
async def test_https_cookie_and_delete_cors_contract(database_settings):
    import httpx
    from test_api import StubRuntime

    from app.main import create_app

    origin = "https://flowtest.example.com"
    settings = database_settings.model_copy(
        update={"cookie_secure": True, "base_url": origin, "app_url": origin, "trusted_origins": [origin]}
    )
    app = create_app(settings, StubRuntime())
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url=origin) as client:
            response = await client.post(
                "/api/auth/sign-up/email",
                json={
                    "name": "HTTPS browser",
                    "email": "https-user@example.com",
                    "password": "test-only-https-password",
                },
                headers={"Origin": origin},
            )
            assert response.status_code == 201
            cookie = response.headers["set-cookie"].lower()
            assert "secure" in cookie and "httponly" in cookie and "samesite=lax" in cookie
            assert (await client.get("/api/v1/me")).status_code == 200
            preflight = await client.options(
                "/api/v1/organizations", headers={"Origin": origin, "Access-Control-Request-Method": "DELETE"}
            )
            assert preflight.status_code == 200
            assert "DELETE" in preflight.headers["access-control-allow-methods"]
            assert preflight.headers["access-control-allow-credentials"] == "true"
            forbidden = await client.post(
                "/api/v1/organizations", json={"name": "cross site"}, headers={"Origin": "https://foreign.example"}
            )
            assert forbidden.status_code == 403
