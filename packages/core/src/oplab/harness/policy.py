from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from oplab.harness.schemas import (
    HarnessAction,
    HarnessEvaluation,
    PlanObjective,
    ResearchPlan,
    ReviewVerdict,
)


def fallback_plan(question: str, success_criteria: list[str]) -> ResearchPlan:
    criteria = success_criteria or [
        "At least two evidence-bound claims",
        "Explicitly search for limitations or counterevidence",
        "Every published claim has a traceable source passage",
    ]
    return ResearchPlan(
        research_question=question,
        scope="Evidence-first literature investigation with explicit uncertainty and provenance.",
        success_criteria=criteria,
        evidence_requirements=[
            "Prefer primary scholarly records",
            "Bind claims to source passages",
            "Preserve material counterevidence",
        ],
        objectives=[
            PlanObjective(
                id="landscape",
                title="Map the literature landscape",
                description="Retrieve a diverse first evidence set for the research question.",
                kind="discover",
                success_criteria=["At least three relevant sources"],
            ),
            PlanObjective(
                id="claims",
                title="Extract traceable claims",
                description="Create claims only from stored source passages.",
                kind="extract",
                depends_on=["landscape"],
                success_criteria=["At least two passage-bound claims"],
            ),
            PlanObjective(
                id="challenge",
                title="Challenge the emerging account",
                description=(
                    "Search for boundary conditions, null results, and contradictory evidence."
                ),
                kind="challenge",
                depends_on=["claims"],
                success_criteria=["Counterevidence search completed"],
            ),
            PlanObjective(
                id="synthesis",
                title="Produce an auditable synthesis",
                description="Review evidence sufficiency before publishing a cited memo.",
                kind="synthesize",
                depends_on=["claims", "challenge"],
                success_criteria=["Reviewer gate accepts the draft"],
            ),
        ],
    )


def fallback_action(state: dict[str, Any], evaluation: HarnessEvaluation) -> HarnessAction:
    usage = state.get("usage", {})
    budget = state.get("budget", {})
    source_ids = state.get("source_ids", [])
    processed = set(state.get("processed_source_ids", []))
    source_intents = state.get("source_intents", {})
    counter_sources = [sid for sid in source_ids if source_intents.get(sid) == "counter"]
    challenged = set(state.get("challenged_source_ids", []))
    query = " ".join(
        item for item in [state.get("question", ""), state.get("revision_direction", "")] if item
    )

    if not source_ids:
        return HarnessAction(
            action="search_literature",
            objective_id="landscape",
            query=query,
            intent="support",
            rationale="No evidence has been retrieved yet.",
            expected_outcome="A diverse initial scholarly source set.",
        )
    unprocessed = [sid for sid in source_ids if sid not in processed and sid not in counter_sources]
    if unprocessed:
        return HarnessAction(
            action="extract_claims",
            objective_id="claims",
            rationale="New sources do not yet have passage-bound claims.",
            expected_outcome="Traceable claims extracted from stored passages.",
        )
    counter_searched = any(
        item.get("action") == "search_literature" and item.get("intent") == "counter"
        for item in state.get("trajectory", [])
    )
    if not counter_searched and usage.get("searches", 0) < budget.get("max_searches", 4):
        return HarnessAction(
            action="search_literature",
            objective_id="challenge",
            query=f"{query} limitations boundary conditions null results contradictory evidence",
            intent="counter",
            rationale="The current account has not been tested against counterevidence.",
            expected_outcome="Studies exposing limitations, null results, or boundary conditions.",
        )
    if any(sid not in challenged for sid in counter_sources):
        return HarnessAction(
            action="challenge_claims",
            objective_id="challenge",
            rationale="Counterevidence sources have not been compared with current claims.",
            expected_outcome="Material opposing links or an explicit no-challenge finding.",
        )
    if evaluation.ready_for_review:
        return HarnessAction(
            action="request_review",
            objective_id="synthesis",
            rationale="Configured evidence gates are satisfied.",
            expected_outcome="Human approval to draft an evidence-bound synthesis.",
        )
    if usage.get("searches", 0) < budget.get("max_searches", 4):
        gaps = " ".join(evaluation.gaps)
        return HarnessAction(
            action="search_literature",
            objective_id="landscape",
            query=f"{query} {gaps}",
            intent="broaden",
            rationale="Evidence gates remain open and the search budget is available.",
            expected_outcome="Sources that close the recorded evidence gaps.",
        )
    return HarnessAction(
        action="request_user",
        rationale="The evidence gates remain open but the autonomous search budget is exhausted.",
        expected_outcome="A user decision to revise scope, add evidence, or stop.",
    )


