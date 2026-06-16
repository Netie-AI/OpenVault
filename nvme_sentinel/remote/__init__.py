"""Remote collectors (Unraid NAS, etc.)."""

from nvme_sentinel.remote.unraid import (
    UnraidDiskHealth,
    UnraidSnapshot,
    collect_unraid_snapshot,
    discover_unraid_disks,
)

__all__ = [
    "UnraidDiskHealth",
    "UnraidSnapshot",
    "collect_unraid_snapshot",
    "discover_unraid_disks",
]
