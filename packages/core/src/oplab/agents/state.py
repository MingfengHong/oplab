from typing import Any, TypedDict


class ResearchState(TypedDict, total=False):
    project_id: str
    run_id: str
    thread_id: str
    trace_id: str
    question: str
    success_criteria: list[str]
    current_phase: str
    generation: int
    charter: dict[str, Any]
    source_ids: list[str]
    claim_ids: list[str]
    revision_direction: str | None
    meeting_id: str
    decision: dict[str, Any]
    report_artifact_id: str
    stop_reason: str
    plan: dict[str, Any]
    budget: dict[str, int]
    usage: dict[str, int]
    quality_gate: dict[str, Any]
    source_intents: dict[str, str]
    processed_source_ids: list[str]
    challenged_source_ids: list[str]
    evaluation: dict[str, Any]
    pending_action: dict[str, Any]
    trajectory: list[dict[str, Any]]
    draft: str
    review: dict[str, Any]
    controller_source: str
