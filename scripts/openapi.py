"""Generate and check the request/response contract from the actual FastAPI app."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.main import create_app  # noqa: E402
from app.openapi import CONTRACT, build_openapi, validate_openapi  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail if the saved contract is stale or invalid")
    args = parser.parse_args()
    document = build_openapi(create_app())
    errors = validate_openapi(document)
    serialized = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    if args.check:
        saved = CONTRACT.read_text(encoding="utf-8")
        errors.extend(validate_openapi(json.loads(saved)))
        if saved != serialized:
            errors.append("docs/openapi.json is stale; run uv run python scripts/openapi.py")
    elif not errors:
        CONTRACT.write_text(serialized, encoding="utf-8", newline="\n")
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(
        f"OpenAPI valid: {len(document['paths'])} paths, {sum(len(item) for item in document['paths'].values())} operations"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
