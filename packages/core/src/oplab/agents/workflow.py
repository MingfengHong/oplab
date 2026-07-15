from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Literal

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command as GraphCommand
from langgraph.types import interrupt

from oplab.agents.state import ResearchState
from oplab.config import Settings
from oplab.domain.commands import CreateTask, PublishArtifact, RequestMeeting, make_idempotency_key
from oplab.domain.enums import ResearchPhase, RunStatus, StopReason, TaskStatus
from oplab.domain.service import DomainService
from oplab.harness.controller import HarnessController
from oplab.harness.model import ModelGateway, ModelUnavailableError
from oplab.harness.policy import action_fingerprint, evaluate_state
from oplab.harness.schemas import HarnessAction
from oplab.harness.tools import HarnessToolRegistry
from oplab.retrieval.base import SearchAdapter

EXECUTABLE_ACTIONS = {
    "search_literature",
    "extract_claims",
    "challenge_claims",
    "inspect_evidence",
}


class ResearchWorkflow:
    """Durable shell around a typed, model-directed research harness loop."""

    def __init__(
        self,
        *,
        settings: Settings,
        domain: DomainService,
        search: SearchAdapter,
        model: ModelGateway,
    ):
        self.settings = settings
        self.domain = domain
        self.model = model
        self.controller = HarnessController(model)
        self.tools = HarnessToolRegistry(
            domain=domain,
            search=search,
            model=model,
            retrieval_limit=settings.retrieval_limit,
        )
        self._saver_context: Any = None
        self._saver: AsyncSqliteSaver | None = None
        self.graph: Any = None

    async def start(self) -> None:
        self._saver_context = AsyncSqliteSaver.from_conn_string(
            self.settings.checkpoint_database_url
        )
        self._saver = await self._saver_context.__aenter__()
        builder = StateGraph(ResearchState)
        builder.add_node("bootstrap", self._bootstrap)
        builder.add_node("controller", self._controller)
        builder.add_node("tool", self._tool)
        builder.add_node("evaluator", self._evaluator)
        builder.add_node("meeting", self._meeting)
        builder.add_node("draft", self._draft)
        builder.add_node("reviewer", self._reviewer)
        builder.add_node("publish", self._publish)
        builder.add_node("stop", self._stop)
        builder.add_edge(START, "bootstrap")
        builder.add_edge("bootstrap", "controller")
        builder.add_conditional_edges(
            "controller",
            self._route_controller,
            {"tool": "tool", "meeting": "meeting", "stop": "stop"},
        )
        builder.add_edge("tool", "evaluator")
        builder.add_edge("evaluator", "controller")
        builder.add_conditional_edges(
            "meeting",
            self._route_meeting,
            {"draft": "draft", "controller": "controller", "stop": "stop"},
        )
        builder.add_edge("draft", "reviewer")
        builder.add_conditional_edges(
            "reviewer",
            self._route_review,
            {"publish": "publish", "draft": "draft", "meeting": "meeting"},
        )
        builder.add_edge("publish", END)
        builder.add_edge("stop", END)
        self.graph = builder.compile(checkpointer=self._saver)

    async def close(self) -> None:
        if self._saver_context is not None:
            await self._saver_context.__aexit__(None, None, None)

    async def initial_state(self, run_id: str) -> ResearchState:
        run = await self.domain.get_run(run_id)
        question = await self.domain.get_primary_question(run.project_id)
        return ResearchState(
            project_id=run.project_id,
            run_id=run.id,
            thread_id=run.thread_id,
            trace_id=run.trace_id,
            question=question.text,
            success_criteria=question.success_criteria,
            current_phase=ResearchPhase.PLAN.value,
            generation=0,
            source_ids=[],
            claim_ids=[],
            processed_source_ids=[],
            challenged_source_ids=[],
            source_intents={},
            trajectory=[],
            budget={
                "max_iterations": self.settings.harness_max_iterations,
                "max_searches": self.settings.harness_max_searches,
                "max_sources": self.settings.harness_max_sources,
            },
            usage={"iterations": 0, "searches": 0, "review_attempts": 0},
            quality_gate={
                "min_sources": self.settings.harness_min_sources,
                "min_claims": self.settings.harness_min_claims,
                "require_counter_search": True,
            },
        )

    async def invoke(self, run_id: str, resume: dict | None = None) -> dict:
        if self.graph is None:
            raise RuntimeError("Research workflow has not started")
        run = await self.domain.get_run(run_id)
        config = {"configurable": {"thread_id": run.thread_id}, "recursion_limit": 100}
        if resume is not None:
            return await self.graph.ainvoke(GraphCommand(resume=resume), config=config)
        snapshot = await self.graph.aget_state(config)
        if snapshot.values:
            return await self.graph.ainvoke(None, config=config)
        return await self.graph.ainvoke(await self.initial_state(run_id), config=config)

    async def _bootstrap(self, state: ResearchState) -> dict:
        plan, source = await self.controller.create_plan(
            state["question"], state.get("success_criteria", [])
        )
        plan_data = plan.model_dump()
        for objective in plan_data["objectives"]:
            digest = hashlib.sha256(json.dumps(objective, sort_keys=True).encode()).hexdigest()
            result = await self.domain.execute(
                CreateTask(
                    project_id=state["project_id"],
                    idempotency_key=make_idempotency_key(
                        state["project_id"], state["run_id"], "CreateTask", digest
                    ),
                    actor="pi",
                    title=objective["title"],
                    objective=objective["description"],
                    owner=_owner_for_kind(objective["kind"]),
                    success_criteria=objective["success_criteria"],
                    evidence_requirements=plan_data["evidence_requirements"],
                )
            )
            objective["task_id"] = result.entity_id
        update = {
            "plan": plan_data,
            "controller_source": source,
            "current_phase": ResearchPhase.DECIDE.value,
        }
        await self.domain.record_harness_event(
            project_id=state["project_id"],
            run_id=state["run_id"],
            event_type="HarnessPlanCreated",
            payload={"source": source, "plan": plan_data},
        )
        await self._persist_progress(state, update, ResearchPhase.DECIDE)
        return update

    async def _controller(self, state: ResearchState) -> dict:
        usage = dict(state.get("usage", {}))
        usage["iterations"] = int(usage.get("iterations", 0)) + 1
        state_with_usage = {**state, "usage": usage}
        evaluation = evaluate_state(
            state_with_usage, await self.domain.evidence_bundle(state["project_id"])
        )
        if usage["iterations"] > int(state.get("budget", {}).get("max_iterations", 10)):
            action = HarnessAction(
                action="request_user",
                rationale="The autonomous iteration budget has been exhausted.",
                expected_outcome="User decides whether to revise scope, add evidence, or stop.",
            )
            source = "budget_guard"
        else:
            action, source = await self.controller.choose_action(state_with_usage, evaluation)
        action_data = action.model_dump()
        trace = {
            "kind": "decision",
            **action_data,
            "fingerprint": action_fingerprint(action),
            "iteration": usage["iterations"],
            "decision_source": source,
        }
        trajectory = [*state.get("trajectory", []), trace]
        phase = (
            ResearchPhase.EXECUTE
            if action.action in EXECUTABLE_ACTIONS
            else ResearchPhase.MEETING
            if action.action in {"request_review", "request_user"}
            else ResearchPhase.COMPLETE
        )
        update = {
            "usage": usage,
            "evaluation": evaluation.model_dump(),
            "pending_action": action_data,
            "controller_source": source,
            "trajectory": trajectory,
            "current_phase": phase.value,
        }
        await self._set_objective_status(state, action.objective_id, TaskStatus.IN_PROGRESS)
        await self.domain.record_harness_event(
            project_id=state["project_id"],
            run_id=state["run_id"],
            event_type="HarnessDecisionMade",
            payload=trace,
        )
        await self._persist_progress(state, update, phase)
        return update

    @staticmethod
    def _route_controller(state: ResearchState) -> Literal["tool", "meeting", "stop"]:
        action = state.get("pending_action", {}).get("action")
        if action in EXECUTABLE_ACTIONS:
            return "tool"
        if action in {"request_review", "request_user"}:
            return "meeting"
        return "stop"

    async def _tool(self, state: ResearchState) -> dict:
        action = HarnessAction.model_validate(state["pending_action"])
        values, outcome = await self.tools.execute(state, action)
        trace = {
            "kind": "tool_result",
            **outcome.model_dump(),
            "intent": action.intent,
            "fingerprint": action_fingerprint(action),
            "iteration": state.get("usage", {}).get("iterations", 0),
        }
        update = {
            **values,
            "trajectory": [*state.get("trajectory", []), trace],
            "current_phase": ResearchPhase.EVALUATE.value,
        }
        await self.domain.record_harness_event(
            project_id=state["project_id"],
            run_id=state["run_id"],
            event_type="HarnessToolCompleted",
            payload=trace,
        )
        await self._persist_progress(state, update, ResearchPhase.EVALUATE)
        return update

    async def _evaluator(self, state: ResearchState) -> dict:
        bundle = await self.domain.evidence_bundle(state["project_id"])
        evaluation = evaluate_state(state, bundle)
        plan = _update_plan_progress(state.get("plan", {}), state, evaluation.model_dump())
        await self._sync_plan_tasks(plan)
        trace = {
            "kind": "evaluation",
            **evaluation.model_dump(),
            "iteration": state.get("usage", {}).get("iterations", 0),
        }
        update = {
            "evaluation": evaluation.model_dump(),
            "plan": plan,
            "trajectory": [*state.get("trajectory", []), trace],
            "current_phase": ResearchPhase.DECIDE.value,
        }
        await self.domain.record_harness_event(
            project_id=state["project_id"],
            run_id=state["run_id"],
            event_type="HarnessEvaluationCompleted",
            payload=trace,
        )
        await self._persist_progress(state, update, ResearchPhase.DECIDE)
        return update

    async def _meeting(self, state: ResearchState) -> dict:
        generation = int(state.get("generation", 0))
        bundle = await self.domain.evidence_bundle(state["project_id"])
        evaluation = evaluate_state(state, bundle)
        action = state.get("pending_action", {})
        supported = sum(claim["status"] == "supported" for claim in bundle["claims"])
        contested = sum(claim["status"] == "contested" for claim in bundle["claims"])
        positions = [
            {
                "agent": "Harness Controller",
                "recommendation": "continue" if evaluation.ready_for_review else "revise",
                "confidence": round(evaluation.coverage_score, 2),
                "reason": evaluation.next_recommendation,
            },
            {
                "agent": "Evidence Auditor",
                "recommendation": "continue" if bundle["claims"] else "revise",
                "confidence": min(0.95, len(bundle["sources"]) / 10),
                "reason": f"{len(bundle['sources'])} sources, {len(bundle['claims'])} claims.",
            },
            {
                "agent": "Skeptic",
                "recommendation": "continue"
                if contested or evaluation.ready_for_review
                else "revise",
                "confidence": 0.65,
                "reason": f"{contested} claims retain material counterevidence links.",
            },
            {
                "agent": "Independent Reviewer",
                "recommendation": "continue" if supported + contested else "revise",
                "confidence": 0.6,
                "reason": (
                    "A draft will still face citation and evidence review before publication."
                ),
            },
        ]
        packet = {
            "source_count": len(bundle["sources"]),
            "claim_count": len(bundle["claims"]),
            "supported_count": supported,
            "contested_count": contested,
            "coverage_score": evaluation.coverage_score,
            "unresolved_questions": evaluation.gaps,
            "trigger_action": action.get("action", "request_user"),
            "trigger_reason": action.get("rationale", "Human review required."),
        }
        digest = hashlib.sha256(json.dumps(packet, sort_keys=True).encode()).hexdigest()
        result = await self.domain.execute(
            RequestMeeting(
                project_id=state["project_id"],
                idempotency_key=make_idempotency_key(
                    state["project_id"], state["run_id"], "RequestMeeting", digest, generation
                ),
                actor="pi",
                run_id=state["run_id"],
                trigger=packet["trigger_reason"],
                agenda=[
                    "Does the trace justify the next step?",
                    "Which evidence gaps or dissent remain material?",
                    "Continue to a reviewed draft, revise the investigation, or stop?",
                ],
                evidence_packet=packet,
                position_cards=positions,
            )
        )
        waiting_state = {
            **state,
            "meeting_id": result.entity_id,
            "current_phase": ResearchPhase.MEETING.value,
        }
        await self.domain.set_run_state(
            state["run_id"],
            status=RunStatus.NEEDS_USER,
            phase=ResearchPhase.MEETING,
            state=_json_state(waiting_state),
            stop_reason=StopReason.NEEDS_USER,
        )
        decision = interrupt(
            {
                "type": "meeting_decision_required",
                "meeting_id": result.entity_id,
                "evidence_packet": packet,
                "position_cards": positions,
                "allowed_decisions": ["continue", "revise", "stop"],
            }
        )
        kind = str(decision.get("kind", "stop"))
        update: dict[str, Any] = {
            "meeting_id": result.entity_id,
            "decision": decision,
            "current_phase": ResearchPhase.WRITER.value
            if kind == "continue"
            else ResearchPhase.DECIDE.value
            if kind == "revise"
            else ResearchPhase.COMPLETE.value,
        }
        if kind == "revise":
            update["generation"] = generation + 1
            update["revision_direction"] = decision.get("direction")
        await self.domain.record_harness_event(
            project_id=state["project_id"],
            run_id=state["run_id"],
            event_type="HarnessHumanDecisionApplied",
            payload={"meeting_id": result.entity_id, **decision},
        )
        if kind != "stop":
            await self._persist_progress(
                state,
                update,
                ResearchPhase.WRITER if kind == "continue" else ResearchPhase.DECIDE,
            )
        return update

    @staticmethod
    def _route_meeting(state: ResearchState) -> Literal["draft", "controller", "stop"]:
        kind = state.get("decision", {}).get("kind", "stop")
        if kind == "continue":
            return "draft"
        if kind == "revise":
            return "controller"
        return "stop"

    async def _draft(self, state: ResearchState) -> dict:
        bundle = await self.domain.evidence_bundle(state["project_id"])
        if not bundle["sources"] or not bundle["claims"]:
            raise RuntimeError(
                "Evidence gate denied synthesis: at least one source and one "
                "bound claim are required"
            )
        source_labels = {
            source["id"]: f"S{index}" for index, source in enumerate(bundle["sources"], 1)
        }
        fallback = self._deterministic_memo(state, bundle, source_labels)
        try:
            body = await self.model.complete_text(
                system=(
                    "You are a cautious research synthesizer. Use only supplied evidence. Every "
                    "substantive claim must cite supplied labels like [S1]. Preserve uncertainty, "
                    "counterevidence, and unresolved questions. Never invent a citation label."
                ),
                prompt=json.dumps(
                    {
                        "question": state["question"],
                        "plan": state.get("plan", {}),
                        "decision": state.get("decision", {}),
                        "previous_review": state.get("review", {}),
                        "evidence": bundle,
                        "source_labels": source_labels,
                    },
                    ensure_ascii=False,
                    default=str,
                ),
                fallback=fallback,
            )
            memo = body if _citations_are_valid(body, set(source_labels.values())) else fallback
        except ModelUnavailableError:
            memo = fallback
        memo = _append_sources_if_missing(memo, bundle["sources"], source_labels)
        update = {"draft": memo, "current_phase": ResearchPhase.REVIEW.value}
        await self.domain.record_harness_event(
            project_id=state["project_id"],
            run_id=state["run_id"],
            event_type="HarnessDraftGenerated",
            payload={"characters": len(memo), "source_labels": list(source_labels.values())},
        )
        await self._persist_progress(state, update, ResearchPhase.REVIEW)
        return update

    async def _reviewer(self, state: ResearchState) -> dict:
        bundle = await self.domain.evidence_bundle(state["project_id"])
        source_labels = {
            source["id"]: f"S{index}" for index, source in enumerate(bundle["sources"], 1)
        }
        verdict, source = await self.controller.review_draft(
            memo=state["draft"], bundle=bundle, source_labels=source_labels
        )
        usage = dict(state.get("usage", {}))
        usage["review_attempts"] = int(usage.get("review_attempts", 0)) + 1
        review = {**verdict.model_dump(), "review_source": source}
        if verdict.decision == "revise" and usage["review_attempts"] >= 3:
            review["decision"] = "request_user"
            review["summary"] = "The draft failed review three times; human guidance is required."
        phase = ResearchPhase.PUBLISH if review["decision"] == "accept" else ResearchPhase.REVIEW
        update = {
            "usage": usage,
            "review": review,
            "current_phase": phase.value,
            "pending_action": {
                "action": "request_user",
                "rationale": review["summary"],
                "expected_outcome": "Human guidance resolves repeated reviewer findings.",
            }
            if review["decision"] == "request_user"
            else state.get("pending_action", {}),
        }
        await self.domain.record_harness_event(
            project_id=state["project_id"],
            run_id=state["run_id"],
            event_type="HarnessDraftReviewed",
            payload=review,
        )
        await self._persist_progress(state, update, phase)
        return update

    @staticmethod
    def _route_review(state: ResearchState) -> Literal["publish", "draft", "meeting"]:
        decision = state.get("review", {}).get("decision", "request_user")
        if decision == "accept":
            return "publish"
        if decision == "revise":
            return "draft"
        return "meeting"

    async def _publish(self, state: ResearchState) -> dict:
        memo = state["draft"]
        bundle = await self.domain.evidence_bundle(state["project_id"])
        digest = hashlib.sha256(memo.encode("utf-8")).hexdigest()
        directory = self.settings.oplab_artifact_root / state["project_id"] / state["run_id"]
        directory.mkdir(parents=True, exist_ok=True)
        final_path = directory / "research-memo.md"
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=directory, delete=False, suffix=".tmp"
        ) as handle:
            handle.write(memo)
            temporary_path = Path(handle.name)
        os.replace(temporary_path, final_path)
        artifact = await self.domain.execute(
            PublishArtifact(
                project_id=state["project_id"],
                idempotency_key=make_idempotency_key(
                    state["project_id"], state["run_id"], "PublishArtifact", digest
                ),
                actor="writer",
                run_id=state["run_id"],
                kind="research_memo",
                title="Reviewed research memo",
                path=str(final_path.resolve()),
                media_type="text/markdown",
                content_hash=digest,
                provenance={
                    "trace_id": state["trace_id"],
                    "thread_id": state["thread_id"],
                    "source_ids": [source["id"] for source in bundle["sources"]],
                    "claim_ids": [claim["id"] for claim in bundle["claims"]],
                    "model": self.settings.openai_model if self.model.enabled else "deterministic",
                    "review": state.get("review", {}),
                    "harness_trajectory_entries": len(state.get("trajectory", [])),
                },
            )
        )
        plan = _mark_synthesis_done(state.get("plan", {}))
        await self._sync_plan_tasks(plan)
        final_state = {
            **state,
            "plan": plan,
            "report_artifact_id": artifact.entity_id,
            "current_phase": ResearchPhase.COMPLETE.value,
            "stop_reason": StopReason.SUCCESS.value,
        }
        await self.domain.set_run_state(
            state["run_id"],
            status=RunStatus.COMPLETED,
            phase=ResearchPhase.COMPLETE,
            state=_json_state(final_state),
            stop_reason=StopReason.SUCCESS,
            report_artifact_id=artifact.entity_id,
        )
        await self.domain.record_harness_event(
            project_id=state["project_id"],
            run_id=state["run_id"],
            event_type="HarnessRunCompleted",
            payload={"artifact_id": artifact.entity_id, "content_hash": digest},
        )
        return final_state

    async def _stop(self, state: ResearchState) -> dict:
        final_state = {
            **state,
            "current_phase": ResearchPhase.COMPLETE.value,
            "stop_reason": StopReason.NO_PROGRESS.value,
        }
        await self.domain.set_run_state(
            state["run_id"],
            status=RunStatus.CANCELLED,
            phase=ResearchPhase.COMPLETE,
            state=_json_state(final_state),
            stop_reason=StopReason.NO_PROGRESS,
        )
        return final_state

    async def _persist_progress(
        self, state: ResearchState, update: dict[str, Any], phase: ResearchPhase
    ) -> None:
        await self.domain.set_run_state(
            state["run_id"],
            status=RunStatus.RUNNING,
            phase=phase,
            state=_json_state({**state, **update}),
        )

    async def _set_objective_status(
        self, state: ResearchState, objective_id: str | None, status: TaskStatus
    ) -> None:
        if not objective_id:
            return
        for objective in state.get("plan", {}).get("objectives", []):
            if objective.get("id") == objective_id and objective.get("task_id"):
                await self.domain.set_task_status(objective["task_id"], status)

    async def _sync_plan_tasks(self, plan: dict[str, Any]) -> None:
        mapping = {
            "todo": TaskStatus.TODO,
            "in_progress": TaskStatus.IN_PROGRESS,
            "done": TaskStatus.DONE,
            "blocked": TaskStatus.BLOCKED,
        }
        for objective in plan.get("objectives", []):
            if objective.get("task_id"):
                await self.domain.set_task_status(
                    objective["task_id"], mapping.get(objective.get("status"), TaskStatus.TODO)
                )

    @staticmethod
    def _deterministic_memo(
        state: ResearchState, bundle: dict[str, Any], source_labels: dict[str, str]
    ) -> str:
        lines = [
            f"# Research memo: {state['question']}",
            "",
            "## Decision context",
            "",
            str(state.get("decision", {}).get("rationale") or "Human review approved synthesis."),
            "",
            "## Evidence-backed findings",
            "",
        ]
        for claim in bundle["claims"]:
            labels = sorted(
                {
                    source_labels[item["source_id"]]
                    for item in claim["evidence"]
                    if item["source_id"] in source_labels
                }
            )
            citation = "".join(f"[{label}]" for label in labels)
            lines.append(
                f"- **{claim['status'].title()} ({claim['confidence']:.2f})** — "
                f"{claim['text']} {citation}".rstrip()
            )
            for evidence in claim["evidence"]:
                if evidence["stance"] == "opposes":
                    label = source_labels.get(evidence["source_id"])
                    counter = f"  - Counterevidence: {evidence['rationale']}"
                    lines.append(f"{counter} [{label}]" if label else counter)
        lines.extend(
            [
                "",
                "## Limitations and unresolved questions",
                "",
                "- Source abstracts and uploaded passages support screening, "
                "not automatic causal inference.",
                "- Contested links identify material for human review; "
                "they do not automatically refute a claim.",
                "- Full-text access, study quality, and external validity "
                "require follow-up review.",
            ]
        )
        return "\n".join(lines).strip() + "\n"


