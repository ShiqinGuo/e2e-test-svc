"""Minimal authenticated CLI/CI runner. Credentials are read interactively or from a private cookie file."""

import argparse
import getpass
import json
import time
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:4100")
    parser.add_argument(
        "--cookie-file",
        type=Path,
        help="Private file containing an e2e_session cookie value; never passed in arguments",
    )
    parser.add_argument("--email")
    parser.add_argument("--project", required=True)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--scenario")
    target.add_argument("--group")
    target.add_argument("--rerun", help="Original run ID; reuses original version and environment snapshots")
    parser.add_argument("--environment")
    args = parser.parse_args()
    if not args.rerun and not args.environment:
        parser.error("--environment is required unless --rerun is used")
    with httpx.Client(base_url=args.base_url, timeout=30) as client:
        if args.cookie_file:
            client.cookies.set("e2e_session", args.cookie_file.read_text().strip())
        else:
            email = args.email or input("Email: ")
            response = client.post(
                "/api/auth/sign-in/email", json={"email": email, "password": getpass.getpass("Password: ")}
            )
            response.raise_for_status()
        prefix = f"/api/v1/projects/{args.project}/runs"
        if args.rerun:
            response = client.post(f"{prefix}/{args.rerun}/rerun", json={})
        else:
            response = client.post(
                prefix,
                json={
                    "environmentId": args.environment,
                    "scenarioId" if args.scenario else "groupId": args.scenario or args.group,
                },
            )
        response.raise_for_status()
        run = response.json()
        print(f"Run created: {run['id']}", flush=True)
        while run["status"] in {"queued", "running"}:
            time.sleep(1)
            response = client.get(f"{prefix}/{run['id']}")
            response.raise_for_status()
            run = response.json()
        print(
            json.dumps(
                {
                    "id": run["id"],
                    "status": run["status"],
                    "verification": run["verification"],
                    "summary": run["summary"],
                },
                indent=2,
            )
        )
        # A skipped/unverified/flaky suite must not silently satisfy CI quality checks.
        raise SystemExit(
            0 if run["status"] == "passed" and run["verification"] == "verified" and not run["summary"]["flaky"] else 1
        )


if __name__ == "__main__":
    main()
