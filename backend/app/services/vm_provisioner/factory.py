"""VM provisioner factory with ordered fallback.

COMPUTE_PROVIDERS (env var) is a comma-separated priority list, e.g.:
  "huawei,turkcell,cloudsigma"

On each job submission, providers are tried in order. The first one that
passes is_healthy() is used. If a provider's create_instance() raises,
the next provider is tried before giving up entirely.

This gives us resilience against:
  - Planned maintenance windows
  - Regional outages (Izmir vs Istanbul)
  - Quota exhaustion on a single provider
"""
from __future__ import annotations

import logging

from app.config import settings
from app.services.vm_provisioner.base import VMProvisioner

logger = logging.getLogger(__name__)

_REGISTRY: dict[str, type[VMProvisioner]] = {}


def _register(name: str, cls: type[VMProvisioner]) -> None:
    _REGISTRY[name] = cls


def _load_providers() -> None:
    if _REGISTRY:
        return
    from app.services.vm_provisioner.huawei import HuaweiECSProvisioner
    from app.services.vm_provisioner.turkcell import TurkcellVCDProvisioner
    from app.services.vm_provisioner.cloudsigma import CloudSigmaProvisioner

    _register("huawei",     HuaweiECSProvisioner)
    _register("turkcell",   TurkcellVCDProvisioner)
    _register("cloudsigma", CloudSigmaProvisioner)


def get_provisioner() -> VMProvisioner:
    """Return the first healthy provider in COMPUTE_PROVIDERS order.

    Raises RuntimeError if all providers are unhealthy or misconfigured.
    """
    _load_providers()

    order = [p.strip() for p in settings.COMPUTE_PROVIDERS.split(",") if p.strip()]
    if not order:
        raise RuntimeError(
            "COMPUTE_PROVIDERS is empty — set it to e.g. 'huawei,turkcell,cloudsigma'"
        )

    errors: list[str] = []
    for name in order:
        cls = _REGISTRY.get(name)
        if cls is None:
            logger.warning("[factory] unknown provider '%s', skipping", name)
            errors.append(f"{name}: unknown provider")
            continue

        provisioner = cls()
        try:
            healthy = provisioner.is_healthy()
        except Exception as exc:
            healthy = False
            logger.warning("[factory] %s.is_healthy() raised: %s", name, exc)

        if healthy:
            logger.info("[factory] selected provider: %s", name)
            return provisioner

        errors.append(f"{name}: unhealthy")
        logger.warning("[factory] provider %s is unhealthy, trying next", name)

    raise RuntimeError(
        "All compute providers are unavailable. "
        f"Tried: {', '.join(order)}. "
        f"Errors: {'; '.join(errors)}"
    )


def get_provisioner_with_fallback(
    job_id: str,
    user_data: str,
    flavor: str,
) -> tuple[VMProvisioner, str]:
    """Try providers in order for create_instance, not just health check.

    Returns (provisioner, instance_id).  Useful when is_healthy() passes
    but the actual API call fails (quota exceeded, image not found, etc.).
    """
    _load_providers()

    order = [p.strip() for p in settings.COMPUTE_PROVIDERS.split(",") if p.strip()]
    last_exc: Exception | None = None

    for name in order:
        cls = _REGISTRY.get(name)
        if cls is None:
            continue
        provisioner = cls()
        if not provisioner.is_healthy():
            logger.warning("[factory] %s unhealthy, skipping for job %s", name, job_id)
            continue
        try:
            instance_id = provisioner.create_instance(job_id, user_data, flavor)
            logger.info("[factory] job %s → %s instance %s", job_id, name, instance_id)
            return provisioner, instance_id
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "[factory] %s.create_instance failed for job %s: %s — trying next",
                name, job_id, exc,
            )

    raise RuntimeError(
        f"All providers failed to create instance for job {job_id}. "
        f"Last error: {last_exc}"
    )
