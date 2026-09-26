"""Request summaries and committed business results; never request bodies or credentials."""

import json
import logging
from contextvars import ContextVar
from enum import StrEnum
from functools import lru_cache

from opentelemetry import trace
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import Resource as TraceResource
from opentelemetry.sdk.trace import TracerProvider
from sqlalchemy import event
from sqlalchemy.orm import Session

request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
logger = logging.getLogger("e2e.events")


class Event(StrEnum):
    REQUEST_COMPLETED = "request.completed"
    RESOURCE_CREATED = "resource.created"
    RESOURCE_TRANSITIONED = "resource.transitioned"
    BACKGROUND_FAILED = "background.failed"
    ORGANIZATION_CREATED = "organization.created"
    MEMBERSHIP_CHANGED = "membership.changed"
    INVITATION_CREATED = "invitation.created"
    INVITATION_ACCEPTED = "invitation.accepted"
    INVITATION_REVOKED = "invitation.revoked"


def emit(event_name: Event, **fields) -> None:
    # A telemetry sink is not allowed to turn a committed transaction into an API failure.
    try:
        span = trace.get_current_span().get_span_context()
        logger.info(
            json.dumps(
                {
                    "event": event_name,
                    "requestId": request_id.get(),
                    "traceId": format(span.trace_id, "032x") if span.is_valid else None,
                    "spanId": format(span.span_id, "016x") if span.is_valid else None,
                    **fields,
                }
            )
        )
    except Exception:
        pass


@lru_cache(maxsize=1)
def configure_tracing():
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        logger.addHandler(logging.StreamHandler())
    logger.propagate = False
    provider = trace.get_tracer_provider()
    if isinstance(provider, trace.ProxyTracerProvider):
        provider = TracerProvider(resource=TraceResource.create({"service.name": "e2e-test-svc"}))
        trace.set_tracer_provider(provider)
    # Instrument once per process, before constructing engines or external clients.
    SQLAlchemyInstrumentor().instrument(tracer_provider=provider)
    HTTPXClientInstrumentor().instrument(tracer_provider=provider)
    return provider


def after_commit_event(session: Session, name: Event, **fields) -> None:
    session.info.setdefault("business_events", []).append((name, fields))


@event.listens_for(Session, "after_commit")
def committed(session: Session) -> None:
    for name, fields in session.info.pop("business_events", []):
        emit(name, **fields)


@event.listens_for(Session, "after_rollback")
def rolled_back(session: Session) -> None:
    session.info.pop("business_events", None)
