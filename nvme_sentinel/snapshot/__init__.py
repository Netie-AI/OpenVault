"""Read-only device telemetry snapshots for reports and VM host-proxy."""

from nvme_sentinel.snapshot.collect import collect_snapshot
from nvme_sentinel.snapshot.schema import DeviceSnapshot

__all__ = ["DeviceSnapshot", "collect_snapshot"]