class RunManager:
    def __init__(self, workflow: ResearchWorkflow, domain: DomainService):
        self.workflow = workflow
        self.domain = domain
        self._tasks: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    async def enqueue(self, run_id: str, resume: dict | None = None) -> None:
        async with self._lock:
            current = self._tasks.get(run_id)
            if current and not current.done():
                raise RuntimeError("Run is already executing")
            self._tasks[run_id] = asyncio.create_task(
                self._execute(run_id, resume), name=f"oplab-run-{run_id}"
            )

    async def recover(self) -> None:
        for run in await self.domain.list_recoverable_runs():
            await self.enqueue(run.id)

    async def wait(self, run_id: str) -> None:
        task = self._tasks.get(run_id)
        if task:
            await task

    async def close(self) -> None:
        active = [task for task in self._tasks.values() if not task.done()]
        for task in active:
            task.cancel()
        if active:
            await asyncio.gather(*active, return_exceptions=True)

    async def _execute(self, run_id: str, resume: dict | None) -> None:
        try:
            if resume is None:
                run = await self.domain.get_run(run_id)
                await self.domain.set_run_state(
                    run_id,
                    status=RunStatus.RUNNING,
                    phase=ResearchPhase(run.current_phase),
                    state=run.state,
                )
            await self.workflow.invoke(run_id, resume)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            run = await self.domain.get_run(run_id)
            await self.domain.set_run_state(
                run_id,
                status=RunStatus.FAILED,
                phase=ResearchPhase(run.current_phase),
                state=run.state,
                stop_reason=StopReason.FAILED,
                error=f"{type(exc).__name__}: {exc}",
            )


