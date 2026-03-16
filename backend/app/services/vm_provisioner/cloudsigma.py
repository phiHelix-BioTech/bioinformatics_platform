"""CloudSigma / Siaflex provisioner — Izmir, Turkey.

Uses the CloudSigma REST API v2 with HTTP Basic authentication.
API docs: https://docs.cloudsigma.com/en/latest/

CloudSigma bills per-second and exposes a clean REST API with no SDK
dependency — plain requests is sufficient.

Storage note: CloudSigma has no native S3-compatible storage. VMs must be
configured to write results to whichever S3-compatible endpoint is set in
S3_ENDPOINT_URL (Huawei OBS or Turkcell nDepo).
"""
from __future__ import annotations

import base64
import logging
import uuid

import requests

from app.config import settings
from app.services.vm_provisioner.base import VMProvisioner

logger = logging.getLogger(__name__)

# Map generic tier → (cpu_mhz, mem_mb, ssd_gb)
_SPEC_MAP: dict[str, tuple[int, int, int]] = {
    "small":    (4_000,   8_192,  30),
    "standard": (16_000, 32_768, 100),
    "large":    (32_000, 65_536, 200),
    "xlarge":   (48_000, 131_072, 300),
}

_TIMEOUT = 15  # seconds for API calls


class CloudSigmaProvisioner(VMProvisioner):

    def _auth(self) -> tuple[str, str]:
        return (settings.CLOUDSIGMA_USERNAME, settings.CLOUDSIGMA_PASSWORD)

    def _url(self, path: str) -> str:
        base = settings.CLOUDSIGMA_API_ENDPOINT.rstrip("/")
        return f"{base}/{path.lstrip('/')}"

    def _get(self, path: str) -> dict:
        r = requests.get(self._url(path), auth=self._auth(), timeout=_TIMEOUT)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, body: dict) -> dict:
        r = requests.post(
            self._url(path), json=body, auth=self._auth(), timeout=_TIMEOUT
        )
        r.raise_for_status()
        return r.json()

    def _delete(self, path: str) -> None:
        r = requests.delete(self._url(path), auth=self._auth(), timeout=_TIMEOUT)
        r.raise_for_status()

    # ── VMProvisioner interface ──────────────────────────────────────────

    @property
    def provider_name(self) -> str:
        return "cloudsigma"

    def is_healthy(self) -> bool:
        if not (settings.CLOUDSIGMA_USERNAME and settings.CLOUDSIGMA_PASSWORD):
            return False
        try:
            self._get("servers/?limit=1")
            return True
        except Exception as exc:
            logger.warning("[cloudsigma] health check failed: %s", exc)
            return False

    def create_instance(self, job_id: str, user_data: str, flavor: str) -> str:
        cpu_mhz, mem_mb, ssd_gb = _SPEC_MAP.get(flavor, _SPEC_MAP["standard"])

        # CloudSigma injects user_data as a meta field; the OS reads it via
        # cloud-init from the config drive (not HTTP metadata endpoint).
        ud_b64 = base64.b64encode(user_data.encode()).decode()

        # Create a drive for the OS first (CloudSigma uses a separate drive object)
        drive_resp = self._post("drives/", {
            "objects": [{
                "name": f"bio-drive-{job_id[:8]}",
                "size": ssd_gb * 1024 * 1024 * 1024,  # bytes
                "media": "disk",
            }]
        })
        drive_uuid = drive_resp["objects"][0]["uuid"]

        # Create the server
        server_resp = self._post("servers/", {
            "objects": [{
                "name": f"bioplatform-job-{job_id[:12]}",
                "cpu": cpu_mhz,
                "mem": mem_mb * 1024 * 1024,  # bytes
                "drives": [{"boot_order": 1, "dev_channel": "0:0",
                             "device": "virtio", "drive": {"uuid": drive_uuid}}],
                "nics": [{"ip_v4_conf": {"conf": "dhcp"}, "model": "virtio"}],
                "meta": {"cloudinit-user-data": ud_b64},
                "tags": [f"job={job_id}"],
            }]
        })
        server_uuid = server_resp["objects"][0]["uuid"]

        # Start the server
        self._post(f"servers/{server_uuid}/action/", {"action": "start"})

        logger.info("[cloudsigma] created server %s for job %s (cpu=%d MHz, mem=%d MB)",
                    server_uuid, job_id, cpu_mhz, mem_mb)
        return server_uuid

    def get_instance_status(self, instance_id: str) -> str:
        try:
            data = self._get(f"servers/{instance_id}/")
            status = data.get("status", "").lower()
            _MAP = {
                "starting":  "pending",
                "running":   "running",
                "stopping":  "running",
                "stopped":   "stopped",
                "paused":    "stopped",
                "error":     "error",
            }
            return _MAP.get(status, "pending")
        except Exception as exc:
            logger.warning("[cloudsigma] get_instance_status failed: %s", exc)
            return "error"

    def terminate_instance(self, instance_id: str) -> None:
        try:
            # Stop first (graceful)
            try:
                self._post(f"servers/{instance_id}/action/", {"action": "stop"})
            except Exception:
                pass
            # Delete server
            self._delete(f"servers/{instance_id}/?recurse=all_drives")
            logger.info("[cloudsigma] deleted server %s", instance_id)
        except Exception as exc:
            logger.warning("[cloudsigma] terminate_instance failed: %s", exc)
