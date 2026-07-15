from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from oplab.domain.commands import ContestClaim, ProposeClaim, make_idempotency_key
from oplab.domain.service import DomainService
from oplab.evidence.ingestor import EvidenceIngestor
from oplab.harness.model import ModelGateway, ModelUnavailableError
from oplab.harness.schemas import (
    ChallengeBatch,
    ChallengeLink,
    ClaimExtraction,
    ClaimExtractionBatch,
    HarnessAction,
    ToolOutcome,
)
from oplab.retrieval.base import SearchAdapter
from oplab.retrieval.text import first_substantive_sentence


class HarnessToolRegistry:
    """Deterministic, permissioned research tools exposed to the harness controller."""

    def __init__(
        self,
        *,
        domain: DomainService,
        search: SearchAdapter,
        model: ModelGateway,
        retrieval_limit: int,
    ):
        self.domain = domain
        self.search = search
        self.model = model
        self.retrieval_limit = retrieval_limit
        self.ingestor = EvidenceIngestor(domain)

    async def execute(
        self, state: dict[str, Any], action: HarnessAction
    ) -> tuple[dict[str, Any], ToolOutcome]:
        if action.action == "search_literature":
            return await self._search(state, action)
        if action.action == "extract_claims":
            return await self._extract_claims(state, action)
        if action.action == "challenge_claims":
            return await self._challenge_claims(state, action)
        if action.action == "inspect_evidence":
            bundle = await self.domain.evidence_bundle(state["project_id"])
            outcome = ToolOutcome(
                action=action.action,
                status="ok",
                objective_id=action.objective_id,
                summary="Inspected the current evidence ledger.",
                observations=[
                    f"{len(bundle['sources'])} sources",
                    f"{len(bundle['claims'])} claims",
                ],
            )
            return {}, outcome
        raise ValueError(f"Action {action.action} is not an executable research tool")

    async def _search(
        self, state: dict[str, Any], action: HarnessAction
    ) -> tuple[dict[str, Any], ToolOutcome]:
        budget = state.get("budget", {})
        remaining = int(budget.get("max_sources", 20)) - len(state.get("source_ids", []))
        if remaining <= 0:
            return {}, ToolOutcome(
                action=action.action,
                status="denied",
                objective_id=action.objective_id,
                query=action.query,
                summary="Source budget exhausted.",
            )
        limit = min(self.retrieval_limit, remaining)
        candidates = await self.search.search(action.query or state["question"], limit)
        source_ids = list(state.get("source_ids", []))
        source_intents = dict(state.get("source_intents", {}))
        created: list[str] = []
        for candidate in candidates:
            result = await self.ingestor.ingest(
                project_id=state["project_id"],
                run_id=state["run_id"],
                actor="skeptic" if action.intent == "counter" else "librarian",
                candidate=candidate,
                generation=int(state.get("generation", 0)),
            )
            if result.entity_id not in source_ids:
                source_ids.append(result.entity_id)
                created.append(result.entity_id)
            source_intents[result.entity_id] = action.intent or "support"
        usage = dict(state.get("usage", {}))
        usage["searches"] = int(usage.get("searches", 0)) + 1
        outcome = ToolOutcome(
            action=action.action,
            status="ok" if candidates else "no_result",
            objective_id=action.objective_id,
            query=action.query,
            created_source_ids=created,
            summary=f"Retrieved {len(candidates)} candidates and added {len(created)} new sources.",
            observations=[f"intent={action.intent}"],
        )
        return {"source_ids": source_ids, "source_intents": source_intents, "usage": usage}, outcome

    async def _extract_claims(
        self, state: dict[str, Any], action: HarnessAction
    ) -> tuple[dict[str, Any], ToolOutcome]:
        processed = set(state.get("processed_source_ids", []))
        source_intents = state.get("source_intents", {})
        source_ids = [
            sid
            for sid in state.get("source_ids", [])
            if sid not in processed and source_intents.get(sid) != "counter"
        ]
        records: list[dict[str, str]] = []
        fallback_items: list[ClaimExtraction] = []
        for source_id in source_ids[:10]:
            passages = await self.domain.list_passages_for_source(source_id)
            if not passages:
                processed.add(source_id)
                continue
            passage = passages[0]
            claim = first_substantive_sentence(passage.text)
            records.append(
                {"source_id": source_id, "passage_id": passage.id, "passage": passage.text[:2500]}
            )
            if len(claim) >= 20:
                fallback_items.append(
                    ClaimExtraction(
                        source_id=source_id,
                        passage_id=passage.id,
                        claim=claim,
                        confidence=0.55,
                        rationale="The statement is directly present in the stored passage.",
                    )
                )
        fallback = ClaimExtractionBatch(items=fallback_items)
        batch = fallback
        if records and hasattr(self.model, "complete_model"):
            try:
                batch = await self.model.complete_model(
                    system=(
                        "Extract at most one cautious, self-contained claim per source passage. "
                        "Use only supplied source_id and passage_id values. Do not infer causality "
                        "unless the passage explicitly reports a causal design."
                    ),
                    prompt=json.dumps(records, ensure_ascii=False),
                    schema=ClaimExtractionBatch,
                    fallback=fallback,
                )
            except ModelUnavailableError:
                batch = fallback
        valid_pairs = {(item["source_id"], item["passage_id"]) for item in records}
        existing = {
            claim.canonical_text.strip().casefold()
            for claim in await self.domain.list_project_claims(state["project_id"])
        }
        claim_ids = list(state.get("claim_ids", []))
        created: list[str] = []
        for item in batch.items:
            if (item.source_id, item.passage_id) not in valid_pairs:
                continue
            if item.claim.strip().casefold() in existing:
                continue
            digest = hashlib.sha256(
                f"{item.source_id}:{item.passage_id}:{item.claim}".encode()
            ).hexdigest()
            result = await self.domain.execute(
                ProposeClaim(
                    project_id=state["project_id"],
                    idempotency_key=make_idempotency_key(
                        state["project_id"],
                        state["run_id"],
                        "ProposeClaim",
                        digest,
                        int(state.get("generation", 0)),
                    ),
                    actor="librarian",
                    canonical_text=item.claim,
                    confidence=item.confidence,
                    passage_id=item.passage_id,
                    rationale=item.rationale,
                    strength=item.confidence,
                )
            )
            if result.entity_id not in claim_ids:
                claim_ids.append(result.entity_id)
                created.append(result.entity_id)
            existing.add(item.claim.strip().casefold())
        processed.update(source_ids)
        outcome = ToolOutcome(
            action=action.action,
            status="ok" if created else "no_result",
            objective_id=action.objective_id,
            created_claim_ids=created,
            summary=f"Created {len(created)} passage-bound claims from {len(source_ids)} sources.",
        )
        return {"claim_ids": claim_ids, "processed_source_ids": sorted(processed)}, outcome

    async def _challenge_claims(
        self, state: dict[str, Any], action: HarnessAction
    ) -> tuple[dict[str, Any], ToolOutcome]:
        bundle = await self.domain.evidence_bundle(state["project_id"])
        challenged = set(state.get("challenged_source_ids", []))
        source_intents = state.get("source_intents", {})
        counter_sources = [
            sid
            for sid in state.get("source_ids", [])
            if source_intents.get(sid) == "counter" and sid not in challenged
        ]
        records: list[dict[str, str]] = []
        fallback_items: list[ChallengeLink] = []
        assessed_sources: set[str] = set()
        for source_id in counter_sources[:8]:
            if len(records) >= 30:
                break
            passages = await self.domain.list_passages_for_source(source_id)
            if not passages:
                challenged.add(source_id)
                continue
            passage = passages[0]
            for claim in bundle["claims"][:8]:
                if len(records) >= 30:
                    break
                records.append(
                    {
                        "claim_id": claim["id"],
                        "claim": claim["text"],
                        "passage_id": passage.id,
                        "counter_passage": passage.text[:2000],
                    }
                )
                material = _looks_like_material_challenge(claim["text"], passage.text)
                fallback_items.append(
                    ChallengeLink(
                        claim_id=claim["id"],
                        passage_id=passage.id,
                        is_material_challenge=material,
                        rationale=(
                            "The passage reports a limitation, attenuation, null result, or "
                            "boundary condition relevant to the claim."
                            if material
                            else (
                                "No material challenge can be established from lexical "
                                "evidence alone."
                            )
                        ),
                        strength=0.45 if material else 0.1,
                    )
                )
                assessed_sources.add(source_id)
        fallback = ChallengeBatch(items=fallback_items)
        batch = fallback
        if records and hasattr(self.model, "complete_model"):
            compact_prompt = {
                "claims": list(
                    {
                        item["claim_id"]: {
                            "claim_id": item["claim_id"],
                            "claim": item["claim"],
                        }
                        for item in records
                    }.values()
                ),
                "counter_passages": list(
                    {
                        item["passage_id"]: {
                            "passage_id": item["passage_id"],
                            "counter_passage": item["counter_passage"][:1200],
                        }
                        for item in records
                    }.values()
                ),
            }
            try:
                batch = await self.model.complete_model(
                    system=(
                        "Judge whether each counter passage materially limits, contradicts, or "
                        "qualifies its paired claim. Use only supplied IDs. Mark false when the "
                        "relationship is merely topical or uncertain."
                    ),
                    prompt=json.dumps(compact_prompt, ensure_ascii=False),
                    schema=ChallengeBatch,
                    fallback=fallback,
                )
            except ModelUnavailableError:
                batch = fallback
        valid_pairs = {(item["claim_id"], item["passage_id"]) for item in records}
        contested: list[str] = []
        for item in batch.items:
            if not item.is_material_challenge:
                continue
            if (item.claim_id, item.passage_id) not in valid_pairs:
                continue
            digest = hashlib.sha256(f"{item.claim_id}:{item.passage_id}".encode()).hexdigest()
            result = await self.domain.execute(
                ContestClaim(
                    project_id=state["project_id"],
                    idempotency_key=make_idempotency_key(
                        state["project_id"],
                        state["run_id"],
                        "ContestClaim",
                        digest,
                        int(state.get("generation", 0)),
                    ),
                    actor="skeptic",
                    claim_id=item.claim_id,
                    passage_id=item.passage_id,
                    rationale=item.rationale,
                    strength=item.strength,
                )
            )
            if result.entity_id not in contested:
                contested.append(result.entity_id)
        challenged.update(assessed_sources)
        outcome = ToolOutcome(
            action=action.action,
            status="ok",
            objective_id=action.objective_id,
            contested_claim_ids=contested,
            summary=(
                f"Compared {len(records)} claim-passage pairs and materialized "
                f"{len(contested)} contested claims."
            ),
            observations=[] if contested else ["No material challenge passed the relevance gate."],
        )
        return {"challenged_source_ids": sorted(challenged)}, outcome


def _looks_like_material_challenge(claim: str, passage: str) -> bool:
    limiting = re.search(
        r"\b(?:null|no association|not significant|weakens?|attenuat|limit|boundary|contradict)",
        passage,
        flags=re.IGNORECASE,
    )
    claim_terms = set(re.findall(r"[a-z]{5,}", claim.casefold()))
    passage_terms = set(re.findall(r"[a-z]{5,}", passage.casefold()))
    return bool(limiting and claim_terms.intersection(passage_terms))
