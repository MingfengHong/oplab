from pathlib import Path

import pytest
from oplab.config import Settings
from oplab.db import Database
from oplab.domain.queries import QueryService
from oplab.domain.service import DomainService


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'domain.sqlite').as_posix()}",
        checkpoint_database_url=str(tmp_path / "checkpoints.sqlite"),
        oplab_artifact_root=tmp_path / "artifacts",
        oplab_upload_root=tmp_path / "uploads",
        openai_api_key=None,
        retrieval_limit=4,
    )


@pytest.fixture
async def services(settings: Settings):
    settings.ensure_directories()
    database = Database(settings)
    await database.create_schema()
    yield database, DomainService(database.sessions), QueryService(database.sessions)
    await database.dispose()
