from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from oplab.domain.commands import (
    AddEvidence,
    AuthorityEnvelope,
    ContestClaim,
    CreateTask,
    ProposeClaim,
    PublishArtifact,
    RecordDecision,
    RequestMeeting,
)
from oplab.domain.enums import (
    ClaimStatus,
    EvidenceStance,
    MeetingStatus,
    ProjectStatus,
    ResearchPhase,
    RunStatus,
    StopReason,
    TaskStatus,
)
from oplab.domain.models import (
    Artifact,
    Claim,
    Decision,
    DomainEvent,
    EvidenceLink,
    IdempotencyRecord,
    Meeting,
    Passage,
    Project,
    ResearchQuestion,
    ResearchRun,
    ResearchTask,
    Source,
    utcnow,
)


class PolicyDeniedError(PermissionError):
    pass


@dataclass(frozen=True)
class CommandResult:
    entity_id: str
    replayed: bool = False


AGENT_AUTHORITIES: dict[str, AuthorityEnvelope] = {
    "pi": AuthorityEnvelope(
        actor="pi", may_create_task=True, may_propose_claim=True, may_request_meeting=True
    ),
    "librarian": AuthorityEnvelope(
        actor="librarian", may_add_evidence=True, may_propose_claim=True
    ),
    "skeptic": AuthorityEnvelope(actor="skeptic", may_add_evidence=True, may_propose_claim=True),
    "writer": AuthorityEnvelope(actor="writer", may_publish_artifact=True),
    "user": AuthorityEnvelope(
        actor="user",
        may_create_task=True,
        may_add_evidence=True,
        may_propose_claim=True,
        may_request_meeting=True,
        may_publish_artifact=True,
    ),
}


