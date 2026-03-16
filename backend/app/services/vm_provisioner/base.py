"""Abstract VM provisioner interface.

Every Turkish cloud provider implements this contract. The factory selects
the first healthy provider and falls back to the next on error.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class VMProvisioner(ABC):
    """Lifecycle interface for ephemeral compute instances."""

    # ── required ────────────────────────────────────────────────────────

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Short identifier used in logs and DB records, e.g. 'huawei'."""

    @abstractmethod
    def is_healthy(self) -> bool:
        """Quick API ping.  Return False if the provider is unreachable."""

    @abstractmethod
    def create_instance(
        self,
        job_id: str,
        user_data: str,
        flavor: str,
    ) -> str:
        """Provision a VM and return the provider's instance ID.

        ``user_data`` is a shell script (cloud-init) that runs the pipeline
        and shuts the VM down when done.  ``flavor`` is a provider-specific
        size identifier (e.g. 'c7n.2xlarge.4' for Huawei, 'standard' mapped
        internally by each provider).
        """

    @abstractmethod
    def get_instance_status(self, instance_id: str) -> str:
        """Return one of: 'pending' | 'running' | 'stopped' | 'error'."""

    @abstractmethod
    def terminate_instance(self, instance_id: str) -> None:
        """Delete / power-off and release the instance.  Best-effort."""
