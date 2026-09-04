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
from openmw.openvault.ship.hosts.coolify import CoolifyAdapter
from openmw.openvault.ship.hosts.netlify import NetlifyAdapter
from openmw.openvault.ship.hosts.spaceship_ftp import SpaceshipFtpAdapter
from openmw.openvault.ship.hosts.vps_ssh import VpsSshAdapter

#: id -> adapter class. Kept deliberately small; a target that cannot really
#: deploy does not belong here, it belongs in the docs as a plan.
ADAPTERS: dict[str, type] = {
    CloudflarePagesAdapter.id: CloudflarePagesAdapter,
    CoolifyAdapter.id: CoolifyAdapter,
    NetlifyAdapter.id: NetlifyAdapter,
    SpaceshipFtpAdapter.id: SpaceshipFtpAdapter,
    VpsSshAdapter.id: VpsSshAdapter,
}


def adapter_ids() -> list[str]:
    return sorted(ADAPTERS)


def needs_local_build(target: str) -> bool:
    """Does publishing to this target require this machine to build first?

    Ask the adapter, do not guess per caller. Cloudflare Pages and Netlify
    upload a directory, so without a build their deploy step refuses with
    "nothing was built" and the user has no way to fix it from the UI. Coolify
    builds from its own source and the VPS adapter builds on the box, so forcing
    a local build there is pure wasted minutes.

    Unknown targets return False: ``local_demo`` and ``aws_guide`` have no
    adapter and publish nothing.
    """
    adapter = ADAPTERS.get(target)
    return bool(getattr(adapter, "needs_local_build", False))


__all__ = [
    "ADAPTERS",
    "CloudflarePagesAdapter",
    "CoolifyAdapter",
    "DeployResult",
    "DomainResult",
    "HostAdapter",
    "NetlifyAdapter",
    "Preflight",
    "SpaceshipFtpAdapter",
    "VpsSshAdapter",
    "adapter_ids",
    "needs_local_build",
]
