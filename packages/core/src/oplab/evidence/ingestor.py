from oplab.domain.commands import AddEvidence, make_idempotency_key
from oplab.domain.service import CommandResult, DomainService
from oplab.retrieval.base import SourceCandidate
from oplab.retrieval.ingestion import content_hash, passage_payload


class EvidenceIngestor:
    def __init__(self, domain: DomainService):
        self.domain = domain

    async def ingest(
        self,
        *,
        project_id: str,
        run_id: str,
        actor: str,
        candidate: SourceCandidate,
        generation: int = 0,
    ) -> CommandResult:
        digest = content_hash(candidate)
        return await self.domain.execute(
            AddEvidence(
                project_id=project_id,
                idempotency_key=make_idempotency_key(
                    project_id, run_id, "AddEvidence", digest, generation
                ),
                actor=actor,
                source_type=candidate.source_type,
                title=candidate.title,
                uri=candidate.uri,
                doi=candidate.doi,
                authors=candidate.authors,
                published_at=candidate.published_at,
                content_hash=digest,
                quality=candidate.quality,
                license_status=candidate.license_status,
                passages=passage_payload(candidate),
            )
        )
