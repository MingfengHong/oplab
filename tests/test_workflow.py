import asyncio
import hashlib
import json
from pathlib import Path

import pytest
from oplab.agents.workflow import ResearchWorkflow, RunManager
from oplab.domain.commands import RecordDecision, make_idempotency_key
from oplab.domain.enums import MeetingDecisionKind
from oplab.harness.model import ModelGateway

from tests.fixtures import StubSearch


class ProviderShapedModel:
    enabled = True

    async def complete_json(self, **_kwargs):
        return {
            "research_question": "How do communities recover from maintainer loss?",
            "scope": "Open-source governance resilience",
            "success_criteria": "Identify recovery mechanisms; retain opposing evidence",
            "exclusions": "No unsupported causal claims",
            "evidence_requirements": (
                "Longitudinal studies; case evidence from open-source communities"
            ),
            "search_query": (
                '"open source community" AND "maintainer loss" AND resilience governance'
            ),
        }

    async def complete_text(self, *, fallback, **_kwargs):
        return fallback


@pytest.mark.asyncio
async def test_workflow_normalizes_provider_charter_and_reaches_meeting(settings, services):
    _, domain, queries = services
    project = await domain.create_project(
        title="Provider-shaped charter",
        question="How do open-source communities recover after maintainer loss?",
        success_criteria=[],
    )
    run = await domain.start_run(project.id)
    workflow = ResearchWorkflow(
        settings=settings,
        domain=domain,
        search=StubSearch(),
        model=ProviderShapedModel(),
    )
    await workflow.start()
    manager = RunManager(workflow, domain)
    await manager.enqueue(run.id)
    await manager.wait(run.id)

    waiting = await queries.run(run.id)
    assert waiting["status"] == "needs_user", waiting["error"]
    assert waiting["current_phase"] == "meeting"
    assert waiting["state"]["charter"]["search_query"] == (
        "open source community maintainer loss resilience governance"
    )
    assert waiting["state"]["charter"]["success_criteria"] == [
        "Identify recovery mechanisms",
        "retain opposing evidence",
    ]
    evidence = await queries.evidence(project.id)
    assert len(evidence["sources"]) == 2
    assert len(evidence["claims"]) == 1

    await manager.close()
    await workflow.close()


@pytest.mark.asyncio
async def test_workflow_survives_runtime_restart_and_publishes_cited_memo(settings, services):
    _, domain, queries = services
    project = await domain.create_project(
        title="Open-source resilience",
        question="Does maintainer diversity improve recovery after contributor loss?",
        success_criteria=["Retain supporting and opposing evidence"],
    )
    run = await domain.start_run(project.id)

    workflow = ResearchWorkflow(
        settings=settings,
        domain=domain,
        search=StubSearch(),
        model=ModelGateway(settings),
    )
    await workflow.start()
    manager = RunManager(workflow, domain)
    await manager.enqueue(run.id)
    await manager.wait(run.id)

    waiting = await queries.run(run.id)
    assert waiting["status"] == "needs_user"
    assert waiting["current_phase"] == "meeting"
    assert waiting["meeting"]["status"] == "waiting_user"
    assert len(waiting["meeting"]["position_cards"]) == 4

    # Destroy the graph/checkpointer connection to model an API process restart.
    await manager.close()
    await workflow.close()

    decision = {
        "kind": "continue",
        "rationale": "The bound support and counterevidence are sufficient for a cautious memo.",
        "direction": None,
        "dissent": [],
    }
    digest = hashlib.sha256(json.dumps(decision, sort_keys=True).encode()).hexdigest()
    await domain.execute(
        RecordDecision(
            project_id=project.id,
            idempotency_key=make_idempotency_key(project.id, run.id, "RecordDecision", digest),
            actor="user",
            meeting_id=waiting["meeting"]["id"],
            kind=MeetingDecisionKind.CONTINUE,
            rationale=decision["rationale"],
        )
    )

    recovered_workflow = ResearchWorkflow(
        settings=settings,
        domain=domain,
        search=StubSearch(),
        model=ModelGateway(settings),
    )
    await recovered_workflow.start()
    recovered_manager = RunManager(recovered_workflow, domain)
    await recovered_manager.enqueue(run.id, resume=decision)
    await recovered_manager.wait(run.id)

    completed = await queries.run(run.id)
    assert completed["status"] == "completed", completed["error"]
    assert completed["report_artifact_id"]
    artifact = await queries.artifact(completed["report_artifact_id"])
    memo = await asyncio.to_thread(Path(artifact.path).read_text, encoding="utf-8")
    assert "[S1]" in memo
    assert "## Sources" in memo
    assert "Counterevidence" in memo
    evidence = await queries.evidence(project.id)
    assert {link["stance"] for link in evidence["links"]} == {"supports", "opposes"}
    assert all(link["passage_id"] for link in evidence["links"])

    await recovered_manager.close()
    await recovered_workflow.close()
