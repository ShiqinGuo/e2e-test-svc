"""A narrow JSON protocol to trusted Node controllers; no API secrets are inherited."""

import asyncio
import hashlib
import json
import os
import uuid
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

from .config import ROOT, Settings
from .domain import RunStatus, Verification
from .responses import RunSummary, RuntimeAvailability
from .schemas import Check


class RuntimePort(Protocol):
    async def available(self) -> dict: ...
    async def run(self, input: dict, emit: Any) -> dict: ...
    async def cancel(self, ident: str) -> Any: ...
    async def recording_start(self, input: dict) -> Any: ...
    async def recording_read(self, ident: str) -> dict: ...
    async def recording_stop(self, ident: str) -> dict: ...
    async def recording_target(self, ident: str) -> dict | None: ...
    async def recover(self, interrupted: list[tuple[str, str]]) -> None: ...
    async def close(self) -> None: ...


class RuntimeArtifact(BaseModel):
    path: str
    name: str
    contentType: str
    kind: str


class RunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: RunStatus
    verification: Verification
    summary: RunSummary
    error: str | None = None
    artifacts: list[RuntimeArtifact]


class RecordingResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    checks: list[Check]


class Runtime:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.process = None
        self.pending = {}
        self.events = {}
        self.lock = asyncio.Lock()
        self.reader = None
        self.stderr = None

    async def _ensure(self):
        async with self.lock:
            if self.process and self.process.returncode is None:
                return
            allowed = {
                "PATH",
                "SYSTEMROOT",
                "WINDIR",
                "TEMP",
                "TMP",
                "USERPROFILE",
                "HOME",
                "DOCKER_HOST",
                "DOCKER_CONTEXT",
                "DOCKER_CONFIG",
                "PROGRAMFILES",
                "PROGRAMFILES(X86)",
                "COMSPEC",
                "PATHEXT",
            }
            env = {k: v for k, v in os.environ.items() if k.upper() in allowed}
            self.process = await asyncio.create_subprocess_exec(
                self.settings.node_binary,
                "--import",
                "tsx",
                str(ROOT / "runtime/bridge.ts"),
                cwd=ROOT,
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=4 * 1024 * 1024,
            )
            self.reader = asyncio.create_task(self._read(self.process))
            # Controller errors cannot leak credentials into API logs; error responses carry bounded safe messages.
            self.stderr = asyncio.create_task(self._drain_stderr(self.process))

    async def _drain_stderr(self, process):
        while await process.stderr.read(65536):
            pass

    async def _read(self, process):
        failure = RuntimeError("Runtime controller disconnected")
        try:
            async for line in process.stdout:
                message = json.loads(line)
                ident = message.get("id")
                if "event" in message and ident in self.events:
                    await self.events[ident](message["event"])
                elif ident in self.pending:
                    future = self.pending[ident]
                    if not future.done():
                        if "error" in message:
                            future.set_exception(RuntimeError(message["error"]))
                        else:
                            future.set_result(message["result"])
        except Exception as exc:
            # Stop a broken protocol reader; subsequent operations need a fresh controller.
            # Do not include raw frames, which can contain environment credentials.
            failure = RuntimeError("Runtime protocol or event persistence failed")
            failure.__cause__ = exc
        finally:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(failure)
            if process.returncode is None:
                process.kill()
                await process.wait()

    async def _call(self, op, input=None, emit=None, timeout=120):
        await self._ensure()
        ident = str(uuid.uuid4())
        future = asyncio.get_running_loop().create_future()
        self.pending[ident] = future
        if emit:
            self.events[ident] = emit
        try:
            self.process.stdin.write(
                (json.dumps({"id": ident, "op": op, "input": {} if input is None else input}) + "\n").encode()
            )
            await self.process.stdin.drain()
            return await asyncio.wait_for(future, timeout)
        finally:
            self.pending.pop(ident, None)
            self.events.pop(ident, None)

    async def available(self):
        try:
            result = await self._call("available", timeout=20)
        except (OSError, TimeoutError, RuntimeError):
            return {
                "runner": {"available": False, "reason": "Build Playwright runtime images and check Docker"},
                "recorder": {"available": False, "reason": "Build Playwright runtime images and check Docker"},
            }
        return RuntimeAvailability.model_validate(result).model_dump(exclude_none=True)

    async def run(self, input, emit):
        return await self._call("run", input, emit, timeout=input["timeoutMs"] / 1000 + 180)

    async def cancel(self, ident):
        return await self._call("cancel", {"id": ident}, timeout=60)

    async def recording_start(self, input, emit=None):
        return await self._call("recording_start", input, timeout=120)

    async def recording_read(self, ident):
        return await self._call("recording_read", {"id": ident}, timeout=20)

    async def recording_stop(self, ident):
        return await self._call("recording_stop", {"id": ident}, timeout=60)

    async def recording_target(self, ident):
        return await self._call("recording_target", {"id": ident}, timeout=20)

    async def close(self):
        if self.process and self.process.returncode is None:
            try:
                await self._call("close", timeout=60)
            except (OSError, TimeoutError, RuntimeError):
                pass
            self.process.stdin.close()
            try:
                await asyncio.wait_for(self.process.wait(), 10)
            except TimeoutError:
                self.process.kill()
                await self.process.wait()
        for task in (self.reader, self.stderr):
            if task:
                task.cancel()
        await asyncio.gather(*(task for task in (self.reader, self.stderr) if task), return_exceptions=True)

    async def recover(self, interrupted):
        """Remove only resources derived from this database's interrupted records."""

        async def docker(*args):
            process = await asyncio.create_subprocess_exec(
                "docker", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
            )
            output, _ = await asyncio.wait_for(process.communicate(), 30)
            if process.returncode:
                raise RuntimeError("Docker recovery command failed")
            return output.decode().strip()

        for kind, ident in interrupted:
            uuid.UUID(ident)
            run_label = ident if kind == "run" else "recorder-" + hashlib.sha256(ident.encode()).hexdigest()[:24]
            labels = [f"e2e.run={run_label}"]
            if kind == "recording":
                labels.append(f"e2e.recording={ident}")
            for label in labels:
                ids = (
                    await docker("ps", "-aq", "--filter", "label=e2e.platform=true", "--filter", f"label={label}")
                ).split()
                if ids:
                    await docker("rm", "-f", *ids)
            network = f"e2e-net-{run_label[:48]}"
            names = (
                await docker("network", "ls", "--filter", "label=e2e.platform=true", "--format", "{{.Name}}")
            ).split()
            if network in names:
                await docker("network", "rm", network)
            directory = self.settings.data_dir / ("runs" if kind == "run" else "recordings") / ident
            private = [
                directory / "execution" / name for name in ("input.json", "variables.json", "playwright.config.ts")
            ]
            private.append(directory / "input.json")
            for filename in private:
                if filename.exists() and filename.resolve().is_relative_to(self.settings.data_dir.resolve()):
                    filename.unlink()
