"""Snakemake runner on Turkish cloud VMs."""
from __future__ import annotations

import json
import logging
import time

from app.config import settings
from app.services.snakemake.base import SnakemakeRunner
from app.services.vm_provisioner.factory import get_provisioner_with_fallback
from app.services.vm_provisioner.runner import (
    build_user_data,
    collect_results,
    poll_until_done,
)

logger = logging.getLogger(__name__)


def _tier_to_flavor(tier: str | None) -> str:
    _MAP = {"basic": "small", "standard": "standard", "premium": "large"}
    return _MAP.get((tier or "standard").lower(), "standard")


class TurkishCloudSnakemakeRunner(SnakemakeRunner):

    def run(
        self,
        pipeline_id: str,
        storage_key: str,
        file_type: str,
        job_id: str = "",
        workflow_config: dict | None = None,
    ) -> dict:
        start = time.time()
        wf_cfg = workflow_config or {}
        endpoint_flag = (
            f"--endpoint-url {settings.S3_ENDPOINT_URL}"
            if settings.S3_ENDPOINT_URL else ""
        )
        input_uri  = f"s3://{settings.S3_BUCKET}/{storage_key}"
        output_uri = f"s3://{settings.S3_BUCKET}/smk-output/{job_id}/"
        wf_json = json.dumps(wf_cfg)

        pipeline_cmd = f"""
# Download input file
mkdir -p /data/{job_id}
aws s3 cp "{input_uri}" /data/{job_id}/input.{file_type} {endpoint_flag}

# Write workflow_config
cat > /tmp/wf_config.json <<'WFCFG'
{wf_json}
WFCFG

# Pull worker image
docker pull {settings.BIOSCRIPT_DOCKER_IMAGE}

# Run Snakemake inside Docker
docker run --rm \\
  -v /data/{job_id}:/data \\
  -v /outputs/{job_id}:/outputs \\
  -e WF_CONFIG_PATH=/tmp/wf_config.json \\
  {settings.BIOSCRIPT_DOCKER_IMAGE} \\
  snakemake --cores $(nproc) \\
    --use-conda \\
    --rerun-incomplete \\
    --nolock \\
    --directory /outputs \\
    --snakefile /app/Snakefile

# Upload results
aws s3 sync /outputs/{job_id}/ "{output_uri}" {endpoint_flag} --exclude ".snakemake/*"
"""

        flavor = _tier_to_flavor(wf_cfg.get("tier"))
        user_data = build_user_data(pipeline_cmd, job_id)
        provisioner, instance_id = get_provisioner_with_fallback(job_id, user_data, flavor)

        logger.info("[smk/turkishcloud] job=%s flavor=%s provider=%s",
                    job_id, flavor, provisioner.provider_name)
        try:
            poll_until_done(job_id, instance_id, provisioner)
        finally:
            provisioner.terminate_instance(instance_id)

        runtime = int(time.time() - start)
        return collect_results(job_id, runtime, provisioner.provider_name)
