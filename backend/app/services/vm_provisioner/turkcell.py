"""Turkcell Bulut provisioner — VMware vCloud Director API.

Uses pyvcloud (VMware's official Python SDK for vCD).
  pip install pyvcloud

Turkcell Bulut exposes a standard vCD REST API. Each job gets its own vApp
(a lightweight VM container). Cloud-init is injected via guest customization
script — vCD's equivalent of EC2 user_data.

Docs: https://docs.turkcellbulut.com
vCD API: https://developer.vmware.com/apis/1601/vmware-cloud-director
"""
from __future__ import annotations

import logging
import time

from app.config import settings
from app.services.vm_provisioner.base import VMProvisioner

logger = logging.getLogger(__name__)


# Map generic tier → (cpu_count, memory_mb, disk_gb)
_SPEC_MAP: dict[str, tuple[int, int, int]] = {
    "small":    (2,   8_192,  50),
    "standard": (8,  32_768, 200),
    "large":    (16, 65_536, 400),
    "xlarge":   (16, 131_072, 400),
}


def _client():
    """Return an authenticated pyvcloud Client."""
    try:
        from pyvcloud.vcd.client import BasicLoginCredentials, Client
    except ImportError as e:
        raise RuntimeError(
            "pyvcloud not installed — run: pip install pyvcloud"
        ) from e

    client = Client(
        settings.TURKCELL_VCD_URL,
        api_version="36.3",
        verify_ssl_certs=True,
    )
    client.set_credentials(BasicLoginCredentials(
        user=settings.TURKCELL_VCD_USER,
        org=settings.TURKCELL_VCD_ORG,
        password=settings.TURKCELL_VCD_PASSWORD,
    ))
    return client


class TurkcellVCDProvisioner(VMProvisioner):

    @property
    def provider_name(self) -> str:
        return "turkcell"

    def is_healthy(self) -> bool:
        required = (
            settings.TURKCELL_VCD_URL,
            settings.TURKCELL_VCD_USER,
            settings.TURKCELL_VCD_PASSWORD,
            settings.TURKCELL_VCD_ORG,
            settings.TURKCELL_VCD_VDC,
        )
        if not all(required):
            return False
        try:
            _client()  # authenticates on instantiation
            return True
        except Exception as exc:
            logger.warning("[turkcell] health check failed: %s", exc)
            return False

    def create_instance(self, job_id: str, user_data: str, flavor: str) -> str:
        from pyvcloud.vcd.org import Org
        from pyvcloud.vcd.vapp import VApp
        from pyvcloud.vcd.vdc import VDC

        cpu, mem_mb, disk_gb = _SPEC_MAP.get(flavor, _SPEC_MAP["standard"])
        client = _client()

        org_resource = client.get_org()
        org = Org(client, resource=org_resource)
        vdc_resource = org.get_vdc(settings.TURKCELL_VCD_VDC)
        vdc = VDC(client, resource=vdc_resource)

        vapp_name = f"bio-{job_id[:12]}"

        # Instantiate vApp from catalog template
        vapp_resource = vdc.instantiate_vapp(
            name=vapp_name,
            catalog=settings.TURKCELL_VCD_CATALOG,
            template=settings.TURKCELL_VCD_TEMPLATE,
            network=settings.TURKCELL_VCD_NETWORK,
            memory=mem_mb,
            cpu=cpu,
            disk_size=disk_gb * 1024,  # pyvcloud expects MB
            accept_all_eulas=True,
        )
        vapp = VApp(client, resource=vapp_resource)

        # Inject cloud-init script via guest customization
        vm_name = vapp.get_vm_names()[0]
        vapp.customize_on_next_powercycle(vm_name)
        vapp.set_user_data(vm_name, user_data)

        # Power on
        task = vapp.power_on()
        client.get_task_monitor().wait_for_success(task)

        # Use vApp href as the instance ID (unique within the org)
        instance_id = vapp.resource.get("href")
        logger.info("[turkcell] created vApp %s (href=%s) for job %s",
                    vapp_name, instance_id, job_id)
        return instance_id

    def get_instance_status(self, instance_id: str) -> str:
        try:
            from pyvcloud.vcd.vapp import VApp
            client = _client()
            vapp = VApp(client, href=instance_id)
            status = vapp.get_status()
            # vCD statuses: 3=suspended, 4=powered_on, 8=powered_off
            _MAP = {
                "Powered on":  "running",
                "Powered off": "stopped",
                "Suspended":   "stopped",
                "4": "running",
                "8": "stopped",
            }
            return _MAP.get(str(status), "pending")
        except Exception as exc:
            logger.warning("[turkcell] get_instance_status failed: %s", exc)
            return "error"

    def terminate_instance(self, instance_id: str) -> None:
        try:
            from pyvcloud.vcd.vapp import VApp
            client = _client()
            vapp = VApp(client, href=instance_id)
            # Power off (ignore if already off)
            try:
                task = vapp.power_off()
                client.get_task_monitor().wait_for_success(task)
            except Exception:
                pass
            # Delete vApp
            task = vapp.delete()
            client.get_task_monitor().wait_for_success(task)
            logger.info("[turkcell] deleted vApp %s", instance_id)
        except Exception as exc:
            logger.warning("[turkcell] terminate_instance failed: %s", exc)
