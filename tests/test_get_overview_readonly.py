from pathlib import Path
from app.dissertation.service import DissertationService
from app.dissertation.repository import DissertationTargetNotFoundError
from app.memory.database import MemoryDatabase
import pytest

def test_get_overview_is_readonly(tmp_path: Path) -> None:
    database = MemoryDatabase(tmp_path / "nova.sqlite3")
    database.initialize()
    service = DissertationService(database)
    service.initialize()

    with pytest.raises(DissertationTargetNotFoundError):
        service.get_overview()

    with database.connection() as connection:
        count = connection.execute("SELECT COUNT(*) FROM dissertation_workspace").fetchone()[0]

    assert count == 0
