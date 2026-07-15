from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from oplab.domain.models import (
    Artifact,
    Claim,
    Decision,
    EvidenceLink,
    Meeting,
    Passage,
    Project,
    ResearchQuestion,
    ResearchRun,
    ResearchTask,
    Source,
)


class QueryService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]):
        self.sessions = sessions

    async def list_projects(self) -> list[dict]:
        async with self.sessions() as session:
            projects = list(
                await session.scalars(select(Project).order_by(Project.created_at.desc()))
            )
            values = []
            for project in projects:
                question = await session.scalar(
                    select(ResearchQuestion).where(
                        ResearchQuestion.project_id == project.id,
                        ResearchQuestion.is_primary.is_(True),
                    )
                )
                latest = await session.scalar(
                    select(ResearchRun)
                    .where(ResearchRun.project_id == project.id)
                    .order_by(ResearchRun.created_at.desc())
                )
                values.append(
                    {
                        "id": project.id,
                        "title": project.title,
                        "question": question.text if question else "",
                        "stage": project.stage,
                        "status": project.status,
                        "created_at": project.created_at,
                        "latest_run": _run(latest) if latest else None,
                    }
                )
            return values

    async def project_dashboard(self, project_id: str) -> dict:
        async with self.sessions() as session:
            project = await session.get(Project, project_id)
            if project is None:
                raise LookupError("Project not found")
            question = await session.scalar(
                select(ResearchQuestion).where(
                    ResearchQuestion.project_id == project_id,
                    ResearchQuestion.is_primary.is_(True),
                )
            )
            tasks = list(
                await session.scalars(
                    select(ResearchTask)
                    .where(ResearchTask.project_id == project_id)
                    .order_by(ResearchTask.created_at)
                )
            )
            sources = list(
                await session.scalars(
                    select(Source)
                    .where(Source.project_id == project_id)
                    .order_by(Source.created_at.desc())
                )
            )
            claims = list(
                await session.scalars(
                    select(Claim)
                    .where(Claim.project_id == project_id)
                    .order_by(Claim.created_at.desc())
                )
            )
            runs = list(
                await session.scalars(
                    select(ResearchRun)
                    .where(ResearchRun.project_id == project_id)
                    .order_by(ResearchRun.created_at.desc())
                )
            )
            artifacts = list(
                await session.scalars(
                    select(Artifact)
                    .where(Artifact.project_id == project_id)
                    .order_by(Artifact.created_at.desc())
                )
            )
            return {
                "project": {
                    "id": project.id,
                    "title": project.title,
                    "stage": project.stage,
                    "status": project.status,
                    "budget": project.budget,
                    "created_at": project.created_at,
                },
                "question": {
                    "text": question.text if question else "",
                    "success_criteria": question.success_criteria if question else [],
                },
                "counts": {
                    "tasks": len(tasks),
                    "sources": len(sources),
                    "claims": len(claims),
                    "contested_claims": sum(claim.status == "contested" for claim in claims),
                    "artifacts": len(artifacts),
                },
                "tasks": [
                    {
                        "id": task.id,
                        "title": task.title,
                        "objective": task.objective,
                        "owner": task.owner,
                        "status": task.status,
                    }
                    for task in tasks
                ],
                "sources": [_source(source) for source in sources],
                "claims": [_claim(claim) for claim in claims],
                "runs": [_run(run) for run in runs],
                "artifacts": [_artifact(artifact) for artifact in artifacts],
            }

    async def run(self, run_id: str) -> dict:
        async with self.sessions() as session:
            value = await session.get(ResearchRun, run_id)
            if value is None:
                raise LookupError("Run not found")
            result = _run(value)
            meeting = await session.scalar(
                select(Meeting).where(Meeting.run_id == run_id).order_by(Meeting.created_at.desc())
            )
            result["meeting"] = _meeting(meeting) if meeting else None
            return result

    async def meeting(self, meeting_id: str) -> dict:
        async with self.sessions() as session:
            value = await session.get(Meeting, meeting_id)
            if value is None:
                raise LookupError("Meeting not found")
            result = _meeting(value)
            decision = await session.scalar(
                select(Decision).where(Decision.meeting_id == meeting_id)
            )
            result["decision"] = _decision(decision) if decision else None
            return result

    async def artifact(self, artifact_id: str) -> Artifact:
        async with self.sessions() as session:
            value = await session.get(Artifact, artifact_id)
            if value is None:
                raise LookupError("Artifact not found")
            return value

    async def evidence(self, project_id: str) -> dict:
        async with self.sessions() as session:
            sources = list(
                await session.scalars(
                    select(Source)
                    .where(Source.project_id == project_id)
                    .order_by(Source.created_at)
                )
            )
            passages = list(
                await session.scalars(
                    select(Passage)
                    .join(Source, Source.id == Passage.source_id)
                    .where(Source.project_id == project_id)
                    .order_by(Passage.created_at)
                )
            )
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
                "sources": [_source(source) for source in sources],
                "passages": [
                    {
                        "id": passage.id,
                        "source_id": passage.source_id,
                        "locator": passage.locator,
                        "text": passage.text,
                    }
                    for passage in passages
                ],
                "claims": [_claim(claim) for claim in claims],
                "links": [
                    {
                        "id": link.id,
                        "claim_id": link.claim_id,
                        "passage_id": link.passage_id,
                        "stance": link.stance,
                        "rationale": link.rationale,
                        "strength": link.strength,
                    }
                    for link in links
                ],
            }


def _run(run: ResearchRun) -> dict:
    return {
        "id": run.id,
        "project_id": run.project_id,
        "thread_id": run.thread_id,
        "trace_id": run.trace_id,
        "status": run.status,
        "current_phase": run.current_phase,
        "stop_reason": run.stop_reason,
        "state": run.state,
        "error": run.error,
        "report_artifact_id": run.report_artifact_id,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
    }


def _source(source: Source) -> dict:
    return {
        "id": source.id,
        "source_type": source.source_type,
        "title": source.title,
        "uri": source.uri,
        "doi": source.doi,
        "authors": source.authors,
        "published_at": source.published_at,
        "quality": source.quality,
        "license_status": source.license_status,
        "created_at": source.created_at,
    }


def _claim(claim: Claim) -> dict:
    return {
        "id": claim.id,
        "canonical_text": claim.canonical_text,
        "status": claim.status,
        "confidence": claim.confidence,
        "owner": claim.owner,
        "last_reviewed_at": claim.last_reviewed_at,
    }


def _meeting(meeting: Meeting) -> dict:
    return {
        "id": meeting.id,
        "project_id": meeting.project_id,
        "run_id": meeting.run_id,
        "status": meeting.status,
        "trigger": meeting.trigger,
        "agenda": meeting.agenda,
        "evidence_packet": meeting.evidence_packet,
        "position_cards": meeting.position_cards,
        "created_at": meeting.created_at,
    }


def _decision(decision: Decision) -> dict:
    return {
        "id": decision.id,
        "kind": decision.kind,
        "rationale": decision.rationale,
        "direction": decision.direction,
        "dissent": decision.dissent,
        "decided_by": decision.decided_by,
        "created_at": decision.created_at,
    }


def _artifact(artifact: Artifact) -> dict:
    return {
        "id": artifact.id,
        "run_id": artifact.run_id,
        "kind": artifact.kind,
        "title": artifact.title,
        "media_type": artifact.media_type,
        "content_hash": artifact.content_hash,
        "provenance": artifact.provenance,
        "created_at": artifact.created_at,
    }
