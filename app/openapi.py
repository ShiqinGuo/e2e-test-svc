"""Generate the contract from executable request and response models."""

from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from .responses import AuthError, ErrorResponse

CONTRACT = Path(__file__).resolve().parents[1] / "docs" / "openapi.json"
METHODS = {"get", "post", "put", "patch", "delete", "options", "head"}


def build_openapi(app: FastAPI) -> dict[str, Any]:
    document = get_openapi(title=app.title, version=app.version, routes=app.routes)
    schemas = document.setdefault("components", {}).setdefault("schemas", {})
    for model in (AuthError, ErrorResponse):
        schema = model.model_json_schema(ref_template="#/components/schemas/{model}")
        schemas.update(schema.pop("$defs", {}))
        schemas[model.__name__] = schema
    document["components"]["securitySchemes"] = {
        "sessionCookie": {"type": "apiKey", "in": "cookie", "name": "e2e_session"}
    }
    for path, item in document["paths"].items():
        for method, operation in item.items():
            if method not in METHODS:
                continue
            operation["responses"].pop("422", None)
            error_model = "AuthError" if path.startswith("/api/auth/") else "ErrorResponse"
            for status in (400, 401, 403, 404, 409, 413, 429, 503):
                operation["responses"][str(status)] = {
                    "description": "Explicit request, authorization, conflict or availability error",
                    "content": {"application/json": {"schema": {"$ref": f"#/components/schemas/{error_model}"}}},
                }
            if path.startswith("/api/v1/") and path != "/api/v1/invitations/preview":
                operation["security"] = [{"sessionCookie": []}]
    document["paths"]["/api/openapi.json"] = {
        "get": {
            "operationId": "getOpenApi",
            "responses": {
                "200": {
                    "description": "Generated API contract",
                    "content": {"application/json": {"schema": {"type": "object"}}},
                }
            },
        }
    }
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
