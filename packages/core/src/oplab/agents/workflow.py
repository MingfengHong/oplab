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
from oplab.domain.commands import (
    ContestClaim,
    CreateTask,
    ProposeClaim,
    PublishArtifact,
    RequestMeeting,
    make_idempotency_key,
)
from oplab.domain.enums import ResearchPhase, RunStatus, StopReason
from oplab.domain.service import DomainService
from oplab.evidence.ingestor import EvidenceIngestor
from oplab.harness.model import ModelGateway, ModelUnavailableError
from oplab.retrieval.base import SearchAdapter
from oplab.retrieval.text import first_substantive_sentence


class ResearchWorkflow:
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
        self.search = search
        self.model = model
        self.ingestor = EvidenceIngestor(domain)
        self._saver_context: Any = None
        self._saver: AsyncSqliteSaver | None = None
        self.graph: Any = None

    async def start(self) -> None:
        self._saver_context = AsyncSqliteSaver.from_conn_string(
            self.settings.checkpoint_database_url
        )
        self._saver = await self._saver_context.__aenter__()
        builder = StateGraph(ResearchState)
        builder.add_node("charter", self._charter)
        builder.add_node("librarian", self._librarian)
        builder.add_node("skeptic", self._skeptic)
        builder.add_node("meeting", self._meeting)
        builder.add_node("writer", self._writer)
        builder.add_node("stop", self._stop)
        builder.add_edge(START, "charter")
        builder.add_edge("charter", "librarian")
        builder.add_edge("librarian", "skeptic")
        builder.add_edge("skeptic", "meeting")
        builder.add_conditional_edges(
            "meeting",
            self._route_after_meeting,
            {"writer": "writer", "revise": "librarian", "stop": "stop"},
        )
        builder.add_edge("writer", END)
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
            current_phase=ResearchPhase.CHARTER.value,
            generation=0,
            source_ids=[],
            claim_ids=[],
        )

    async def invoke(self, run_id: str, resume: dict | None = None) -> dict:
        if self.graph is None:
            raise RuntimeError("Research workflow has not started")
        run = await self.domain.get_run(run_id)
        config = {"configurable": {"thread_id": run.thread_id}}
        if resume is not None:
            return await self.graph.ainvoke(GraphCommand(resume=resume), config=config)
        snapshot = await self.graph.aget_state(config)
        if snapshot.values:
            return await self.graph.ainvoke(None, config=config)
        return await self.graph.ainvoke(await self.initial_state(run_id), config=config)

    async def _charter(self, state: ResearchState) -> dict:
        fallback = {
            "research_question": state["question"],
            "scope": "Phase-A evidence synthesis",
            "success_criteria": state.get("success_criteria")
            or ["Identify supported and contested claims", "Preserve source-level citations"],
            "exclusions": ["No causal claim without an identified design"],
            "evidence_requirements": [
                "Prefer primary academic sources",
                "Search explicitly for counterevidence",
            ],
            "search_query": state["question"],
            "search_queries": [state["question"]],
        }
        try:
            charter_payload = await self.model.complete_json(
                system=(
                    "You are the research director. Return a compact JSON Research Charter with "
                    "research_question, scope, success_criteria, exclusions, "
                    "evidence_requirements, and search_queries. success_criteria, exclusions, and "
                    "evidence_requirements must be JSON arrays of strings. search_queries must be "
                    "three concise English academic keyword queries as a JSON array. Do not use "
                    "quotes or Boolean operators such as AND/OR. Do not invent findings."
                ),
                prompt=state["question"],
                fallback=fallback,
            )
        except ModelUnavailableError:
            charter_payload = fallback
        charter = _normalize_charter(charter_payload, fallback)
        digest = hashlib.sha256(json.dumps(charter, sort_keys=True).encode()).hexdigest()
        for owner, title, objective in (
            ("librarian", "Build the evidence ledger", "Find and bind passages to source records"),
            ("skeptic", "Search for counterevidence", "Challenge early claims independently"),
        ):
            await self.domain.execute(
                CreateTask(
                    project_id=state["project_id"],
                    idempotency_key=make_idempotency_key(
                        state["project_id"], state["run_id"], f"CreateTask:{owner}", digest
                    ),
                    actor="pi",
                    title=title,
                    objective=objective,
                    owner=owner,
                    success_criteria=charter.get("success_criteria", []),
                    evidence_requirements=charter.get("evidence_requirements", []),
                )
            )
        update = {"charter": charter, "current_phase": ResearchPhase.LIBRARIAN.value}
        await self._persist_progress(state, update, ResearchPhase.LIBRARIAN)
        return update

    async def _librarian(self, state: ResearchState) -> dict:
        generation = state.get("generation", 0)
        charter = state.get("charter", {})
        queries = charter.get("search_queries") or [
            charter.get("search_query", state["question"])
        ]
        queries = [_normalize_search_query(str(query)) for query in queries]
        queries = [query for query in dict.fromkeys(queries) if query]
        if state.get("revision_direction"):
            queries = [
                _normalize_search_query(f"{query} {state['revision_direction']}")
                for query in queries
            ]
        candidates = []
        seen_candidates = set()
        per_query = max(2, (self.settings.retrieval_limit + len(queries) - 1) // len(queries))
        for query in queries:
            for candidate in await self.search.search(query, per_query):
                key = candidate.doi or candidate.uri or candidate.title.casefold()
                if key in seen_candidates:
                    continue
                seen_candidates.add(key)
                candidates.append(candidate)
                if len(candidates) >= self.settings.retrieval_limit:
                    break
            if len(candidates) >= self.settings.retrieval_limit:
                break
        if not candidates:
            fallback_query = _normalize_search_query(
                str(charter.get("research_question") or state["question"])
            )
            if fallback_query and fallback_query not in queries:
                candidates = await self.search.search(
                    fallback_query, self.settings.retrieval_limit
                )
        source_ids = list(state.get("source_ids", []))
        claim_ids = list(state.get("claim_ids", []))
        for candidate in candidates:
            result = await self.ingestor.ingest(
                project_id=state["project_id"],
                run_id=state["run_id"],
                actor="librarian",
                candidate=candidate,
                generation=generation,
            )
            if result.entity_id not in source_ids:
                source_ids.append(result.entity_id)
            claim_id = await self._propose_first_claim(state, result.entity_id, generation)
            if claim_id and claim_id not in claim_ids:
                claim_ids.append(claim_id)
        # User-uploaded sources are first-class evidence even if network search returned nothing.
        for source in await self.domain.list_project_sources(state["project_id"]):
            if source.id in source_ids:
                continue
            source_ids.append(source.id)
            claim_id = await self._propose_first_claim(state, source.id, generation)
            if claim_id and claim_id not in claim_ids:
                claim_ids.append(claim_id)
        update = {
            "source_ids": source_ids,
            "claim_ids": claim_ids,
            "current_phase": ResearchPhase.SKEPTIC.value,
        }
        await self._persist_progress(state, update, ResearchPhase.SKEPTIC)
        return update

    async def _propose_first_claim(
        self, state: ResearchState, source_id: str, generation: int
    ) -> str | None:
        passages = await self.domain.list_passages_for_source(source_id)
        if not passages:
            return None
        passage = passages[0]
        claim_text = first_substantive_sentence(passage.text)
        if len(claim_text) < 30:
            return None
        digest = hashlib.sha256(f"{source_id}:{passage.id}:{claim_text}".encode()).hexdigest()
        claim = await self.domain.execute(
            ProposeClaim(
                project_id=state["project_id"],
                idempotency_key=make_idempotency_key(
                    state["project_id"], state["run_id"], "ProposeClaim", digest, generation
                ),
                actor="librarian",
                canonical_text=claim_text,
                confidence=0.55,
                passage_id=passage.id,
                rationale="The claim is stated in a bound source passage.",
                strength=0.55,
            )
        )
        return claim.entity_id

    async def _skeptic(self, state: ResearchState) -> dict:
        generation = state.get("generation", 0)
        base_query = state.get("charter", {}).get("search_query", state["question"])
        query = _normalize_search_query(
            f"{base_query} limitations contradictory evidence null results"
        )
        candidates = await self.search.search(query, max(3, self.settings.retrieval_limit // 2))
        opposing_passages = []
        source_ids = list(state.get("source_ids", []))
        for candidate in candidates:
            result = await self.ingestor.ingest(
                project_id=state["project_id"],
                run_id=state["run_id"],
                actor="skeptic",
                candidate=candidate,
                generation=generation,
            )
            if result.entity_id not in source_ids:
                source_ids.append(result.entity_id)
            passages = await self.domain.list_passages_for_source(result.entity_id)
            if passages:
                opposing_passages.append(passages[0])
        for index, claim_id in enumerate(state.get("claim_ids", [])):
            if index >= len(opposing_passages):
                break
            passage = opposing_passages[index]
            digest = hashlib.sha256(f"{claim_id}:{passage.id}".encode()).hexdigest()
            await self.domain.execute(
                ContestClaim(
                    project_id=state["project_id"],
                    idempotency_key=make_idempotency_key(
                        state["project_id"], state["run_id"], "ContestClaim", digest, generation
                    ),
                    actor="skeptic",
                    claim_id=claim_id,
                    passage_id=passage.id,
                    rationale=(
                        "The independently retrieved limitations search may constrain or challenge "
                        "the scope of this claim; human review is required."
                    ),
                    strength=0.45,
                )
            )
        update = {"source_ids": source_ids, "current_phase": ResearchPhase.MEETING.value}
        await self._persist_progress(state, update, ResearchPhase.MEETING)
        return update

    async def _meeting(self, state: ResearchState) -> dict:
        generation = state.get("generation", 0)
        bundle = await self.domain.evidence_bundle(state["project_id"])
        supported = sum(claim["status"] == "supported" for claim in bundle["claims"])
        contested = sum(claim["status"] == "contested" for claim in bundle["claims"])
        positions = [
            {
                "agent": "PI",
                "recommendation": "continue" if bundle["claims"] else "revise",
                "confidence": 0.65 if bundle["claims"] else 0.2,
                "reason": "The charter is ready; the decision depends on evidence coverage.",
            },
            {
                "agent": "Librarian",
                "recommendation": "continue" if len(bundle["sources"]) >= 3 else "revise",
                "confidence": min(0.9, len(bundle["sources"]) / 10),
                "reason": (
                    f"{len(bundle['sources'])} sources and "
                    f"{len(bundle['claims'])} claims are bound."
                ),
            },
            {
                "agent": "Skeptic",
                "recommendation": "revise" if contested == 0 else "continue",
                "confidence": 0.6,
                "reason": f"{contested} claims have explicit opposing evidence.",
            },
            {
                "agent": "Writer",
                "recommendation": "continue" if supported + contested > 0 else "revise",
                "confidence": 0.55,
                "reason": "A memo can be drafted only from traceable evidence links.",
            },
        ]
        packet = {
            "source_count": len(bundle["sources"]),
            "claim_count": len(bundle["claims"]),
            "supported_count": supported,
            "contested_count": contested,
            "unresolved_questions": []
            if bundle["claims"]
            else ["No usable evidence was retrieved; add sources or revise the search."],
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
                trigger="Evidence review before synthesis",
                agenda=[
                    "Is the evidence coverage sufficient?",
                    "Which claims remain contested?",
                    "Continue, revise the search, or stop?",
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
        update: dict[str, Any] = {"meeting_id": result.entity_id, "decision": decision}
        if kind == "revise":
            update.update(
                {
                    "generation": generation + 1,
                    "revision_direction": decision.get("direction"),
                    "current_phase": ResearchPhase.LIBRARIAN.value,
                }
            )
            await self._persist_progress(state, update, ResearchPhase.LIBRARIAN)
        elif kind == "continue":
            update["current_phase"] = ResearchPhase.WRITER.value
            await self._persist_progress(state, update, ResearchPhase.WRITER)
        else:
            update["current_phase"] = ResearchPhase.COMPLETE.value
        return update

    @staticmethod
    def _route_after_meeting(state: ResearchState) -> Literal["writer", "revise", "stop"]:
        kind = state.get("decision", {}).get("kind", "stop")
        if kind == "continue":
            return "writer"
        if kind == "revise":
            return "revise"
        return "stop"

    async def _writer(self, state: ResearchState) -> dict:
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
                    "You are a cautious research synthesizer. Use only the supplied evidence. "
                    "Every substantive claim must cite one or more supplied labels like [S1]. "
                    "Preserve uncertainty and counterevidence. Never invent a citation label."
                ),
                prompt=json.dumps(
                    {
                        "question": state["question"],
                        "charter": state.get("charter", {}),
                        "decision": state.get("decision", {}),
                        "evidence": bundle,
                        "source_labels": source_labels,
                    },
                    ensure_ascii=False,
                ),
                fallback=fallback,
            )
            memo = body if _citations_are_valid(body, set(source_labels.values())) else fallback
        except ModelUnavailableError:
            memo = fallback
        memo = _append_sources_if_missing(memo, bundle["sources"], source_labels)
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
                title="Research memo",
                path=str(final_path.resolve()),
                media_type="text/markdown",
                content_hash=digest,
                provenance={
                    "trace_id": state["trace_id"],
                    "thread_id": state["thread_id"],
                    "source_ids": list(source_labels),
                    "model": self.settings.openai_model if self.model.enabled else "deterministic",
                },
            )
        )
        final_state = {
            **state,
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
        return final_state

    async def _stop(self, state: ResearchState) -> dict:
        final_state = {
            **state,
            "current_phase": ResearchPhase.COMPLETE.value,
            "stop_reason": StopReason.SUCCESS.value,
        }
        await self.domain.set_run_state(
            state["run_id"],
            status=RunStatus.CANCELLED,
            phase=ResearchPhase.COMPLETE,
            state=_json_state(final_state),
            stop_reason=StopReason.SUCCESS,
        )
        return final_state

    async def _persist_progress(
        self, state: ResearchState, update: dict, phase: ResearchPhase
    ) -> None:
        merged = {**state, **update}
        await self.domain.set_run_state(
            state["run_id"],
            status=RunStatus.RUNNING,
            phase=phase,
            state=_json_state(merged),
        )

    @staticmethod
    def _deterministic_memo(
        state: ResearchState, bundle: dict, source_labels: dict[str, str]
    ) -> str:
        lines = [
            f"# Research memo: {state['question']}",
            "",
            "## Decision context",
            "",
            str(
                state.get("decision", {}).get("rationale")
                or "The evidence review approved synthesis."
            ),
            "",
            "## Evidence-backed findings",
            "",
        ]
        if not bundle["claims"]:
            lines.append(
                "No evidence-backed claim is currently available. Further retrieval is required."
            )
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
                "they do not by themselves refute a claim.",
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


def _json_state(state: dict) -> dict:
    return json.loads(json.dumps(state, default=str))


def _normalize_charter(payload: Any, fallback: dict[str, Any]) -> dict[str, Any]:
    """Keep provider formatting differences from breaking the research run."""
    values = payload if isinstance(payload, dict) else {}
    raw_queries = values.get("search_queries") or values.get("search_query")
    fallback_queries = fallback.get("search_queries") or [fallback["search_query"]]
    search_queries = [
        query
        for query in (
            _normalize_search_query(item)
            for item in _as_text_list(raw_queries, fallback_queries)
        )
        if query
    ][:4]
    if not search_queries:
        search_queries = [_normalize_search_query(fallback["search_query"])]
    return {
        "research_question": _as_text(
            values.get("research_question"), fallback["research_question"]
        ),
        "scope": _as_text(values.get("scope"), fallback["scope"]),
        "success_criteria": _as_text_list(
            values.get("success_criteria"), fallback["success_criteria"]
        ),
        "exclusions": _as_text_list(values.get("exclusions"), fallback["exclusions"]),
        "evidence_requirements": _as_text_list(
            values.get("evidence_requirements"), fallback["evidence_requirements"]
        ),
        "search_query": search_queries[0],
        "search_queries": search_queries,
    }


def _as_text(value: Any, fallback: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, list):
        joined = " ".join(str(item).strip() for item in value if str(item).strip())
        if joined:
            return joined
    return fallback


def _as_text_list(value: Any, fallback: list[str]) -> list[str]:
    if isinstance(value, str):
        candidates = re.split(r"(?:\r?\n|[;；])", value)
    elif isinstance(value, list):
        candidates = value
    else:
        return list(fallback)
    items = []
    for candidate in candidates:
        text = re.sub(r"^\s*(?:[-*•]|\d+[.)、])\s*", "", str(candidate)).strip()
        if text:
            items.append(text)
    return items or list(fallback)


def _normalize_search_query(value: str) -> str:
    text = re.sub(r'["“”‘’()]', " ", value)
    text = re.sub(r"\b(?:AND|OR|NOT)\b", " ", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip()


def _citations_are_valid(text: str, allowed: set[str]) -> bool:
    labels = set(re.findall(r"\[(S\d+)\]", text))
    return bool(labels) and labels.issubset(allowed)


def _append_sources_if_missing(text: str, sources: list[dict], labels: dict[str, str]) -> str:
    if "## Sources" in text:
        return text.rstrip() + "\n"
    lines = [text.rstrip(), "", "## Sources", ""]
    for source in sources:
        label = labels[source["id"]]
        details = ", ".join(source.get("authors") or [])
        year = source.get("published_at") or "n.d."
        prefix = f"{details} ({year}). " if details else f"({year}). "
        lines.append(f"- [{label}] {prefix}{source['title']}. {source['uri']}")
    return "\n".join(lines).strip() + "\n"
