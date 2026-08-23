"""Cortex / Netie Engine client — status, engines, models."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import structlog

from openmw.model_router import ModelRouter
from openmw.openvault.mesh.local_mesh import DEFAULT_CORTEX_URL, cortex_base_url

log = structlog.get_logger()

# Re-export shared mesh default (http://127.0.0.1:8010); override via CORTEX_URL.
__all__ = ("DEFAULT_CORTEX_URL", "CortexClient", "CortexStatus")


@dataclass(frozen=True)
class CortexStatus:
    online: bool
    base_url: str
    detail: str
    raw: dict[str, Any] | None = None


class CortexClient:
    """Talk to a running Cortex instance; fall back to local model registry."""

    def __init__(
        self,
        base_url: str | None = None,
        *,
        timeout_s: float = 5.0,
        local_registry: Path | None = None,
    ) -> None:
        self.base_url = (base_url if base_url is not None else cortex_base_url()).rstrip("/")
        self._timeout_s = timeout_s
        self._local_registry = local_registry

    async def status(self) -> CortexStatus:
        try:
            async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                resp = await client.get(f"{self.base_url}/health")
            if resp.status_code >= 200 and resp.status_code < 300:
                payload: dict[str, Any]
                try:
                    payload = resp.json()
                except json.JSONDecodeError:
                    payload = {"raw": resp.text[:200]}
                return CortexStatus(True, self.base_url, "healthy", payload)
            return CortexStatus(
                False,
                self.base_url,
                f"HTTP {resp.status_code}",
                None,
            )
        except httpx.HTTPError as exc:
            return CortexStatus(False, self.base_url, str(exc), None)

    async def engines(self) -> dict[str, Any]:
        """Fetch Netie Engine registry; return cached local descriptors if offline."""
        paths = (
            "/api/engine/backends",
            "/api/engine/",
            "/api/engine",
        )
        async with httpx.AsyncClient(timeout=self._timeout_s) as client:
            for path in paths:
                try:
                    resp = await client.get(f"{self.base_url}{path}")
                except httpx.HTTPError:
                    continue
                if resp.status_code >= 200 and resp.status_code < 300:
                    try:
                        data = resp.json()
                    except json.JSONDecodeError:
                        data = {"raw": resp.text[:500]}
                    return {
                        "source": "cortex",
                        "online": True,
                        "base_url": self.base_url,
                        "engines": data,
                    }
        return {
            "source": "local_fallback",
            "online": False,
            "base_url": self.base_url,
            "engines": _local_engine_catalog(),
        }

    def local_models(self) -> list[dict[str, Any]]:
        router = (
            ModelRouter(registry_path=self._local_registry)
            if self._local_registry is not None
            else ModelRouter()
        )
        models: list[dict[str, Any]] = []
        for model_id, spec in router.registry.items():
            models.append(
                {
                    "id": model_id,
                    "name": spec.name,
                    "tier": spec.tier,
                    "params_B": spec.params_B,
                    "source": "openmw_registry",
                    "cortex_tier_hint": _map_hardware_tier_to_cortex(spec.tier),
                }
            )
        return models

    async def models(self) -> dict[str, Any]:
        local = self.local_models()
        cortex_models: list[dict[str, Any]] = []
        online = False
        async with httpx.AsyncClient(timeout=self._timeout_s) as client:
            for path in ("/api/engine/models", "/v1/models"):
                try:
                    resp = await client.get(f"{self.base_url}{path}")
                except httpx.HTTPError:
                    continue
                if resp.status_code >= 200 and resp.status_code < 300:
                    online = True
                    try:
                        payload = resp.json()
                    except json.JSONDecodeError:
                        payload = {}
                    if isinstance(payload, dict) and "data" in payload:
                        for item in payload["data"]:
                            if isinstance(item, dict):
                                cortex_models.append(
                                    {
                                        "id": str(item.get("id", "")),
                                        "name": str(item.get("id", "")),
                                        "tier": "T1",
                                        "source": "cortex",
                                    }
                                )
                    elif isinstance(payload, list):
                        for item in payload:
                            if isinstance(item, dict):
                                cortex_models.append(
                                    {
                                        "id": str(item.get("id", item.get("name", ""))),
                                        "name": str(item.get("name", item.get("id", ""))),
                                        "tier": str(item.get("tier", "T1")),
                                        "source": "cortex",
                                    }
                                )
                    break
        return {
            "online": online,
            "base_url": self.base_url,
            "models": cortex_models + local,
            "cortex_count": len(cortex_models),
            "local_count": len(local),
        }


def _local_engine_catalog() -> list[dict[str, Any]]:
    return [
        {
            "id": "vllm",
            "name": "vLLM",
            "status": "descriptor",
            "note": "Cortex offline — local descriptor only",
        },
        {
            "id": "sglang",
            "name": "SGLang",
            "status": "descriptor",
            "note": "Cortex offline — local descriptor only",
        },
        {
            "id": "ollama",
            "name": "Ollama",
            "status": "descriptor",
            "note": "Local inference fallback",
        },
        {
            "id": "llama.cpp",
            "name": "llama.cpp",
            "status": "descriptor",
            "note": "Local GGUF runtime",
        },
    ]


def _map_hardware_tier_to_cortex(tier: str) -> str:
    mapping = {
        "NANO": "T0",
        "SMALL": "T1",
        "MID": "T1",
        "LARGE": "T2",
        "XLARGE": "T3",
    }
    return mapping.get(tier, "T1")
