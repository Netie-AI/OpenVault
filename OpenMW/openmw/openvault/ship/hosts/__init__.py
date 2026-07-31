"""Places a built artifact can actually be published to.

Adapters are registered by id so the target picker, the deployment record and
the engine all refer to a host the same way. Adding a target means adding a
module here and one line in ``ADAPTERS`` — nothing in the engine changes.
"""

from __future__ import annotations

from openmw.openvault.ship.hosts.base import (
    DeployResult,
    DomainResult,
    HostAdapter,
    Preflight,
)
from openmw.openvault.ship.hosts.cloudflare_pages import CloudflarePagesAdapter

#: id -> adapter class. Kept deliberately small; a target that cannot really
#: deploy does not belong here, it belongs in the docs as a plan.
ADAPTERS: dict[str, type] = {
    CloudflarePagesAdapter.id: CloudflarePagesAdapter,
}


def adapter_ids() -> list[str]:
    return sorted(ADAPTERS)


__all__ = [
    "ADAPTERS",
    "CloudflarePagesAdapter",
    "DeployResult",
    "DomainResult",
    "HostAdapter",
    "Preflight",
    "adapter_ids",
]
