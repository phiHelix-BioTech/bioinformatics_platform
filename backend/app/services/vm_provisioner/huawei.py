"""Huawei Cloud ECS provisioner — Turkey North (tr-west-1).

Uses the official huaweicloud-sdk-python-v3.
Docs: https://support.huaweicloud.com/intl/en-us/api-ecs/

Instance lifecycle:
  create_instance  →  POST /v1/{project}/cloudservers
  get_status       →  GET  /v1/{project}/cloudservers/{id}
  terminate        →  POST /v1/{project}/cloudservers/delete
"""
from __future__ import annotations

import base64
import logging

from app.config import settings
from app.services.vm_provisioner.base import VMProvisioner

logger = logging.getLogger(__name__)


# Map generic tier names to Huawei ECS flavor IDs (tr-west-1).
# Adjust or override via HUAWEI_FLAVOR_* env vars.
_FLAVOR_MAP: dict[str, str] = {
    "small":    "c7n.large.4",      #  2 vCPU,  8 GB
    "standard": "c7n.2xlarge.4",    #  8 vCPU, 32 GB
    "large":    "c7n.4xlarge.4",    # 16 vCPU, 64 GB
    "xlarge":   "m7n.4xlarge.8",    # 16 vCPU, 128 GB
}


def _ecs_client():
    """Lazy import — only fails at call time, not import time."""
    try:
        from huaweicloudsdkcore.auth.credentials import BasicCredentials
        from huaweicloudsdkcore.exceptions import exceptions as hw_exc  # noqa: F401
        from huaweicloudsdkecs.v2 import EcsClient
        from huaweicloudsdkecs.v2.region.ecs_region import EcsRegion
    except ImportError as e:
        raise RuntimeError(
            "huaweicloudsdkecs not installed — run: "
            "pip install huaweicloudsdkcore huaweicloudsdkecs"
        ) from e

    credentials = BasicCredentials(
        ak=settings.HUAWEI_AK,
        sk=settings.HUAWEI_SK,
        project_id=settings.HUAWEI_PROJECT_ID,
    )
    return EcsClient.new_builder() \
        .with_credentials(credentials) \
        .with_region(EcsRegion.value_of(settings.HUAWEI_REGION)) \
        .build()


class HuaweiECSProvisioner(VMProvisioner):

    @property
    def provider_name(self) -> str:
        return "huawei"

    def is_healthy(self) -> bool:
        if not (settings.HUAWEI_AK and settings.HUAWEI_SK and settings.HUAWEI_PROJECT_ID):
            return False
        try:
            from huaweicloudsdkecs.v2 import ListFlavorsRequest
            _ecs_client().list_flavors(ListFlavorsRequest())
            return True
        except Exception as exc:
            logger.warning("[huawei] health check failed: %s", exc)
            return False

    def create_instance(self, job_id: str, user_data: str, flavor: str) -> str:
        from huaweicloudsdkecs.v2 import (
            CreateServersRequest,
            CreateServersRequestBody,
            PrePaidServer,
            PrePaidServerDataVolume,
            PrePaidServerEip,
            PrePaidServerEipBandwidth,
            PrePaidServerNic,
            PrePaidServerRootVolume,
        )

        resolved_flavor = _FLAVOR_MAP.get(flavor, flavor) or settings.HUAWEI_FLAVOR_DEFAULT
        ud_b64 = base64.b64encode(user_data.encode()).decode()

        server = PrePaidServer(
            name=f"bioplatform-job-{job_id[:12]}",
            flavor_ref=resolved_flavor,
            image_ref=settings.HUAWEI_IMAGE_ID,
            user_data=ud_b64,
            vpcid=settings.HUAWEI_VPC_ID,
            nics=[PrePaidServerNic(subnet_id=settings.HUAWEI_SUBNET_ID)],
            security_groups=[{"id": settings.HUAWEI_SECURITY_GROUP_ID}],
            root_volume=PrePaidServerRootVolume(
                volumetype="SSD",
                size=50,
            ),
            data_volumes=[
                PrePaidServerDataVolume(volumetype="SSD", size=200)
            ],
            count=1,
            # No key_name — SSH not needed; cloud-init drives everything
        )

        request = CreateServersRequest(body=CreateServersRequestBody(server=server))
        response = _ecs_client().create_servers(request)

        instance_ids = response.server_ids
        if not instance_ids:
            raise RuntimeError("[huawei] create_servers returned no server IDs")
        instance_id = instance_ids[0]
        logger.info("[huawei] created instance %s for job %s (flavor=%s)",
                    instance_id, job_id, resolved_flavor)
        return instance_id

    def get_instance_status(self, instance_id: str) -> str:
        from huaweicloudsdkecs.v2 import ShowServerRequest

        try:
            resp = _ecs_client().show_server(ShowServerRequest(server_id=instance_id))
            raw = (resp.server.status or "").upper()
            # Huawei ECS statuses: BUILD, ACTIVE, SHUTOFF, ERROR, DELETED
            _MAP = {
                "BUILD":   "pending",
                "ACTIVE":  "running",
                "SHUTOFF": "stopped",
                "DELETED": "stopped",
                "ERROR":   "error",
            }
            return _MAP.get(raw, "pending")
        except Exception as exc:
            logger.warning("[huawei] get_instance_status failed: %s", exc)
            return "error"

    def terminate_instance(self, instance_id: str) -> None:
        from huaweicloudsdkecs.v2 import (
            DeleteServersRequest,
            DeleteServersRequestBody,
            ServerId,
        )
        try:
            body = DeleteServersRequestBody(
                servers=[ServerId(id=instance_id)],
                delete_publicip=True,
                delete_volume=True,
            )
            _ecs_client().delete_servers(DeleteServersRequest(body=body))
            logger.info("[huawei] terminated instance %s", instance_id)
        except Exception as exc:
            logger.warning("[huawei] terminate_instance failed: %s", exc)
