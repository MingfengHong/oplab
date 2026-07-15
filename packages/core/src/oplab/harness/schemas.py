from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class PlanObjective(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=3, max_length=180)
    description: str = Field(min_length=3, max_length=1_000)
    kind: Literal["discover", "extract", "challenge", "synthesize"]
    depends_on: list[str] = Field(default_factory=list, max_length=10)
    success_criteria: list[str] = Field(default_factory=list, max_length=10)
    status: Literal["todo", "in_progress", "done", "blocked"] = "todo"
    task_id: str | None = None

    @field_validator("depends_on", "success_criteria", mode="before")
    @classmethod
    def coerce_text_lists(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [part.strip() for part in value.replace("；", ";").split(";") if part.strip()]
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return []


class ResearchPlan(BaseModel):
    research_question: str = Field(min_length=5, max_length=4_000)
    scope: str = Field(min_length=3, max_length=2_000)
    success_criteria: list[str] = Field(min_length=1, max_length=20)
    evidence_requirements: list[str] = Field(min_length=1, max_length=20)
    objectives: list[PlanObjective] = Field(min_length=3, max_length=8)

    @field_validator("success_criteria", "evidence_requirements", mode="before")
    @classmethod
    def coerce_text_lists(cls, value: object) -> list[str]:
        return PlanObjective.coerce_text_lists(value)


HarnessActionName = Literal[
    "search_literature",
    "extract_claims",
    "challenge_claims",
    "inspect_evidence",
    "request_review",
    "request_user",
    "stop",
]


class HarnessAction(BaseModel):
    action: HarnessActionName
    objective_id: str | None = Field(default=None, max_length=80)
    rationale: str = Field(min_length=3, max_length=2_000)
    expected_outcome: str = Field(min_length=3, max_length=1_000)
    query: str | None = Field(default=None, max_length=500)
    intent: Literal["support", "counter", "broaden"] | None = None


class ToolOutcome(BaseModel):
    action: HarnessActionName
    status: Literal["ok", "no_result", "denied", "failed"]
    summary: str
    objective_id: str | None = None
    query: str | None = None
    created_source_ids: list[str] = Field(default_factory=list)
    created_claim_ids: list[str] = Field(default_factory=list)
    contested_claim_ids: list[str] = Field(default_factory=list)
    observations: list[str] = Field(default_factory=list)


class HarnessEvaluation(BaseModel):
    ready_for_review: bool
    coverage_score: float = Field(ge=0.0, le=1.0)
    metrics: dict[str, int] = Field(default_factory=dict)
    gaps: list[str] = Field(default_factory=list)
    next_recommendation: str


class ClaimExtraction(BaseModel):
    source_id: str
    passage_id: str
    claim: str = Field(min_length=20, max_length=2_000)
    confidence: float = Field(default=0.55, ge=0.0, le=1.0)
    rationale: str = Field(min_length=3, max_length=1_000)


class ClaimExtractionBatch(BaseModel):
    items: list[ClaimExtraction] = Field(default_factory=list, max_length=20)


class ChallengeLink(BaseModel):
    claim_id: str
    passage_id: str
    is_material_challenge: bool
    rationale: str = Field(min_length=3, max_length=1_000)
    strength: float = Field(default=0.45, ge=0.0, le=1.0)


class ChallengeBatch(BaseModel):
    items: list[ChallengeLink] = Field(default_factory=list, max_length=30)


class ReviewVerdict(BaseModel):
    decision: Literal["accept", "revise", "request_user"]
    summary: str = Field(min_length=3, max_length=2_000)
    findings: list[str] = Field(default_factory=list, max_length=20)
    required_actions: list[str] = Field(default_factory=list, max_length=20)
    citation_traceable: bool
    evidence_sufficient: bool