def _owner_for_kind(kind: str) -> str:
    return {
        "discover": "Researcher",
        "extract": "Evidence Analyst",
        "challenge": "Skeptic",
        "synthesize": "Reviewer",
    }.get(kind, "Harness")


def _update_plan_progress(
    plan: dict[str, Any], state: ResearchState, evaluation: dict[str, Any]
) -> dict[str, Any]:
    value = json.loads(json.dumps(plan))
    metrics = evaluation.get("metrics", {})
    counter_searched = any(
        item.get("action") == "search_literature" and item.get("intent") == "counter"
        for item in state.get("trajectory", [])
    )
    counter_sources = {
        sid for sid, intent in state.get("source_intents", {}).items() if intent == "counter"
    }
    challenged = set(state.get("challenged_source_ids", []))
    for objective in value.get("objectives", []):
        kind = objective.get("kind")
        if kind == "discover" and metrics.get("sources", 0) >= state.get("quality_gate", {}).get(
            "min_sources", 3
        ):
            objective["status"] = "done"
        elif kind == "extract" and metrics.get("claims", 0) >= state.get("quality_gate", {}).get(
            "min_claims", 2
        ):
            objective["status"] = "done"
        elif kind == "challenge" and counter_searched and counter_sources <= challenged:
            objective["status"] = "done"
        elif objective.get("status") == "todo":
            objective["status"] = "in_progress"
            break
    return value


def _mark_synthesis_done(plan: dict[str, Any]) -> dict[str, Any]:
    value = json.loads(json.dumps(plan))
    for objective in value.get("objectives", []):
        if objective.get("kind") == "synthesize":
            objective["status"] = "done"
    return value


def _json_state(state: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(state, default=str))


def _citations_are_valid(text: str, allowed: set[str]) -> bool:
    labels = set(re.findall(r"\[(S\d+)\]", text))
    return bool(labels) and labels <= allowed


def _append_sources_if_missing(
    memo: str, sources: list[dict[str, Any]], source_labels: dict[str, str]
) -> str:
    if "## Sources" in memo:
        return memo.strip() + "\n"
    lines = [memo.rstrip(), "", "## Sources", ""]
    for source in sources:
        label = source_labels[source["id"]]
        details = ", ".join(source.get("authors") or [])
        if source.get("published_at"):
            details = f"{details} ({source['published_at']})" if details else source["published_at"]
        title = source["title"]
        uri = source["uri"]
        lines.append(f"- [{label}] {details}. [{title}]({uri}).".replace("  ", " "))
    return "\n".join(lines).strip() + "\n"
