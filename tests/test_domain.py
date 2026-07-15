import pytest
from oplab.domain.commands import AddEvidence, make_idempotency_key
from oplab.domain.service import PolicyDeniedError


@pytest.mark.asyncio
async def test_evidence_commands_are_idempotent_and_policy_checked(services):
    _, domain, queries = services
    project = await domain.create_project(
        title="Evidence test",
        question="How should evidence writes remain repeatable and auditable?",
        success_criteria=[],
    )
    command = AddEvidence(
        project_id=project.id,
        idempotency_key=make_idempotency_key(project.id, "manual", "AddEvidence", "a" * 64),
        actor="user",
        source_type="local_document",
        title="Test source",
        uri="oplab://test",
        content_hash="a" * 64,
        passages=[
            {
                "locator": "passage:1",
                "text": "A sufficiently long passage that supports a test claim.",
                "start_char": 0,
                "end_char": 56,
                "passage_hash": "b" * 64,
            }
        ],
    )
    first = await domain.execute(command)
    second = await domain.execute(command)
    assert first.entity_id == second.entity_id
    assert second.replayed is True
    evidence = await queries.evidence(project.id)
    assert len(evidence["sources"]) == 1
    assert len(evidence["passages"]) == 1

    denied = command.model_copy(
        update={
            "actor": "writer",
            "idempotency_key": make_idempotency_key(project.id, "manual", "AddEvidence", "c" * 64),
        }
    )
    with pytest.raises(PolicyDeniedError):
        await domain.execute(denied)
