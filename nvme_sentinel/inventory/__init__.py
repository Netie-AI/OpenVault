"""OS storage device inventory for nvme-sentinel list-devices."""

from nvme_sentinel.inventory.discovery import list_devices
from nvme_sentinel.inventory.models import InventoryDevice

__all__ = ["InventoryDevice", "list_devices"]
