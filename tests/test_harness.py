import httpx
import pytest
from oplab.config import Settings
from oplab.harness.model import ModelGateway
from oplab.harness.policy import fallback_plan
from oplab.harness.schemas import ResearchPlan


@pytest.mark.asyncio
async def test_typed_model_output_retries_after_schema_failure(tmp_path):
    responses = [
        {"choices": [{"message": {"content": '{"research_question": 3}'}}]},
        {
            "choices": [
                {
                    "message": {
                        "content": fallback_plan(
                            "How can a harness preserve evidence provenance?", []
                        ).model_dump_json()
                    }
                }
            ]
        },
    ]

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=responses.pop(0))

    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'typed.sqlite').as_posix()}",
        checkpoint_database_url=str(tmp_path / "typed-checkpoints.sqlite"),
        openai_api_key="test-key",
        openai_base_url="https://model.test/v1",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = ModelGateway(settings, client=client)
        fallback = fallback_plan("How can a harness preserve evidence provenance?", [])
        result = await gateway.complete_model(
            system="Return a plan.",
            prompt="Plan the investigation.",
            schema=ResearchPlan,
            fallback=fallback,
        )

    assert result.research_question == "How can a harness preserve evidence provenance?"
    assert not responses
