import uuid
from datetime import datetime, timezone

from fastapi_users.db import SQLAlchemyBaseUserTableUUID
from fastapi_users_db_sqlalchemy.access_token import SQLAlchemyBaseAccessTokenTableUUID
from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(SQLAlchemyBaseUserTableUUID, Base):
    name: Mapped[str] = mapped_column(String(200))


class AccessToken(SQLAlchemyBaseAccessTokenTableUUID, Base):
    pass


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"), index=True)
    body: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Resource(Base):
    __tablename__ = "resources"
    __table_args__ = (
        Index("ix_resources_project_kind", "project_id", "kind"),
        UniqueConstraint("parent_id", "version_number", name="uq_scenario_version"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(20))
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"))
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("resources.id"), index=True)
    version_number: Mapped[int | None] = mapped_column(Integer)
    body: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RunEvent(Base):
    __tablename__ = "run_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resources.id"), index=True)
    type: Mapped[str] = mapped_column(String(100))
    timestamp: Mapped[str] = mapped_column(String(40))
    data: Mapped[dict | list | str | None] = mapped_column(JSONB)


class Artifact(Base):
    __tablename__ = "artifacts"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resources.id"), index=True)
    body: Mapped[dict] = mapped_column(JSONB)


class AuthAttempt(Base):
    __tablename__ = "auth_attempts"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
