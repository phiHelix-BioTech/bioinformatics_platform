from abc import ABC, abstractmethod


class NextflowRunner(ABC):
    @abstractmethod
    def run(
        self,
        pipeline_id: str,
        storage_key: str,
        file_type: str,
        job_id: str = "",
        storage_key_r2: str | None = None,
        workflow_config: dict | None = None,
    ) -> dict:
        """Run the specified nf-core pipeline and return the result dict."""


def get_nextflow_runner() -> NextflowRunner:
    from app.config import settings

    if settings.NEXTFLOW_BACKEND in ("aws", "awsbatch"):
        from app.services.nextflow.batch import AWSBatchNextflowRunner
        return AWSBatchNextflowRunner()

    if settings.NEXTFLOW_BACKEND == "local":
        from app.services.nextflow.local import LocalNextflowRunner
        return LocalNextflowRunner()

    if settings.NEXTFLOW_BACKEND == "turkishcloud":
        from app.services.nextflow.turkishcloud import TurkishCloudNextflowRunner
        return TurkishCloudNextflowRunner()

    from app.services.nextflow.mock import MockNextflowRunner
    return MockNextflowRunner()
