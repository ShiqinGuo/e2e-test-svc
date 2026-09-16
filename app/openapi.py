"""Merge actual FastAPI request models with the reviewed response contract.

Request models use a namespace because Role is an input model while the public
Role response deliberately contains only redacted credential-presence flags.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

CONTRACT = Path(__file__).resolve().parents[1] / "docs" / "openapi.json"
METHODS = {"get", "post", "put", "patch", "delete", "options", "head"}


def _rewrite_request_refs(value: Any) -> Any:
    if isinstance(value, list):
        return [_rewrite_request_refs(item) for item in value]
    if isinstance(value, dict):
        return {
            key: item.replace("#/components/schemas/", "#/components/schemas/Request", 1)
            if key == "$ref" and isinstance(item, str) and item.startswith("#/components/schemas/")
            else _rewrite_request_refs(item)
            for key, item in value.items()
        }
    return value


def build_openapi(app: FastAPI) -> dict[str, Any]:
    reviewed = json.loads(CONTRACT.read_text(encoding="utf-8"))
    actual = _rewrite_request_refs(get_openapi(title=app.title, version=app.version, routes=app.routes))
    document = copy.deepcopy(reviewed)
    document["openapi"] = "3.1.0"
    document["paths"] = {}
    document["components"]["schemas"] = {
        name: schema for name, schema in document["components"]["schemas"].items() if not name.startswith("Request")
    }
    document["components"]["schemas"].update(
        {f"Request{name}": schema for name, schema in actual.get("components", {}).get("schemas", {}).items()}
    )
    run = document["components"]["schemas"]["Run"]
    run["properties"]["rerunOf"] = {
        "type": "string",
        "description": "Original run identifier when replaying its pinned versions and environment snapshot.",
    }
    run["properties"]["sourceRunId"] = {"type": "string", "description": "Compatibility alias of rerunOf."}
    for path, path_item in actual["paths"].items():
        document["paths"][path] = {}
        for method, actual_operation in path_item.items():
            if method not in METHODS:
                document["paths"][path][method] = actual_operation
                continue
            template = reviewed["paths"].get(path, {}).get(method)
            if template is None and path.endswith("/runs/{runId}/rerun") and method == "post":
                template = copy.deepcopy(reviewed["paths"]["/api/v1/projects/{projectId}/runs"]["post"])
                template.update(
                    operationId="rerunRun",
                    summary="Create an independent run from the original immutable input snapshot",
                    description="Copies the original pinned scenario versions, role, settings and environment snapshot. Subsequent scenario or environment edits do not affect this replay.",
                )
            if template is None:
                raise ValueError(f"Document the success and error response schemas for {method.upper()} {path}")
            operation = copy.deepcopy(template)
            for key in ("parameters", "requestBody"):
                if key in actual_operation:
                    operation[key] = actual_operation[key]
                else:
                    operation.pop(key, None)
            # RequestValidationError is normalized to the documented 400 body.
            operation["responses"].pop("422", None)
            if path.endswith("/runs/{runId}/trace"):
                operation["responses"].pop("200", None)
                operation["responses"]["307"] = {
                    "description": "Navigate to the same-origin, cookie-authorized official Trace Viewer.",
                    "headers": {"Location": {"required": True, "schema": {"type": "string"}}},
                }
            document["paths"][path][method] = operation
    # FastAPI hides its own schema endpoint from generated operations.
    document["paths"]["/api/openapi.json"] = reviewed["paths"]["/api/openapi.json"]
    return document


def validate_openapi(document: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    operation_ids: set[str] = set()
    if document.get("openapi") != "3.1.0":
        errors.append("Expected OpenAPI 3.1.0")

    def walk(value: Any, location: str) -> None:
        if isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{location}/{index}")
        elif isinstance(value, dict):
            for key, item in value.items():
                if key != "$ref":
                    walk(item, f"{location}/{key}")
                    continue
                if not isinstance(item, str) or not item.startswith("#/"):
                    errors.append(f"{location}: expected a local reference")
                    continue
                current: Any = document
                try:
                    for part in item[2:].split("/"):
                        current = current[part.replace("~1", "/").replace("~0", "~")]
                except (KeyError, TypeError):
                    errors.append(f"{location}: unresolved {item}")

    walk(document, "#")
    for path, item in document.get("paths", {}).items():
        for method, operation in item.items():
            if method not in METHODS:
                continue
            ident = operation.get("operationId")
            if not ident or ident in operation_ids:
                errors.append(f"{method} {path}: missing or duplicate operationId")
            operation_ids.add(ident)
            success = {
                status: response
                for status, response in operation.get("responses", {}).items()
                if status.startswith(("2", "3"))
            }
            if not success:
                errors.append(f"{method} {path}: no success or redirect response")
            for status, response in success.items():
                if status == "204" or status.startswith("3"):
                    continue
                for media_type, content in response.get("content", {}).items():
                    if media_type == "application/json" and not content.get("schema"):
                        errors.append(f"{method} {path}: empty JSON success schema")
    return errors


def install_openapi(app: FastAPI) -> None:
    def openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            document = build_openapi(app)
            errors = validate_openapi(document)
            if errors:
                raise ValueError("\n".join(errors))
            app.openapi_schema = document
        return app.openapi_schema

    app.openapi = openapi
