from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import FastAPI, File, HTTPException, Request, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from oplab.agents.workflow import ResearchWorkflow, RunManager
from oplab.config import Settings, get_settings
from oplab.db import Database
from oplab.domain.commands import RecordDecision, make_idempotency_key
from oplab.domain.enums import MeetingDecisionKind, RunStatus
from oplab.domain.queries import QueryService
from oplab.domain.service import DomainService, PolicyDeniedError
from oplab.evidence.ingestor import EvidenceIngestor
from oplab.harness.model import ModelGateway
from oplab.retrieval.base import SearchAdapter
from oplab.retrieval.ingestion import candidate_from_bytes, candidate_from_url
from oplab.retrieval.openalex import OpenAlexSearch
from sse_starlette.sse import EventSourceResponse

from oplab_api.schemas import AddUrlRequest, CreateProjectRequest, MeetingDecisionRequest


def create_app(
    settings: Settings | None = None,
    *,
    search_adapter: SearchAdapter | None = None,
) -> FastAPI:
    configuration = settings or get_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        configuration.ensure_directories()
        database = Database(configuration)
        await database.create_schema()
        domain = DomainService(database.sessions)
        queries = QueryService(database.sessions)
        workflow = ResearchWorkflow(
            settings=configuration,
            domain=domain,
            search=search_adapter or OpenAlexSearch(mailto=configuration.openalex_mailto),
            model=ModelGateway(configuration),
        )
        await workflow.start()
        manager = RunManager(workflow, domain)
        application.state.settings = configuration
        application.state.database = database
        application.state.domain = domain
        application.state.queries = queries
        application.state.workflow = workflow
        application.state.manager = manager
        await manager.recover()
        try:
            yield
        finally:
            await manager.close()
            await workflow.close()
            await database.dispose()

    application = FastAPI(
        title="Oplab API",
        version="0.1.0",
        summary="Evidence-first control plane for a one-person research lab",
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=configuration.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    _register_routes(application)
    return application


def _register_routes(application: FastAPI) -> None:
    @application.get("/health")
    async def health(request: Request) -> dict:
        settings: Settings = request.app.state.settings
        return {
            "status": "ok",
            "service": "oplab-api",
            "model_enabled": bool(settings.openai_api_key),
            "model": settings.openai_model,
            "model_endpoint": urlsplit(settings.openai_base_url).netloc,
        }

    @application.get("/api/projects")
    async def list_projects(request: Request) -> list[dict]:
        return await request.app.state.queries.list_projects()

    @application.post("/api/projects", status_code=status.HTTP_201_CREATED)
    async def create_project(payload: CreateProjectRequest, request: Request) -> dict:
        project = await request.app.state.domain.create_project(
            title=payload.title,
            question=payload.question,
            success_criteria=payload.success_criteria,
        )
        return {
            "id": project.id,
            "title": project.title,
            "question": payload.question,
            "status": project.status,
        }

    @application.get("/api/projects/{project_id}")
    async def project_dashboard(project_id: str, request: Request) -> dict:
        try:
            return await request.app.state.queries.project_dashboard(project_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @application.get("/api/projects/{project_id}/evidence")
    async def project_evidence(project_id: str, request: Request) -> dict:
        return await request.app.state.queries.evidence(project_id)

    @application.post(
        "/api/projects/{project_id}/sources/upload", status_code=status.HTTP_201_CREATED
    )
    async def upload_source(
        project_id: str,
        request: Request,
        file: Annotated[UploadFile, File()],
    ) -> dict:
        settings_value: Settings = request.app.state.settings
        data = await file.read(settings_value.max_source_bytes + 1)
        if len(data) > settings_value.max_source_bytes:
            raise HTTPException(status_code=413, detail="Source exceeds the configured size limit")
        filename = Path(file.filename or "source.txt").name
        digest = hashlib.sha256(data).hexdigest()
        directory = settings_value.oplab_upload_root / project_id
        directory.mkdir(parents=True, exist_ok=True)
        final_path = directory / f"{digest[:20]}{Path(filename).suffix.lower()}"
        with tempfile.NamedTemporaryFile(
            "wb", dir=directory, delete=False, suffix=".tmp"
        ) as handle:
            handle.write(data)
            temporary_path = Path(handle.name)
        os.replace(temporary_path, final_path)
        try:
            candidate = candidate_from_bytes(
                filename, data, f"oplab://uploads/{project_id}/{final_path.name}"
            )
            result = await EvidenceIngestor(request.app.state.domain).ingest(
                project_id=project_id,
                run_id="manual",
                actor="user",
                candidate=candidate,
            )
        except (ValueError, LookupError) as exc:
            final_path.unlink(missing_ok=True)
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"source_id": result.entity_id, "replayed": result.replayed, "filename": filename}

    @application.post("/api/projects/{project_id}/sources/url", status_code=status.HTTP_201_CREATED)
    async def add_url_source(project_id: str, payload: AddUrlRequest, request: Request) -> dict:
        try:
            candidate = await candidate_from_url(
                str(payload.url), max_bytes=request.app.state.settings.max_source_bytes
            )
            result = await EvidenceIngestor(request.app.state.domain).ingest(
                project_id=project_id,
                run_id="manual",
                actor="user",
                candidate=candidate,
            )
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"Unable to ingest URL: {exc}") from exc
        return {"source_id": result.entity_id, "replayed": result.replayed}

    @application.post("/api/projects/{project_id}/runs", status_code=status.HTTP_202_ACCEPTED)
    async def start_run(project_id: str, request: Request) -> dict:
        try:
            run = await request.app.state.domain.start_run(project_id)
            await request.app.state.manager.enqueue(run.id)
            return await request.app.state.queries.run(run.id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @application.get("/api/runs/{run_id}")
    async def get_run(run_id: str, request: Request) -> dict:
        try:
            return await request.app.state.queries.run(run_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @application.post("/api/runs/{run_id}/decision", status_code=status.HTTP_202_ACCEPTED)
    async def decide_run(run_id: str, payload: MeetingDecisionRequest, request: Request) -> dict:
        try:
            run = await request.app.state.queries.run(run_id)
            if run["status"] != RunStatus.NEEDS_USER.value:
                raise HTTPException(
                    status_code=409, detail="Run is not waiting for a user decision"
                )
            meeting = run.get("meeting")
            if not meeting:
                raise HTTPException(status_code=409, detail="Run has no pending meeting")
            packet = meeting.get("evidence_packet") or {}
            if payload.kind == MeetingDecisionKind.CONTINUE.value and (
                int(packet.get("source_count", 0)) < 1 or int(packet.get("claim_count", 0)) < 1
            ):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Cannot continue to synthesis without at least one source "
                        "and one evidence-bound claim"
                    ),
                )
            decision_data = payload.model_dump()
            digest = hashlib.sha256(
                json.dumps(decision_data, sort_keys=True).encode("utf-8")
            ).hexdigest()
            result = await request.app.state.domain.execute(
                RecordDecision(
                    project_id=run["project_id"],
                    idempotency_key=make_idempotency_key(
                        run["project_id"], run_id, "RecordDecision", digest
                    ),
                    actor="user",
                    meeting_id=meeting["id"],
                    kind=MeetingDecisionKind(payload.kind),
                    rationale=payload.rationale,
                    direction=payload.direction,
                    dissent=payload.dissent,
                )
            )
            await request.app.state.manager.enqueue(run_id, resume=decision_data)
            return {"decision_id": result.entity_id, "run_id": run_id, "accepted": True}
        except HTTPException:
            raise
        except (LookupError, ValueError, PolicyDeniedError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @application.get("/api/meetings/{meeting_id}")
    async def get_meeting(meeting_id: str, request: Request) -> dict:
        try:
            return await request.app.state.queries.meeting(meeting_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @application.get("/api/artifacts/{artifact_id}")
    async def get_artifact(artifact_id: str, request: Request) -> FileResponse:
        try:
            artifact = await request.app.state.queries.artifact(artifact_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        path = Path(artifact.path)
        if not await asyncio.to_thread(path.is_file):
            raise HTTPException(status_code=410, detail="Artifact file is no longer available")
        return FileResponse(path, media_type=artifact.media_type, filename=path.name)

    @application.get("/api/projects/{project_id}/events")
    async def events(project_id: str, request: Request, after: int = 0) -> EventSourceResponse:
        async def stream() -> AsyncIterator[dict[str, str]]:
            cursor = after
            while not await request.is_disconnected():
                values = await request.app.state.domain.list_events(project_id, cursor)
                for event in values:
                    cursor = event.sequence
                    yield {
                        "id": str(event.sequence),
                        "event": event.event_type,
                        "data": json.dumps(
                            {
                                "sequence": event.sequence,
                                "aggregate_type": event.aggregate_type,
                                "aggregate_id": event.aggregate_id,
                                "payload": event.payload,
                                "occurred_at": event.occurred_at.isoformat(),
                            }
                        ),
                    }
                await asyncio.sleep(1)

        return EventSourceResponse(stream())


app = create_app()
