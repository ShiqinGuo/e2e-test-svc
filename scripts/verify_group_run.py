"""Verify one completed Web-triggered group run from persisted PostgreSQL evidence."""

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import Settings  # noqa: E402
from app.db import Database  # noqa: E402
from app.models import Artifact, Resource, RunEvent  # noqa: E402


async def verify(run_id: UUID):
    database = Database(Settings())
    try:
        async with database.sessions() as session:
            run = await session.get(Resource, run_id)
            assert run and run.kind == "run", "Run does not exist"
            body = run.body
            assert body.get("groupId") and body.get("scenarioId") is None, "This is not a group run"
            versions = body["versions"]
            scenarios = {version["scenarioId"] for version in versions}
            assert len(scenarios) >= 2, "At least two independent scenario snapshots are required"
            events = (
                await session.scalars(select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.id))
            ).all()
            beginnings = [event.data for event in events if event.type == "test.begin"]
            endings = [event.data for event in events if event.type == "test.end"]
            assert scenarios <= {event.get("scenarioId") for event in beginnings}, "Missing scenario execution evidence"
            assert {v["id"] for v in versions} <= {event.get("versionId") for event in beginnings}, (
                "Executed versions do not match pinned snapshots"
            )
            assert len({event["testId"] for event in endings}) >= 2, "Missing per-test completion evidence"
            assert body["summary"]["total"] == len({event["testId"] for event in endings}), (
                "Group summary does not match actual test completions"
            )
            assert all(event["status"] == "passed" and event["assertions"] > 0 for event in endings), (
                "A test did not pass with executed assertions"
            )
            assert body["status"] == "passed" and body["verification"] == "verified", "Group did not pass verified"
            artifacts = (await session.scalars(select(Artifact).where(Artifact.run_id == run_id))).all()
            traces = [str(artifact.id) for artifact in artifacts if artifact.body["kind"] == "trace"]
            assert len(traces) >= 2, "Expected independent trace artifacts for both tests"
            by_name = {artifact.body["name"]: artifact for artifact in artifacts}
            associations = []
            for ending in endings:
                attached = []
                for attachment in ending.get("attachments", []):
                    if not attachment.get("path"):
                        continue
                    name = attachment["path"].replace("\\", "/").removeprefix("artifacts/")
                    artifact = by_name.get(name)
                    assert artifact is not None, "A test attachment is missing from the private artifact registry"
                    attached.append({"id": str(artifact.id), "name": name, "kind": artifact.body["kind"]})
                assert any(item["kind"] == "trace" for item in attached), (
                    "A completed test has no matching private trace"
                )
                associations.append({"testId": ending["testId"], "attempt": ending["attempt"], "artifacts": attached})
            report = {
                "verifiedAt": datetime.now(UTC).isoformat(),
                "runId": str(run_id),
                "projectId": str(run.project_id),
                "groupId": body["groupId"],
                "environmentId": body["environmentId"],
                "status": body["status"],
                "verification": body["verification"],
                "summary": body["summary"],
                "pinnedVersions": [
                    {"scenarioId": v["scenarioId"], "versionId": v["id"], "number": v["number"]} for v in versions
                ],
                "tests": [
                    {
                        "testId": e["testId"],
                        "scenarioId": e["scenarioId"],
                        "versionId": e["versionId"],
                        "attempt": e["attempt"],
                    }
                    for e in beginnings
                ],
                "completions": [
                    {
                        "testId": e["testId"],
                        "attempt": e["attempt"],
                        "status": e["status"],
                        "assertions": e["assertions"],
                    }
                    for e in endings
                ],
                "eventCount": len(events),
                "traceArtifactIds": traces,
                "artifactAssociations": associations,
                "evidenceBoundary": "PostgreSQL snapshots and real runner events/artifacts verified here; the separate frontend task records the Web group selection and request.",
            }
            target = ROOT / "data/group-api-acceptance.json"
            target.write_text(json.dumps(report, indent=2), encoding="utf8")
            print(
                f"Group run verified: {run_id}; {len(scenarios)} scenarios, {len(events)} events, {len(traces)} traces"
            )
    finally:
        await database.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id", type=UUID)
    asyncio.run(verify(parser.parse_args().run_id))
