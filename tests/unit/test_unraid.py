"""Unraid remote collector tests (SSH mocked)."""

from __future__ import annotations

import json

import pytest

from nvme_sentinel.remote.unraid import collect_unraid_snapshot, discover_unraid_disks


def test_discover_unraid_disks(monkeypatch: pytest.MonkeyPatch) -> None:
    lsblk = {
        "blockdevices": [
            {
                "name": "nvme0n1",
                "type": "disk",
                "model": "CACHE",
                "serial": "1",
                "tran": "nvme",
            },
            {"name": "sdg", "type": "disk", "model": "HDD", "serial": "2", "tran": "sata"},
        ]
    }

    def fake_ssh(host: str, command: str, **kwargs: object) -> tuple[int, str, str]:
        return 0, json.dumps(lsblk), ""

    monkeypatch.setattr("nvme_sentinel.remote.unraid._ssh_run", fake_ssh)
    disks = discover_unraid_disks("nas.local")
    assert len(disks) == 2
    assert disks[0].is_nvme is True
    assert disks[1].is_nvme is False


def test_collect_unraid_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    lsblk = {"blockdevices": [{"name": "sda", "type": "disk", "model": "DISK", "tran": "sata"}]}
    smartctl_out = json.dumps({"smart_status": {"passed": True}})

    def fake_ssh(host: str, command: str, **kwargs: object) -> tuple[int, str, str]:
        if "lsblk" in command:
            return 0, json.dumps(lsblk), ""
        if "smartctl" in command:
            return 0, smartctl_out, ""
        return 0, "", ""

    monkeypatch.setattr("nvme_sentinel.remote.unraid._ssh_run", fake_ssh)
    snap = collect_unraid_snapshot("nas.local")
    assert snap.host == "nas.local"
    assert len(snap.disks) == 1
    assert snap.disks[0].smart_json is not None