class DomainService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]):
        self.sessions = sessions

    async def create_project(
        self,
        *,
        title: str,
        question: str,
        success_criteria: list[str],
    ) -> Project:
        async with self.sessions.begin() as session:
            project = Project(title=title, status=ProjectStatus.ACTIVE.value)
            session.add(project)
            await session.flush()
            session.add(
                ResearchQuestion(
                    project_id=project.id,
                    text=question,
                    success_criteria=success_criteria,
                )
            )
            session.add(
                DomainEvent(
                    project_id=project.id,
                    aggregate_type="Project",
                    aggregate_id=project.id,
                    event_type="ProjectCreated",
                    payload={"title": title, "question": question},
                )
            )
        return project

    async def start_run(self, project_id: str) -> ResearchRun:
        async with self.sessions.begin() as session:
            project = await session.get(Project, project_id)
            if project is None:
                raise LookupError("Project not found")
            run = ResearchRun(
                project_id=project_id,
                thread_id=f"thread-{uuid4()}",
                trace_id=f"trace-{uuid4()}",
                status=RunStatus.PENDING.value,
                current_phase=ResearchPhase.PLAN.value,
            )
            session.add(run)
            await session.flush()
            session.add(
                DomainEvent(
                    project_id=project_id,
                    aggregate_type="ResearchRun",
                    aggregate_id=run.id,
                    event_type="ResearchRunCreated",
                    payload={"thread_id": run.thread_id, "trace_id": run.trace_id},
                )
            )
        return run

    async def record_harness_event(
        self,
        *,
        project_id: str,
        run_id: str,
        event_type: str,
        payload: dict,
    ) -> None:
        """Persist decision/tool/evaluation trajectory independently of model chat history."""
        async with self.sessions.begin() as session:
            session.add(
                DomainEvent(
                    project_id=project_id,
                    aggregate_type="ResearchRun",
                    aggregate_id=run_id,
                    event_type=event_type,
                    payload=payload,
                )
            )

    async def set_task_status(self, task_id: str, status: TaskStatus) -> None:
        async with self.sessions.begin() as session:
            task = await session.get(ResearchTask, task_id)
            if task is None:
                return
            if task.status == status.value:
                return
            task.status = status.value
            session.add(
                DomainEvent(
                    project_id=task.project_id,
                    aggregate_type="ResearchTask",
                    aggregate_id=task.id,
                    event_type="TaskStatusChanged",
                    payload={"status": status.value},
                )
            )

    async def set_run_state(
        self,
        run_id: str,
        *,
        status: RunStatus,
        phase: ResearchPhase,
        state: dict,
        stop_reason: StopReason | None = None,
        error: str | None = None,
        report_artifact_id: str | None = None,
    ) -> None:
        async with self.sessions.begin() as session:
            run = await session.get(ResearchRun, run_id)
            if run is None:
                raise LookupError("Run not found")
            run.status = status.value
            run.current_phase = phase.value
            run.state = state
            run.stop_reason = stop_reason.value if stop_reason else None
            run.error = error
            if report_artifact_id:
                run.report_artifact_id = report_artifact_id
            session.add(
                DomainEvent(
                    project_id=run.project_id,
                    aggregate_type="ResearchRun",
                    aggregate_id=run.id,
                    event_type="ResearchRunStateChanged",
                    payload={
                        "status": status.value,
                        "phase": phase.value,
                        "stop_reason": run.stop_reason,
                    },
                )
            )

    async def execute(self, command: object) -> CommandResult:
        actor = command.actor
        authority = AGENT_AUTHORITIES.get(actor)
        if authority is None:
            raise PolicyDeniedError(f"Unknown actor: {actor}")

        async with self.sessions.begin() as session:
            replay = await session.get(IdempotencyRecord, command.idempotency_key)
            if replay:
                return CommandResult(replay.result_id, replayed=True)

            if isinstance(command, CreateTask):
                self._require(authority.may_create_task, actor, "create tasks")
                entity = ResearchTask(
                    project_id=command.project_id,
                    title=command.title,
                    objective=command.objective,
                    owner=command.owner,
                    status=TaskStatus.TODO.value,
                    success_criteria=command.success_criteria,
                    evidence_requirements=command.evidence_requirements,
                )
                event_type = "TaskCreated"
            elif isinstance(command, AddEvidence):
                self._require(authority.may_add_evidence, actor, "add evidence")
                entity = await self._add_evidence(session, command)
                event_type = "EvidenceAdded"
            elif isinstance(command, ProposeClaim):
                self._require(authority.may_propose_claim, actor, "propose claims")
                entity = await self._propose_claim(session, command)
                event_type = "ClaimProposed"
            elif isinstance(command, ContestClaim):
                self._require(authority.may_propose_claim, actor, "contest claims")
                entity = await self._contest_claim(session, command)
                event_type = "ClaimContested"
            elif isinstance(command, RequestMeeting):
                self._require(authority.may_request_meeting, actor, "request meetings")
                entity = Meeting(
                    project_id=command.project_id,
                    run_id=command.run_id,
                    status=MeetingStatus.WAITING_USER.value,
                    trigger=command.trigger,
                    agenda=command.agenda,
                    evidence_packet=command.evidence_packet,
                    position_cards=command.position_cards,
                )
                event_type = "MeetingRequested"
            elif isinstance(command, RecordDecision):
                entity = await self._record_decision(session, command)
                event_type = "DecisionRecorded"
            elif isinstance(command, PublishArtifact):
                self._require(authority.may_publish_artifact, actor, "publish artifacts")
                entity = Artifact(
                    project_id=command.project_id,
                    run_id=command.run_id,
                    kind=command.kind,
                    title=command.title,
                    path=command.path,
                    media_type=command.media_type,
                    content_hash=command.content_hash,
                    provenance=command.provenance,
                )
                event_type = "ArtifactPublished"
            else:
                raise TypeError(f"Unsupported command: {type(command).__name__}")

            session.add(entity)
            await session.flush()
            session.add(
                IdempotencyRecord(
                    key=command.idempotency_key,
                    command_type=command.command_type,
                    result_id=entity.id,
                )
            )
            session.add(
                DomainEvent(
                    project_id=command.project_id,
                    aggregate_type=type(entity).__name__,
                    aggregate_id=entity.id,
                    event_type=event_type,
                    payload={"actor": actor},
                )
            )
        return CommandResult(entity.id)

    async def _add_evidence(self, session: AsyncSession, command: AddEvidence) -> Source:
        existing = await session.scalar(
            select(Source).where(
                Source.project_id == command.project_id,
                Source.content_hash == command.content_hash,
            )
        )
        if existing:
            return existing
        source = Source(
            project_id=command.project_id,
            source_type=command.source_type,
            title=command.title,
            uri=command.uri,
            doi=command.doi,
            authors=command.authors,
            published_at=command.published_at,
            content_hash=command.content_hash,
            quality=command.quality,
            license_status=command.license_status,
        )
        for item in command.passages:
            source.passages.append(Passage(**item))
        session.add(source)
        await session.flush()
        return source

    async def _propose_claim(self, session: AsyncSession, command: ProposeClaim) -> Claim:
        passage = await session.get(Passage, command.passage_id)
        if passage is None:
            raise LookupError("Passage not found")
        claim = Claim(
            project_id=command.project_id,
            canonical_text=command.canonical_text,
            scope=command.scope,
            confidence=command.confidence,
            owner=command.actor,
            status=ClaimStatus.SUPPORTED.value,
            last_reviewed_at=utcnow(),
        )
        session.add(claim)
        await session.flush()
        session.add(
            EvidenceLink(
                claim_id=claim.id,
                passage_id=passage.id,
                stance=EvidenceStance.SUPPORTS.value,
                rationale=command.rationale,
                strength=command.strength,
            )
        )
        return claim

    async def _contest_claim(self, session: AsyncSession, command: ContestClaim) -> Claim:
        claim = await session.get(Claim, command.claim_id)
        passage = await session.get(Passage, command.passage_id)
        if claim is None or passage is None:
            raise LookupError("Claim or passage not found")
        claim.status = ClaimStatus.CONTESTED.value
        claim.last_reviewed_at = utcnow()
        session.add(
            EvidenceLink(
                claim_id=claim.id,
                passage_id=passage.id,
                stance=EvidenceStance.OPPOSES.value,
                rationale=command.rationale,
                strength=command.strength,
            )
        )
        return claim

    async def _record_decision(self, session: AsyncSession, command: RecordDecision) -> Decision:
        meeting = await session.get(Meeting, command.meeting_id)
        if meeting is None or meeting.project_id != command.project_id:
            raise LookupError("Meeting not found")
        if meeting.status != MeetingStatus.WAITING_USER.value:
            raise ValueError("Meeting is not waiting for a decision")
        meeting.status = MeetingStatus.DECIDED.value
        return Decision(
            project_id=command.project_id,
            meeting_id=command.meeting_id,
            kind=command.kind.value,
            rationale=command.rationale,
            direction=command.direction,
            dissent=command.dissent,
            decided_by=command.actor,
        )

    @staticmethod
    def _require(allowed: bool, actor: str, action: str) -> None:
        if not allowed:
            raise PolicyDeniedError(f"{actor} may not {action}")

    async def get_primary_question(self, project_id: str) -> ResearchQuestion:
        async with self.sessions() as session:
            result = await session.scalar(
                select(ResearchQuestion).where(
                    ResearchQuestion.project_id == project_id,
                    ResearchQuestion.is_primary.is_(True),
                )
            )
            if result is None:
                raise LookupError("Research question not found")
            return result

    async def get_run(self, run_id: str) -> ResearchRun:
        async with self.sessions() as session:
            run = await session.get(ResearchRun, run_id)
            if run is None:
                raise LookupError("Run not found")
            return run

    async def get_meeting_for_run(self, run_id: str) -> Meeting | None:
        async with self.sessions() as session:
            return await session.scalar(
                select(Meeting).where(Meeting.run_id == run_id).order_by(Meeting.created_at.desc())
            )

    async def list_passages_for_source(self, source_id: str) -> list[Passage]:
        async with self.sessions() as session:
            return list(
                await session.scalars(
                    select(Passage).where(Passage.source_id == source_id).order_by(Passage.locator)
                )
            )

    async def list_project_sources(self, project_id: str) -> list[Source]:
        async with self.sessions() as session:
            return list(
                await session.scalars(
                    select(Source)
                    .where(Source.project_id == project_id)
                    .order_by(Source.created_at)
                )
            )

    async def list_project_claims(self, project_id: str) -> list[Claim]:
        async with self.sessions() as session:
            return list(
                await session.scalars(
                    select(Claim).where(Claim.project_id == project_id).order_by(Claim.created_at)
                )
            )

    async def evidence_bundle(self, project_id: str) -> dict:
        async with self.sessions() as session:
            sources = list(
                await session.scalars(
                    select(Source)
                    .where(Source.project_id == project_id)
                    .order_by(Source.created_at)
                )
            )
            source_map = {source.id: source for source in sources}
            passages = list(
                await session.scalars(
                    select(Passage)
                    .join(Source, Source.id == Passage.source_id)
                    .where(Source.project_id == project_id)
                )
            )
            passage_map = {passage.id: passage for passage in passages}
            claims = list(
                await session.scalars(
                    select(Claim).where(Claim.project_id == project_id).order_by(Claim.created_at)
                )
            )
            links = list(
                await session.scalars(
                    select(EvidenceLink)
                    .join(Claim, Claim.id == EvidenceLink.claim_id)
                    .where(Claim.project_id == project_id)
                )
            )
            return {
                "sources": [
                    {
                        "id": source.id,
                        "title": source.title,
                        "uri": source.uri,
                        "doi": source.doi,
                        "authors": source.authors,
                        "published_at": source.published_at,
                        "quality": source.quality,
                    }
                    for source in sources
                ],
                "claims": [
                    {
                        "id": claim.id,
                        "text": claim.canonical_text,
                        "status": claim.status,
                        "confidence": claim.confidence,
                        "owner": claim.owner,
                        "evidence": [
                            {
                                "stance": link.stance,
                                "rationale": link.rationale,
                                "strength": link.strength,
                                "passage_id": link.passage_id,
                                "passage": passage_map[link.passage_id].text,
                                "locator": passage_map[link.passage_id].locator,
                                "source_id": passage_map[link.passage_id].source_id,
                                "source_title": source_map[
                                    passage_map[link.passage_id].source_id
                                ].title,
                            }
                            for link in links
                            if link.claim_id == claim.id and link.passage_id in passage_map
                        ],
                    }
                    for claim in claims
                ],
            }

    async def list_events(self, project_id: str, after: int = 0) -> list[DomainEvent]:
        async with self.sessions() as session:
            return list(
                await session.scalars(
                    select(DomainEvent)
                    .where(
                        DomainEvent.project_id == project_id,
                        DomainEvent.sequence > after,
                    )
                    .order_by(DomainEvent.sequence)
                )
            )

    async def list_recoverable_runs(self) -> list[ResearchRun]:
        async with self.sessions() as session:
            return list(
                await session.scalars(
                    select(ResearchRun).where(ResearchRun.status == RunStatus.RUNNING.value)
                )
            )

    async def project_counts(self, project_id: str) -> dict[str, int]:
        async with self.sessions() as session:
            counts: dict[str, int] = {}
            for key, model in (
                ("tasks", ResearchTask),
                ("sources", Source),
                ("claims", Claim),
                ("meetings", Meeting),
                ("artifacts", Artifact),
            ):
                counts[key] = int(
                    await session.scalar(
                        select(func.count())
                        .select_from(model)
                        .where(model.project_id == project_id)
                    )
                    or 0
                )
            return counts
