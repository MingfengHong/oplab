from oplab.domain.commands import ProposeClaim, make_idempotency_key
from oplab.harness.schemas import HarnessAction
from oplab.harness.tools import HarnessToolRegistry
from oplab.retrieval.base import PassageCandidate, SourceCandidate


class NoSearch:
    async def search(self, query: str, limit: int) -> list[SourceCandidate]:
        return []


class FallbackModel:
    enabled = False


async def test_challenge_tool_batches_large_pair_sets(settings, services):
    _, domain, _ = services
    project = await domain.create_project(
        title="Challenge batching",
        question="Which evidence materially challenges a large claim set?",
        success_criteria=[],
    )
    run = await domain.start_run(project.id)
    registry = HarnessToolRegistry(
        domain=domain,
        search=NoSearch(),
        model=FallbackModel(),
        retrieval_limit=8,
    )
    support = await registry.ingestor.ingest(
        project_id=project.id,
        run_id=run.id,
        actor="librarian",
        candidate=SourceCandidate(
            source_type="academic",
            title="Supporting source",
            uri="https://example.test/support",
            authors=["Researcher"],
            content="Contributor redundancy is associated with project recovery.",
            passages=[
                PassageCandidate(
                    locator="abstract:1",
                    text="Contributor redundancy is associated with project recovery.",
                )
            ],
        ),
    )
    passage = (await domain.list_passages_for_source(support.entity_id))[0]
    for index in range(6):
        await domain.execute(
            ProposeClaim(
                project_id=project.id,
                idempotency_key=make_idempotency_key(
                    project.id, run.id, "ProposeClaim", f"large-batch-{index}"
                ),
                actor="librarian",
                canonical_text=(
                    f"Claim {index}: contributor redundancy is associated with project recovery."
                ),
                confidence=0.55,
                passage_id=passage.id,
                rationale="Stored support passage.",
            )
        )
    source_ids = []
    for index in range(8):
        result = await registry.ingestor.ingest(
            project_id=project.id,
            run_id=run.id,
            actor="skeptic",
            candidate=SourceCandidate(
                source_type="academic",
                title=f"Counter source {index}",
                uri=f"https://example.test/counter-{index}",
                authors=["Skeptic"],
                content=(
                    "The association weakens under a boundary condition and may be null "
                    "when contributor redundancy is controlled."
                ),
                passages=[
                    PassageCandidate(
                        locator="abstract:1",
                        text=(
                            "The association weakens under a boundary condition and may be null "
                            "when contributor redundancy is controlled."
                        ),
                    )
                ],
            ),
        )
        source_ids.append(result.entity_id)

    update, outcome = await registry.execute(
        {
            "project_id": project.id,
            "run_id": run.id,
            "source_ids": source_ids,
            "source_intents": {source_id: "counter" for source_id in source_ids},
            "challenged_source_ids": [],
            "generation": 0,
        },
        HarnessAction(
            action="challenge_claims",
            objective_id="challenge",
            rationale="Assess counterevidence in bounded batches.",
            expected_outcome="No schema overflow.",
        ),
    )

    assert outcome.status == "ok"
    assert len(outcome.contested_claim_ids) == 6
    assert 0 < len(update["challenged_source_ids"]) < len(source_ids)
