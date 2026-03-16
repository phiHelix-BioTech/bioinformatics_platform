from abc import ABC, abstractmethod


from typing import Any, Optional


class SnakemakeRunner(ABC):
    @abstractmethod
    def run(
        self,
        pipeline_id: str,
        storage_key: str,
        file_type: str,
        job_id: str = "",
        workflow_config: Optional[dict[str, Any]] = None,
    ) -> dict:
        """Run the Snakemake workflow and return the result dict."""


def get_snakemake_runner() -> SnakemakeRunner:
    from app.config import settings

    if settings.SNAKEMAKE_BACKEND == "mock":
        from app.services.snakemake.mock import MockSnakemakeRunner
        return MockSnakemakeRunner()

    if settings.SNAKEMAKE_BACKEND == "local":
        from app.services.snakemake.local import LocalSnakemakeRunner
        return LocalSnakemakeRunner()

    if settings.SNAKEMAKE_BACKEND in ("aws", "awsbatch"):
        from app.services.snakemake.batch import AWSBatchSnakemakeRunner
        return AWSBatchSnakemakeRunner()

    if settings.SNAKEMAKE_BACKEND == "turkishcloud":
        from app.services.snakemake.turkishcloud import TurkishCloudSnakemakeRunner
        return TurkishCloudSnakemakeRunner()

    raise NotImplementedError(
        f"Snakemake backend '{settings.SNAKEMAKE_BACKEND}' is not implemented."
    )
