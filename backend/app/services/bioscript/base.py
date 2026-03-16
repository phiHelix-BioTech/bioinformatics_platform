from abc import ABC, abstractmethod
from typing import Any, Optional


class BioScriptRunner(ABC):
    @abstractmethod
    def run(
        self,
        storage_key: str,
        file_type: str,
        job_id: str = "",
        workflow_config: Optional[dict[str, Any]] = None,
    ) -> dict:
        """Execute a BioScript bash workflow and return the result dict.

        workflow_config expected keys:
          - script: str       — bash script to execute
          - env: dict[str,str]— extra environment variables
        """


def get_bioscript_runner() -> BioScriptRunner:
    from app.config import settings

    if settings.BIOSCRIPT_BACKEND == "mock":
        from app.services.bioscript.mock import MockBioScriptRunner
        return MockBioScriptRunner()

    if settings.BIOSCRIPT_BACKEND == "local":
        from app.services.bioscript.local import LocalBioScriptRunner
        return LocalBioScriptRunner()

    if settings.BIOSCRIPT_BACKEND in ("aws", "awsbatch"):
        from app.services.bioscript.batch import AWSBatchBioScriptRunner
        return AWSBatchBioScriptRunner()

    raise NotImplementedError(
        f"BioScript backend '{settings.BIOSCRIPT_BACKEND}' is not implemented."
    )
