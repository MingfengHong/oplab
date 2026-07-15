import time

from fastapi.testclient import TestClient
from oplab_api.main import create_app

from tests.fixtures import StubSearch


def test_api_creates_project_and_pauses_for_meeting(settings):
    app = create_app(settings, search_adapter=StubSearch())
    with TestClient(app) as client:
        assert client.get("/health").json()["status"] == "ok"
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
