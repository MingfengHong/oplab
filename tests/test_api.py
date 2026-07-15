import time

from fastapi.testclient import TestClient
from oplab_api.main import create_app

from tests.fixtures import EmptySearch, StubSearch


def test_api_creates_project_and_pauses_for_meeting(settings):
    app = create_app(settings, search_adapter=StubSearch())
    with TestClient(app) as client:
        health = client.get("/health").json()
        assert health["status"] == "ok"
        assert health["model"] == settings.openai_model
        assert health["model_endpoint"] == "api.openai.com"
        created = client.post(
            "/api/projects",
            json={
                "title": "API research",
                "question": "Can the API preserve a structured evidence review workflow?",
                "success_criteria": ["Pause before synthesis"],
            },
        )
        assert created.status_code == 201
        project_id = created.json()["id"]
        started = client.post(f"/api/projects/{project_id}/runs")
        assert started.status_code == 202
        run_id = started.json()["id"]

        run = {}
        for _ in range(80):
            run = client.get(f"/api/runs/{run_id}").json()
            if run["status"] in {"needs_user", "completed", "failed", "cancelled"}:
                break
            time.sleep(0.05)
        assert run["status"] == "needs_user", run.get("error")
        decision = client.post(
            f"/api/runs/{run_id}/decision",
            json={"kind": "continue", "rationale": "Proceed with bounded synthesis."},
        )
        assert decision.status_code == 202

        for _ in range(80):
            run = client.get(f"/api/runs/{run_id}").json()
            if run["status"] == "completed":
                break
            time.sleep(0.05)
        assert run["status"] == "completed", run.get("error")
        report = client.get(f"/api/artifacts/{run['report_artifact_id']}")
        assert report.status_code == 200
        assert "[S1]" in report.text


def test_api_rejects_synthesis_without_evidence(settings):
    app = create_app(settings, search_adapter=EmptySearch())
    with TestClient(app) as client:
        created = client.post(
            "/api/projects",
            json={
                "title": "Empty retrieval",
                "question": "Can a research run publish when no evidence was retrieved?",
                "success_criteria": ["Do not publish empty synthesis"],
            },
        )
        project_id = created.json()["id"]
        run_id = client.post(f"/api/projects/{project_id}/runs").json()["id"]
        run = {}
        for _ in range(80):
            run = client.get(f"/api/runs/{run_id}").json()
            if run["status"] in {"needs_user", "failed"}:
                break
            time.sleep(0.05)
        assert run["status"] == "needs_user", run.get("error")

        decision = client.post(
            f"/api/runs/{run_id}/decision",
            json={"kind": "continue", "rationale": "Try to publish anyway."},
        )
        assert decision.status_code == 409
        assert "without at least one source" in decision.json()["detail"]
