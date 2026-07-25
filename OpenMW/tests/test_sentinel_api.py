"""HTTP contract tests for the Sentinel router (mounted in isolation)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openmw.openvault.routers.sentinel import router as sentinel_router
from openmw.openvault.sentinel.fixtures import MOCK_DEVICE_PATH

_DESTRUCTIVE_FRAGMENTS = (
    "firmware",
    "secure-erase",
    "secure_erase",
    "erase",
    "over-provision",
    "cache-flush",
    "cache_clean",
)


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(sentinel_router)
    return TestClient(app)


def _mock_endpoints() -> list[tuple[str, str]]:
    """(method, path) pairs exercised with ?mock=true or body mock=true."""
    return [
        ("GET", "/api/sentinel/devices?mock=true"),
        ("GET", "/api/sentinel/smart?mock=true"),
        ("GET", "/api/sentinel/identify?mock=true"),
        ("GET", "/api/sentinel/errors?mock=true"),
        ("GET", "/api/sentinel/capabilities?mock=true"),
    ]


@pytest.mark.parametrize("method,path", _mock_endpoints())
def test_mock_query_returns_source_mock(method: str, path: str) -> None:
    response = _client().request(method, path)
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "mock"
    assert "degraded_reason" in body


@pytest.mark.parametrize(
    "path,payload",
    [
        ("/api/sentinel/snapshot", {"mock": True}),
        ("/api/sentinel/bench", {"mock": True}),
        ("/api/observe/trace", {"mock": True}),
    ],
)
def test_mock_body_returns_source_mock(path: str, payload: dict[str, object]) -> None:
    response = _client().post(path, json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "mock"


def test_devices_mock_lists_fixture_device() -> None:
    body = _client().get("/api/sentinel/devices?mock=true").json()
    assert body["ok"] is True
    assert body["count"] == 1
    assert body["devices"][0]["device_path"] == MOCK_DEVICE_PATH


def test_capabilities_mock_reports_fixture_adapter() -> None:
    body = _client().get("/api/sentinel/capabilities?mock=true").json()
    assert body["source"] == "mock"
    assert body["passthrough_ok"] is True
    assert "fixture" in (body.get("degraded_reason") or "").lower()
    not_impl = " ".join(body.get("not_implemented", [])).lower()
    assert "firmware" in not_impl
    assert "secure erase" in not_impl


def test_no_destructive_routes_exposed() -> None:
    paths = {route.path for route in sentinel_router.routes if hasattr(route, "path")}
    for path in paths:
        lowered = path.lower()
        for fragment in _DESTRUCTIVE_FRAGMENTS:
            assert fragment not in lowered, f"destructive route exposed: {path}"


def test_trace_mock_writes_timings_and_returns_timeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    response = _client().post(
        "/api/observe/trace",
        json={"mock": True, "device": MOCK_DEVICE_PATH},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "mock"
    assert body["trace_ok"] is True
    assert body["timings_written"] is True
    assert (tmp_path / "last_admin_timings.json").is_file()
    assert "hop_timeline" in body
    assert isinstance(body["hop_timeline"], list)
    assert len(body["hop_timeline"]) > 0


def test_smart_mock_returns_smart_dict() -> None:
    body = _client().get(
        f"/api/sentinel/smart?mock=true&device={MOCK_DEVICE_PATH}"
    ).json()
    assert body["ok"] is True
    assert body["smart"] is not None
    assert "percentage_used" in body["smart"]
