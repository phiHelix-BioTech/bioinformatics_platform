from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class AssessmentResult:
    summary: str
    variants: list[dict] = field(default_factory=list)
    report_path: str | None = None
    report_sha256: str | None = None   # SHA-256 hex digest of the PDF bytes


class AssessmentRunner(ABC):
    @abstractmethod
    def run(
        self,
        job_id: str,
        variants: list[dict],
        workflow_config: dict | None = None,
    ) -> AssessmentResult:
        """Annotate VCF variants against mutation databases and generate a PDF report."""


def get_assessment_runner() -> AssessmentRunner:
    from .real import RealAssessmentRunner
    return RealAssessmentRunner()
