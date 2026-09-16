"""PostgreSQL regression tests for recorder polling, stop, and save consistency."""

import asyncio

import pytest

pytest_plugins = ("test_api",)


async def recording_fixture(client):
    project = (await client.post("/api/v1/projects", json={"name": "Recorder review"})).json()
    root = f"/api/v1/projects/{project['id']}"
    scenario = (await client.post(f"{root}/scenarios", json={"name": "Recorder review"})).json()
    environment = (
        await client.post(
            f"{root}/environments",
            json={"name": "Fixture", "websites": {"main": "https://fixture.example.test"}, "apiBases": {}},
        )
    ).json()
    recording = (
        await client.post(f"{root}/recordings", json={"environmentId": environment["id"], "website": "main"})
    ).json()
    url = f"{root}/recordings/{recording['id']}"
    async with asyncio.timeout(5):
        while (await client.get(url)).json()["status"] != "ready":
            await asyncio.sleep(0.01)
    return root, url, scenario, recording


@pytest.mark.asyncio
async def test_missing_runtime_target_cannot_leave_recording_ready(api):
    _, owner, _, runtime = api
    _, url, _, _ = await recording_fixture(owner)

    async def stopped_target(_ident):
        return None

    runtime.recording_target = stopped_target
    response = await owner.get(url)
    assert response.status_code == 200
    assert response.json()["status"] != "ready", "runtime target=None still reports ready"


@pytest.mark.asyncio
async def test_poll_finishing_after_stop_must_not_overwrite_final_code(api):
    _, owner, _, runtime = api
    _, url, _, recording = await recording_fixture(owner)
    entered, resume = asyncio.Event(), asyncio.Event()
    original = runtime.recordings[recording["id"]]["code"]
    final = original + "\n// final action only flushed by codegen stop\n"

    async def delayed_read(_ident):
        entered.set()
        await resume.wait()
        return {"code": original, "checks": []}

    async def stop(_ident):
        return {"code": final, "checks": []}

    runtime.recording_read = delayed_read
    runtime.recording_stop = stop
    polling = asyncio.create_task(owner.get(url))
    await entered.wait()
    stopped = await owner.post(url + "/stop", json={})
    assert stopped.json()["status"] == "stopped"
    assert stopped.json()["code"] == final
    resume.set()
    polled = await polling
    assert polled.json()["status"] == "stopped", "stale GET reverted stopped to ready"
    assert polled.json()["code"] == final, "stale GET overwrote final codegen flush"


@pytest.mark.asyncio
async def test_stop_failure_must_not_save_an_older_poll_as_a_successful_version(api):
    _, owner, _, runtime = api
    _, url, scenario, _ = await recording_fixture(owner)

    async def failed_stop(_ident):
        raise RuntimeError("Recorder flush failed")

    runtime.recording_stop = failed_stop
    response = await owner.post(url + "/save", json={"scenarioId": scenario["id"], "changeNote": "Save recording"})
    assert response.status_code == 409, (
        f"failed stop/flush nevertheless created immutable version: {response.status_code}"
    )
