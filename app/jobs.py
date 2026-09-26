import asyncio
import copy
import uuid
from pathlib import Path

from sqlalchemy import select

from .domain import TERMINAL_RUNS
from .models import Artifact, Resource, RunEvent
from .observability import Event, emit
from .responses import RunSummary
from .runtime import RunResult
from .security import redact, secrets_of
from .store import timestamp, update_resource

TERMINAL = TERMINAL_RUNS
SUMMARY = RunSummary(total=0, passed=0, failed=0, skipped=0, flaky=0, unverified=0).model_dump()


class Jobs:
    def __init__(self, settings, db, vault, runtime):
        self.settings, self.db, self.vault, self.runtime = settings, db, vault, runtime
        self.tasks = {}
        self.slots = asyncio.Semaphore(settings.max_concurrent_runs)

    def schedule(self, ident):
        self.track(str(ident), self.execute(ident))

    def track(self, key, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks[key] = task

        def completed(finished):
            self.tasks.pop(key, None)
            if not finished.cancelled() and (error := finished.exception()) is not None:
                emit(Event.BACKGROUND_FAILED, task=key, errorType=type(error).__name__)

        task.add_done_callback(completed)
        return task

    async def recover(self):
        interrupted = []
        async with self.db.sessions() as session:
            rows = (await session.scalars(select(Resource).where(Resource.kind.in_(["run", "recording"])))).all()
            for row in rows:
                if row.kind == "run" and row.body["status"] in {"queued", "running"}:
                    interrupted.append(("run", str(row.id)))
                    update_resource(
                        row,
                        {
                            "status": "error",
                            "error": "API process interrupted before completion; create a new run",
                            "finishedAt": timestamp(),
                        },
                    )
                    session.add(
                        RunEvent(
                            run_id=row.id, type="run.error", timestamp=timestamp(), data={"code": "PROCESS_INTERRUPTED"}
                        )
                    )
                if row.kind == "recording" and row.body["status"] in {"starting", "ready", "stopping"}:
                    interrupted.append(("recording", str(row.id)))
                    update_resource(
                        row,
                        {
                            "status": "error",
                            "error": "Recording controller interrupted; start a new session",
                            "viewerUrl": None,
                        },
                    )
            await session.commit()
        if interrupted:
            try:
                await self.runtime.recover(interrupted)
            except Exception:
                # Persistent interruption is already visible. Preserve an actionable cleanup failure as well.
                async with self.db.sessions() as session:
                    for kind, ident in interrupted:
                        row = await session.get(Resource, uuid.UUID(ident), with_for_update=True)
                        update_resource(
                            row, {"error": row.body["error"] + "; runtime cleanup requires Docker availability"}
                        )
                    await session.commit()

    async def execute(self, ident):
        async with self.slots:
            env = {}
            try:
                async with self.db.sessions() as session:
                    row = await session.get(Resource, ident, with_for_update=True)
                    if row.body["status"] in TERMINAL:
                        return
                    update_resource(row, {"status": "running", "startedAt": timestamp()})
                    body = copy.deepcopy(row.body)
                    await session.commit()
                env = self.vault.open(body["encryptedEnvironment"])
                work_dir = self.settings.data_dir / "runs" / str(ident)
                await asyncio.to_thread(work_dir.mkdir, parents=True, exist_ok=True)
                secrets = secrets_of(env)

                async def emit(event):
                    safe = redact(event, secrets)
                    async with self.db.sessions() as session:
                        session.add(
                            RunEvent(
                                run_id=ident,
                                type=safe["type"][:100],
                                timestamp=safe.get("timestamp", timestamp()),
                                data=safe.get("data", {}),
                            )
                        )
                        await session.commit()

                await emit({"type": "run.started", "timestamp": timestamp(), "data": {"runId": str(ident)}})
                result = await self.runtime.run(
                    {
                        "id": str(ident),
                        "projectId": body["projectId"],
                        "workDir": str(work_dir.resolve()),
                        "versions": body["versions"],
                        "environment": env,
                        "role": body["role"],
                        "retries": body["retries"],
                        "timeoutMs": body["timeoutMs"],
                        "platformOrigins": self.settings.trusted_origins + [self.settings.base_url],
                    },
                    emit,
                )
                result = RunResult.model_validate(result).model_dump(mode="json")
                if result["status"] not in TERMINAL:
                    raise ValueError("Runtime returned a nonterminal run result")
                await self.finish(ident, result, work_dir, secrets)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self.finish(
                    ident,
                    {
                        "status": "error",
                        "verification": "pending",
                        "summary": SUMMARY,
                        "error": redact(str(exc), secrets_of(env))[:2000],
                        "artifacts": [],
                    },
                    None,
                    secrets_of(env),
                )

    async def finish(self, ident, result, work_dir, secrets):
        safe = redact({k: v for k, v in result.items() if k != "artifacts"}, secrets)
        async with self.db.sessions() as session:
            row = await session.get(Resource, ident, with_for_update=True)
            if row.body["status"] == "cancelled":
                safe["status"] = "cancelled"
            update_resource(
                row,
                {
                    "status": safe["status"],
                    "verification": safe["verification"],
                    "summary": safe["summary"],
                    "error": safe.get("error"),
                    "finishedAt": timestamp(),
                },
            )
            for item in result.get("artifacts", []):
                filename = Path(item["path"])
                if not filename.is_absolute() and work_dir:
                    filename = work_dir / filename
                try:
                    resolved = filename.resolve(strict=True)
                    if (
                        not work_dir
                        or not resolved.is_relative_to(work_dir.resolve())
                        or not resolved.is_file()
                        or filename.is_symlink()
                    ):
                        continue
                    # A private file ID, never a client-controlled path, is the only download handle.
                    session.add(
                        Artifact(
                            run_id=ident,
                            body={
                                "path": str(resolved),
                                "name": item["name"],
                                "contentType": item["contentType"],
                                "kind": item["kind"],
                                "size": resolved.stat().st_size,
                            },
                        )
                    )
                except (ValueError, OSError):
                    continue
            session.add(
                RunEvent(run_id=ident, type="run.finished", timestamp=timestamp(), data={"runId": str(ident), **safe})
            )
            await session.commit()

    async def close(self):
        await self.runtime.close()
        tasks = list(self.tasks.values())
        if tasks:
            done, pending = await asyncio.wait(tasks, timeout=5)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
