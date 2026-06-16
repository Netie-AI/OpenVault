"""Identify command builders and response parsers."""

from __future__ import annotations

from nvme_sentinel.hal.enums import AdminOpcode, CNSValue
from nvme_sentinel.hal.exceptions import AdminCommandError
from nvme_sentinel.hal.interface import AdminCommand, StorageInterface
from nvme_sentinel.models.identify import ControllerIdentify, NamespaceIdentify


def identify_controller(device: StorageInterface) -> ControllerIdentify:
    """Issue Identify Controller and parse the 4096-byte response."""
    # NVMe Base Spec 2.0c §5.17: cdw10[7:0] contains CNS for Identify.
    cmd = AdminCommand(
        opcode=int(AdminOpcode.IDENTIFY),
        cdw10=int(CNSValue.IDENTIFY_CONTROLLER),
        data_len=4096,
    )
    result = device.admin_passthru(cmd)
    if result.status != 0:
        raise AdminCommandError(
            status_code=result.status,
            opcode=int(AdminOpcode.IDENTIFY),
            message="Identify Controller failed",
        )
    return ControllerIdentify.from_bytes(result.data)


def identify_namespace(device: StorageInterface, nsid: int) -> NamespaceIdentify:
    """Issue Identify Namespace for one NSID and parse the response."""
    # Task contract: command-layer validation raises ValueError for nsid == 0.
    # NamespaceIdentify.from_bytes keeps its ParseError as a defensive parser guard.
    if nsid == 0:
        raise ValueError("nsid must be > 0")

    # NVMe Base Spec 2.0c §5.17: Identify Namespace uses CNS=0x00.
    cmd = AdminCommand(
        opcode=int(AdminOpcode.IDENTIFY),
        nsid=nsid,
        cdw10=int(CNSValue.IDENTIFY_NAMESPACE),
        data_len=4096,
    )
    result = device.admin_passthru(cmd)
    if result.status != 0:
        raise AdminCommandError(
            status_code=result.status,
            opcode=int(AdminOpcode.IDENTIFY),
            message=f"Identify Namespace failed for nsid={nsid}",
        )
    return NamespaceIdentify.from_bytes(result.data, nsid=nsid)


def active_namespace_list(device: StorageInterface, start_nsid: int = 0) -> list[int]:
    """Return zero-terminated active namespace list from Identify CNS 0x02."""
    # NVMe Base Spec 2.0c §5.17.2.3: list is u32 LE entries and zero-terminated.
    cmd = AdminCommand(
        opcode=int(AdminOpcode.IDENTIFY),
        nsid=start_nsid,
        cdw10=int(CNSValue.ACTIVE_NAMESPACE_LIST),
        data_len=4096,
    )
    result = device.admin_passthru(cmd)
    if result.status != 0:
        raise AdminCommandError(
            status_code=result.status,
            opcode=int(AdminOpcode.IDENTIFY),
            message="Active Namespace List failed",
        )

    nsids: list[int] = []
    for index in range(0, len(result.data), 4):
        nsid = int.from_bytes(result.data[index : index + 4], "little")
        if nsid == 0:
            break
        nsids.append(nsid)
    return nsids
