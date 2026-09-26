"""Write a non-secret local deployment/source snapshot for review."""

import argparse
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int, required=True, help="Observed Uvicorn server PID")
    args = parser.parse_args()
    files = []
    for pattern in (
        "app/**/*.py",
        "src/runtime/*.ts",
        "runtime/bridge.ts",
        "runtime/runner/*.mjs",
        "runtime/runner/*.ts",
        "runtime/runner/Dockerfile",
        "runtime/recorder/*.mjs",
        "runtime/recorder/Dockerfile",
        "uv.lock",
        "package-lock.json",
        "migrations/versions/*.py",
    ):
        files.extend(ROOT.glob(pattern))
    hashes = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(set(files))}
    revision = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
    images = {}
    for image in ("e2e-runner:1.63.0", "e2e-recorder:1.63.0", "postgres:18.6-alpine"):
        images[image] = subprocess.check_output(
            ["docker", "image", "inspect", image, "--format", "{{.Id}}"], text=True
        ).strip()
    response = httpx.get("http://127.0.0.1:4100/api/openapi.json", timeout=10)
    response.raise_for_status()
    document = response.json()
    result = {
        "capturedAt": datetime.now(UTC).isoformat(),
        "apiVersion": "0.1.0",
        "contractVersion": document["info"]["version"],
        "apiPid": args.pid,
        "apiUrl": "http://127.0.0.1:4100",
        "sourceRevision": revision,
        "openapiPaths": len(document["paths"]),
        "images": images,
        "files": hashes,
        "note": "PID was observed at service startup. Source/image hashes describe the checked local files; this is not a Git revision or remote deployment attestation.",
    }
    destination = ROOT / "data/deployment-snapshot.json"
    destination.write_text(json.dumps(result, indent=2), encoding="utf8")
    print(f"Deployment snapshot written: revision {revision[:12]}, PID {args.pid}")


if __name__ == "__main__":
    main()
