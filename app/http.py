import uuid
from time import perf_counter

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException

from .config import Settings
from .errors import APIError, ErrorCode
from .observability import Event, emit, request_id


def install_http(app: FastAPI, settings: Settings):
    @app.middleware("http")
    async def request_boundary(request, call_next):
        request.state.request_id = str(uuid.uuid4())
        token = request_id.set(request.state.request_id)
        started = perf_counter()
        status = 500
        try:
            response = await dispatch(request, call_next)
            status = response.status_code
            response.headers["X-Request-ID"] = request.state.request_id
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Cache-Control"] = "no-store"
            response.headers["Referrer-Policy"] = "no-referrer"
            return response
        finally:
            route = request.scope.get("route")
            emit(
                Event.REQUEST_COMPLETED,
                method=request.method,
                route=route.path if route else "unmatched",
                status=status,
                durationMs=round((perf_counter() - started) * 1000, 2),
                errorCode=getattr(request.state, "error_code", None),
            )
            request_id.reset(token)

    async def dispatch(request, call_next):
        origin = request.headers.get("origin")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and origin and origin not in settings.trusted_origins:
            return error_response(request, APIError(ErrorCode.ORIGIN_FORBIDDEN))
        if request.method not in {"GET", "HEAD", "OPTIONS"} and request.headers.get("sec-fetch-site") == "cross-site":
            return error_response(
                request, APIError(ErrorCode.ORIGIN_FORBIDDEN, message="Cross-site writes are forbidden")
            )
        try:
            content_length = int(request.headers.get("content-length", "0"))
        except ValueError:
            return error_response(request, APIError(ErrorCode.VALIDATION_ERROR, message="Invalid Content-Length"))
        if content_length < 0:
            return error_response(request, APIError(ErrorCode.VALIDATION_ERROR, message="Invalid Content-Length"))
        if content_length > 2 * 1024 * 1024:
            return error_response(request, APIError(ErrorCode.PAYLOAD_TOO_LARGE))
        return await call_next(request)

    def error_response(request, exc):
        request.state.error_code = exc.code
        if request.url.path.startswith("/api/auth/"):
            body = {"code": exc.code, "message": exc.message}
        else:
            body = {
                "error": {"code": exc.code, "message": exc.message},
                "requestId": getattr(request.state, "request_id", ""),
            }
            if exc.details is not None:
                body["error"]["details"] = exc.details
        return JSONResponse(body, status_code=exc.status)

    @app.exception_handler(APIError)
    async def api_error(request, exc):
        return error_response(request, exc)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Do not echo Pydantic 'input': it can contain passwords or environment credentials.
        details = [
            {"field": ".".join(map(str, e["loc"])), "message": e["msg"], "type": e["type"]} for e in exc.errors()
        ]
        return error_response(request, APIError(ErrorCode.VALIDATION_ERROR, details=details))

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return error_response(
            request,
            APIError(ErrorCode.NOT_FOUND)
            if exc.status_code == 404
            else APIError(ErrorCode.HTTP_ERROR, status=exc.status_code, message=str(exc.detail)),
        )

    @app.exception_handler(IntegrityError)
    async def integrity_error(request, exc):
        if getattr(exc.orig, "sqlstate", None) != "23505":
            raise exc
        return error_response(request, APIError(ErrorCode.CONFLICT))
