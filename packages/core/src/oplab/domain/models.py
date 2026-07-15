from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from oplab.domain.enums import (
    ClaimStatus,
    EvidenceStance,
    MeetingStatus,
    ProjectStatus,
    ResearchPhase,
    RunStatus,
    TaskStatus,
)


def new_id() -> str:
    return str(uuid4())


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Project(Base, TimestampMixin):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(240))
    stage: Mapped[str] = mapped_column(String(64), default="discovery")
    status: Mapped[str] = mapped_column(String(32), default=ProjectStatus.ACTIVE.value)
    budget: Mapped[dict] = mapped_column(JSON, default=dict)

    questions: Mapped[list[ResearchQuestion]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class ResearchQuestion(Base, TimestampMixin):
    __tablename__ = "research_questions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    text: Mapped[str] = mapped_column(Text)
    success_criteria: Mapped[list] = mapped_column(JSON, default=list)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=True)

    project: Mapped[Project] = relationship(back_populates="questions")


class ResearchTask(Base, TimestampMixin):
    __tablename__ = "research_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    parent_task_id: Mapped[str | None] = mapped_column(ForeignKey("research_tasks.id"))
    title: Mapped[str] = mapped_column(String(240))
    objective: Mapped[str] = mapped_column(Text)
    owner: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(32), default=TaskStatus.TODO.value)
    success_criteria: Mapped[list] = mapped_column(JSON, default=list)
    evidence_requirements: Mapped[list] = mapped_column(JSON, default=list)


class Source(Base, TimestampMixin):
    __tablename__ = "sources"
    __table_args__ = (UniqueConstraint("project_id", "content_hash"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    source_type: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(500))
    uri: Mapped[str] = mapped_column(Text)
    doi: Mapped[str | None] = mapped_column(String(255))
    authors: Mapped[list] = mapped_column(JSON, default=list)
    published_at: Mapped[str | None] = mapped_column(String(32))
    content_hash: Mapped[str] = mapped_column(String(64))
    quality: Mapped[dict] = mapped_column(JSON, default=dict)
    license_status: Mapped[str] = mapped_column(String(64), default="metadata_only")

    passages: Mapped[list[Passage]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )


class Passage(Base, TimestampMixin):
    __tablename__ = "passages"
    __table_args__ = (UniqueConstraint("source_id", "passage_hash"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"))
    locator: Mapped[str] = mapped_column(String(120))
    text: Mapped[str] = mapped_column(Text)
    start_char: Mapped[int | None] = mapped_column(Integer)
    end_char: Mapped[int | None] = mapped_column(Integer)
    passage_hash: Mapped[str] = mapped_column(String(64))

    source: Mapped[Source] = relationship(back_populates="passages")


class Claim(Base, TimestampMixin):
    __tablename__ = "claims"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    canonical_text: Mapped[str] = mapped_column(Text)
    scope: Mapped[str] = mapped_column(String(255), default="project")
    status: Mapped[str] = mapped_column(String(32), default=ClaimStatus.UNVERIFIED.value)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    owner: Mapped[str] = mapped_column(String(80))
    derived_from_claims: Mapped[list] = mapped_column(JSON, default=list)
    last_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EvidenceLink(Base, TimestampMixin):
    __tablename__ = "evidence_links"
    __table_args__ = (UniqueConstraint("claim_id", "passage_id", "stance"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    claim_id: Mapped[str] = mapped_column(ForeignKey("claims.id", ondelete="CASCADE"))
    passage_id: Mapped[str] = mapped_column(ForeignKey("passages.id", ondelete="CASCADE"))
    stance: Mapped[str] = mapped_column(String(16), default=EvidenceStance.SUPPORTS.value)
    rationale: Mapped[str] = mapped_column(Text)
    strength: Mapped[float] = mapped_column(Float, default=0.5)


class ResearchRun(Base, TimestampMixin):
    __tablename__ = "research_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    thread_id: Mapped[str] = mapped_column(String(80), unique=True)
    trace_id: Mapped[str] = mapped_column(String(80), unique=True)
    status: Mapped[str] = mapped_column(String(32), default=RunStatus.PENDING.value)
    current_phase: Mapped[str] = mapped_column(String(32), default=ResearchPhase.CHARTER.value)
    stop_reason: Mapped[str | None] = mapped_column(String(64))
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    report_artifact_id: Mapped[str | None] = mapped_column(String(36))


class Meeting(Base, TimestampMixin):
    __tablename__ = "meetings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    run_id: Mapped[str] = mapped_column(ForeignKey("research_runs.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(32), default=MeetingStatus.PREPARING.value)
    trigger: Mapped[str] = mapped_column(String(120))
    agenda: Mapped[list] = mapped_column(JSON, default=list)
    evidence_packet: Mapped[dict] = mapped_column(JSON, default=dict)
    position_cards: Mapped[list] = mapped_column(JSON, default=list)


class Decision(Base, TimestampMixin):
    __tablename__ = "decisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(24))
    rationale: Mapped[str] = mapped_column(Text)
    direction: Mapped[str | None] = mapped_column(Text)
    dissent: Mapped[list] = mapped_column(JSON, default=list)
    decided_by: Mapped[str] = mapped_column(String(80), default="user")


class Artifact(Base, TimestampMixin):
    __tablename__ = "artifacts"
    __table_args__ = (UniqueConstraint("project_id", "content_hash", "kind"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    run_id: Mapped[str | None] = mapped_column(ForeignKey("research_runs.id"))
    kind: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(300))
    path: Mapped[str] = mapped_column(Text)
    media_type: Mapped[str] = mapped_column(String(120))
    content_hash: Mapped[str] = mapped_column(String(64))
    provenance: Mapped[dict] = mapped_column(JSON, default=dict)


class DomainEvent(Base):
    __tablename__ = "domain_events"

    sequence: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id: Mapped[str] = mapped_column(String(36), unique=True, default=new_id)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    aggregate_type: Mapped[str] = mapped_column(String(64))
    aggregate_id: Mapped[str] = mapped_column(String(36))
    event_type: Mapped[str] = mapped_column(String(100), index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"

    key: Mapped[str] = mapped_column(String(180), primary_key=True)
    command_type: Mapped[str] = mapped_column(String(100))
    result_id: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