def evaluate_state(state: dict[str, Any], bundle: dict[str, Any]) -> HarnessEvaluation:
    sources = len(bundle.get("sources", []))
    claims = len(bundle.get("claims", []))
    contested = sum(item.get("status") == "contested" for item in bundle.get("claims", []))
    links = sum(len(item.get("evidence", [])) for item in bundle.get("claims", []))
    requirements = state.get("quality_gate", {})
    min_sources = int(requirements.get("min_sources", 3))
    min_claims = int(requirements.get("min_claims", 2))
    require_counter = bool(requirements.get("require_counter_search", True))
    counter_searched = any(
        item.get("action") == "search_literature" and item.get("intent") == "counter"
        for item in state.get("trajectory", [])
    )
    counter_sources = {
        source_id
        for source_id, intent in state.get("source_intents", {}).items()
        if intent == "counter"
    }
    challenged_sources = set(state.get("challenged_source_ids", []))
    challenge_complete = counter_searched and counter_sources <= challenged_sources
    gaps: list[str] = []
    if sources < min_sources:
        gaps.append(f"Need {min_sources - sources} more relevant sources")
    if claims < min_claims:
        gaps.append(f"Need {min_claims - claims} more passage-bound claims")
    if require_counter and not counter_searched:
        gaps.append("Counterevidence search has not been completed")
    elif require_counter and counter_sources and not challenge_complete:
        gaps.append("Retrieved counterevidence has not been compared with current claims")
    if claims and links < claims:
        gaps.append("Some claims lack evidence links")
    scores = [
        min(1.0, sources / max(1, min_sources)),
        min(1.0, claims / max(1, min_claims)),
        1.0 if not require_counter or challenge_complete else 0.0,
        min(1.0, links / max(1, claims)),
    ]
    return HarnessEvaluation(
        ready_for_review=not gaps,
        coverage_score=sum(scores) / len(scores),
        metrics={"sources": sources, "claims": claims, "contested": contested, "links": links},
        gaps=gaps,
        next_recommendation="Request review" if not gaps else gaps[0],
    )


def fallback_review(memo: str, allowed_labels: set[str], bundle: dict[str, Any]) -> ReviewVerdict:
    used = set(re.findall(r"\[(S\d+)\]", memo))
    traceable = bool(used) and used <= allowed_labels
    sufficient = bool(bundle.get("sources")) and bool(bundle.get("claims"))
    findings: list[str] = []
    if not traceable:
        findings.append("Draft citations are missing or reference unknown source labels.")
    if not sufficient:
        findings.append("The evidence bundle is empty or lacks passage-bound claims.")
    return ReviewVerdict(
        decision="accept" if traceable and sufficient else "revise",
        summary="Draft passed deterministic provenance checks."
        if traceable and sufficient
        else "Draft failed the evidence or citation gate.",
        findings=findings,
        required_actions=findings,
        citation_traceable=traceable,
        evidence_sufficient=sufficient,
    )


def action_fingerprint(action: HarnessAction) -> str:
    payload = action.model_dump(exclude={"rationale", "expected_outcome"})
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def normalize_query(value: str) -> str:
    text = re.sub(r'["“”‘’()]', " ", value)
    text = re.sub(r"\b(?:AND|OR|NOT)\b", " ", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip()[:500]
