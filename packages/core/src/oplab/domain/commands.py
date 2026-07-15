from typing import Literal

from pydantic import BaseModel, Field

from oplab.domain.enums import MeetingDecisionKind


class AuthorityEnvelope(BaseModel):
    actor: str
    may_create_task: bool = False
    may_add_evidence: bool = False
    may_propose_claim: bool = False
    may_request_meeting: bool = False
    may_publish_artifact: bool = False


class Command(BaseModel):
    project_id: str
    idempotency_key: str = Field(min_length=12, max_length=180)
    actor: str


class CreateTask(Command):
    command_type: Literal["CreateTask"] = "CreateTask"
    title: str
    objective: str
    owner: str
    success_criteria: list[str] = Field(default_factory=list)
    evidence_requirements: list[str] = Field(default_factory=list)


class AddEvidence(Command):
    command_type: Literal["AddEvidence"] = "AddEvidence"
    source_type: str
    title: str
    uri: str
    doi: str | None = None
    authors: list[str] = Field(default_factory=list)
    published_at: str | None = None
    content_hash: str
    quality: dict = Field(default_factory=dict)
    license_status: str = "metadata_only"
    passages: list[dict]


class ProposeClaim(Command):
    command_type: Literal["ProposeClaim"] = "ProposeClaim"
    canonical_text: str
    scope: str = "project"
    confidence: float = Field(ge=0.0, le=1.0)
    passage_id: str
    rationale: str
    strength: float = Field(default=0.5, ge=0.0, le=1.0)


class ContestClaim(Command):
    command_type: Literal["ContestClaim"] = "ContestClaim"
    claim_id: str
    passage_id: str
    rationale: str
    strength: float = Field(default=0.5, ge=0.0, le=1.0)


class RequestMeeting(Command):
    command_type: Literal["RequestMeeting"] = "RequestMeeting"
    run_id: str
    trigger: str
    agenda: list[str]
    evidence_packet: dict
    position_cards: list[dict]


class RecordDecision(Command):
    command_type: Literal["RecordDecision"] = "RecordDecision"
    meeting_id: str
    kind: MeetingDecisionKind
    rationale: str
    direction: str | None = None
    dissent: list[str] = Field(default_factory=list)


class PublishArtifact(Command):
    command_type: Literal["PublishArtifact"] = "PublishArtifact"
    run_id: str
    kind: str
    title: str
    path: str
    media_type: str
    content_hash: str
    provenance: dict


def make_idempotency_key(
    project_id: str,
    run_id: str,
    action_type: str,
    input_hash: str,
    generation: int = 0,
) -> str:
    return f"{project_id}:{run_id}:{action_type}:{input_hash[:24]}:{generation}"
