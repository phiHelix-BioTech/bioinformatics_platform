"""BioScript runner on Turkish cloud VMs."""
from __future__ import annotations

import logging
import time

from app.config import settings
from app.services.bioscript.base import BioScriptRunner
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


class TurkishCloudBioScriptRunner(BioScriptRunner):

    def run(
        self,
        storage_key: str,
        file_type: str,
        job_id: str = "",
        workflow_config: dict | None = None,
    ) -> dict:
        start = time.time()
        wf_cfg = workflow_config or {}
        script = wf_cfg.get("script", "echo 'No script provided'")
        extra_env: dict[str, str] = wf_cfg.get("env", {})

        endpoint_flag = (
            f"--endpoint-url {settings.S3_ENDPOINT_URL}"
            if settings.S3_ENDPOINT_URL else ""
        )
        input_uri  = f"s3://{settings.S3_BUCKET}/{storage_key}"
        output_uri = f"s3://{settings.S3_BUCKET}/bioscript-output/{job_id}/"

        pipeline_cmd = f"""
# Download input file
mkdir -p /data/{job_id} /output/{job_id}
aws s3 cp "{input_uri}" /data/{job_id}/input.{file_type} {endpoint_flag}

# Write and run user script
cat > /tmp/user_script.sh <<'USERSCRIPT'
#!/bin/bash
set -euo pipefail
INPUT="/data/{job_id}/input.{file_type}"
OUTPUT_DIR="/output/{job_id}"
{script}
USERSCRIPT
chmod +x /tmp/user_script.sh

# Pull worker image and run script in sandboxed container
docker pull {settings.BIOSCRIPT_DOCKER_IMAGE}
docker run --rm \\
  -v /data/{job_id}:/data/{job_id} \\
  -v /output/{job_id}:/output/{job_id} \\
  -v /tmp/user_script.sh:/tmp/user_script.sh \\
  --memory=8g \\
  --cpus=$(nproc) \\
  --read-only --tmpfs /tmp:exec \\
  {settings.BIOSCRIPT_DOCKER_IMAGE} \\
  bash /tmp/user_script.sh

# Upload results
aws s3 sync /output/{job_id}/ "{output_uri}" {endpoint_flag}
"""

        flavor = _tier_to_flavor(wf_cfg.get("tier"))
        user_data = build_user_data(pipeline_cmd, job_id, extra_env=extra_env)
        provisioner, instance_id = get_provisioner_with_fallback(job_id, user_data, flavor)

        logger.info("[bio/turkishcloud] job=%s flavor=%s provider=%s",
                    job_id, flavor, provisioner.provider_name)
        try:
            poll_until_done(job_id, instance_id, provisioner)
        finally:
            provisioner.terminate_instance(instance_id)

        runtime = int(time.time() - start)
        return collect_results(job_id, runtime, provisioner.provider_name)
