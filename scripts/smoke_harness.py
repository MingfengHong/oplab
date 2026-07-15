from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import tempfile
from pathlib import Path

from oplab.agents.workflow import ResearchWorkflow, RunManager
from oplab.config import Settings
from oplab.db import Database
from oplab.domain.commands import RecordDecision, make_idempotency_key
from oplab.domain.enums import MeetingDecisionKind
from oplab.domain.queries import QueryService
from oplab.domain.service import DomainService
from oplab.harness.model import ModelGateway
from oplab.retrieval.openalex import OpenAlexSearch


async def run_smoke(question: str) -> dict:
    configured = Settings()
    with tempfile.TemporaryDirectory(prefix="oplab-harness-") as directory:
        root = Path(directory)
        settings = configured.model_copy(
            update={
                "database_url": f"sqlite+aiosqlite:///{(root / 'domain.sqlite').as_posix()}",
                "checkpoint_database_url": str(root / "checkpoints.sqlite"),
                "oplab_artifact_root": root / "artifacts",
                "oplab_upload_root": root / "uploads",
            }
        )
        settings.ensure_directories()
        database = Database(settings)
        await database.create_schema()
        domain = DomainService(database.sessions)
        queries = QueryService(database.sessions)
        workflow = ResearchWorkflow(
            settings=settings,
            domain=domain,
            search=OpenAlexSearch(mailto=settings.openalex_mailto),
            model=ModelGateway(settings),
        )
        await workflow.start()
        manager = RunManager(workflow, domain)
        try:
            project = await domain.create_project(
                title="Harness smoke test",
                question=question,
                success_criteria=[
                    "Retrieve multiple scholarly sources",
                    "Bind claims to source passages",
                    "Search for counterevidence",
                ],
            )
            run = await domain.start_run(project.id)
            await manager.enqueue(run.id)
            await manager.wait(run.id)
            waiting = await queries.run(run.id)
            if waiting["status"] != "needs_user":
                raise RuntimeError(f"Harness did not reach a user checkpoint: {waiting}")
            evidence = await queries.evidence(project.id)
            if not evidence["sources"] or not evidence["claims"]:
                raise RuntimeError("Harness reached review without usable evidence")

            decision = {
                "kind": "continue",
                "rationale": "Smoke test approves a reviewed synthesis.",
                "direction": None,
                "dissent": [],
            }
            digest = hashlib.sha256(json.dumps(decision, sort_keys=True).encode()).hexdigest()
            await domain.execute(
                RecordDecision(
                    project_id=project.id,
                    idempotency_key=make_idempotency_key(
                        project.id, run.id, "RecordDecision", digest
                    ),
                    actor="user",
                    meeting_id=waiting["meeting"]["id"],
                    kind=MeetingDecisionKind.CONTINUE,
                    rationale=decision["rationale"],
                )
            )
            await manager.enqueue(run.id, resume=decision)
            await manager.wait(run.id)
            completed = await queries.run(run.id)
            if completed["status"] != "completed":
                raise RuntimeError(f"Harness did not complete: {completed}")
            artifact = await queries.artifact(completed["report_artifact_id"])
            memo = await asyncio.to_thread(Path(artifact.path).read_text, encoding="utf-8")
            citations = sorted(set(re.findall(r"\[S\d+\]", memo)))
            if not citations:
                raise RuntimeError("Published memo has no traceable citations")
            state = completed["state"]
            return {
                "status": completed["status"],
                "model_enabled": bool(settings.openai_api_key),
                "model": settings.openai_model if settings.openai_api_key else "deterministic",
                "sources": len(evidence["sources"]),
                "claims": len(evidence["claims"]),
                "opposing_links": sum(item["stance"] == "opposes" for item in evidence["links"]),
                "trajectory_entries": len(state.get("trajectory", [])),
                "iterations": state.get("usage", {}).get("iterations", 0),
                "searches": state.get("usage", {}).get("searches", 0),
                "review": state.get("review", {}),
                "citations": citations,
                "memo_characters": len(memo),
            }
        finally:
            await manager.close()
            await workflow.close()
            await database.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run an isolated end-to-end Oplab harness test")
    parser.add_argument(
        "--question",
        default=(
            "How does maintainer diversity affect recovery after contributor loss in "
            "open-source software projects?"
        ),
    )
    args = parser.parse_args()
    result = asyncio.run(run_smoke(args.question))
    # ASCII escaping keeps the report printable in legacy Windows code pages.
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
