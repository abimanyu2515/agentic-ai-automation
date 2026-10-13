from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from backend.models.execution_result import ExecutionResult, ExecutionStatus
from backend.repository.project_repository import JSONProjectRepository


def test_json_execution_persists_project_configuration_snapshot():
    project_id = uuid4()
    snapshot = {
        "project_id": str(project_id),
        "target_url": "https://cricscore.example",
        "framework": "playwright",
        "credential_reference": str(project_id),
    }

    with TemporaryDirectory() as directory:
        repository = JSONProjectRepository(Path(directory) / "store.json")
        result = ExecutionResult(
            test_case_id=uuid4(),
            status=ExecutionStatus.PASSED,
            project_config_snapshot=snapshot,
        )
        repository.save_execution_result(project_id, result)

        loaded = repository.get_execution_result(project_id, result.id)

    assert loaded is not None
    assert loaded.project_config_snapshot == snapshot
