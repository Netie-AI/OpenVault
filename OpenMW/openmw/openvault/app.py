"""FastAPI application for the OpenVault local console."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict
from typing import Any

import structlog
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from openmw.demo_payload import bundled_webui_dir
from openmw.openvault.cortex_client import CortexClient
from openmw.openvault.fallback import FallbackConfig, FallbackManager
from openmw.openvault.health import bottleneck_payload, devices_payload
from openmw.openvault.orchestration import OrchestrationSelection, load_selection, save_selection
from openmw.openvault.precheck import PrecheckLoop, precheck_all, precheck_one
from openmw.openvault.proxy import chat_completions
from openmw.openvault.vault import KeyRole, KeyVault, ProviderKind

log = structlog.get_logger()


class KeyCreate(BaseModel):
    label: str
    provider: ProviderKind
    secret: str
    role: KeyRole = "backup"
    base_url: str = ""
    priority: int = 100
    enabled: bool = True


class KeyUpdate(BaseModel):
    label: str | None = None
    provider: ProviderKind | None = None
    secret: str | None = None
    role: KeyRole | None = None
    base_url: str | None = None
    priority: int | None = None
    enabled: bool | None = None


class FallbackUpdate(BaseModel):
    role_order: list[str] = Field(default_factory=lambda: ["primary", "backup", "cheap", "free"])
    failure_threshold: int = 3
    open_seconds: float = 60.0


class SelectionUpdate(BaseModel):
    primary_model: str = ""
    fallback_models: list[str] = Field(default_factory=list)
    cortex_tier: str = "T1"
    engine_id: str = "ollama"
    notes: str = ""


class ChatBody(BaseModel):
    model: str = "auto"
    messages: list[dict[str, Any]]
    temperature: float | None = None
    max_tokens: int | None = None
    stream: bool | None = False


def create_app(
    *,
    vault: KeyVault | None = None,
    cortex_url: str = "http://127.0.0.1:8000",
    precheck_interval_s: float = 60.0,
    mock_health: bool = False,
    enable_precheck_loop: bool = True,
) -> FastAPI:
    state_vault = vault if vault is not None else KeyVault()
    fallback = FallbackManager(state_vault)
    cortex = CortexClient(cortex_url)
    loop_holder: dict[str, PrecheckLoop | None] = {"loop": None}
    task_holder: dict[str, asyncio.Task[None] | None] = {"task": None}

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if enable_precheck_loop:
            pre_loop = PrecheckLoop(state_vault, interval_s=precheck_interval_s)
            loop_holder["loop"] = pre_loop
            task_holder["task"] = asyncio.create_task(pre_loop.run_forever())
            log.info("openvault_precheck_loop_started", interval_s=precheck_interval_s)
        yield
        if loop_holder["loop"] is not None:
            loop_holder["loop"].stop()
        task = task_holder["task"]
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    app = FastAPI(title="OpenVault", version="0.1.0", lifespan=lifespan)

    @app.get("/api/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "service": "openvault"}

    @app.get("/api/health/devices")
    def health_devices() -> dict[str, Any]:
        return devices_payload(use_live_detect=not mock_health)

    @app.get("/api/health/bottleneck")
    def health_bottleneck() -> dict[str, Any]:
        return bottleneck_payload()

    @app.get("/api/keys")
    def list_keys() -> dict[str, Any]:
        return {"keys": [asdict(k) for k in state_vault.list_keys()]}

    @app.post("/api/keys")
    def create_key(body: KeyCreate) -> dict[str, Any]:
        record = state_vault.create(
            label=body.label,
            provider=body.provider,
            secret=body.secret,
            role=body.role,
            base_url=body.base_url,
            priority=body.priority,
            enabled=body.enabled,
        )
        return asdict(record)

    @app.patch("/api/keys/{key_id}")
    def patch_key(key_id: str, body: KeyUpdate) -> dict[str, Any]:
        record = state_vault.update(
            key_id,
            label=body.label,
            provider=body.provider,
            secret=body.secret,
            role=body.role,
            base_url=body.base_url,
            priority=body.priority,
            enabled=body.enabled,
        )
        if record is None:
            raise HTTPException(status_code=404, detail="key not found")
        return asdict(record)

    @app.delete("/api/keys/{key_id}")
    def delete_key(key_id: str) -> dict[str, bool]:
        ok = state_vault.delete(key_id)
        if not ok:
            raise HTTPException(status_code=404, detail="key not found")
        return {"deleted": True}

    @app.get("/api/keys/{key_id}/secret")
    def reveal_secret(key_id: str) -> dict[str, str]:
        secret = state_vault.get_secret(key_id)
        if secret is None:
            raise HTTPException(status_code=404, detail="key not found")
        return {"id": key_id, "secret": secret}

    @app.post("/api/keys/{key_id}/precheck")
    async def key_precheck(key_id: str) -> dict[str, Any]:
        result = await precheck_one(state_vault, key_id)
        return asdict(result)

    @app.post("/api/keys/precheck-all")
    async def keys_precheck_all() -> dict[str, Any]:
        results = await precheck_all(state_vault)
        return {"results": [asdict(r) for r in results]}

    @app.get("/api/fallback")
    def get_fallback() -> dict[str, Any]:
        status = fallback.status()
        return {"hops": status.hops, "config": status.config}

    @app.put("/api/fallback")
    def put_fallback(body: FallbackUpdate) -> dict[str, Any]:
        fallback.save_config(
            FallbackConfig(
                role_order=body.role_order,
                failure_threshold=body.failure_threshold,
                open_seconds=body.open_seconds,
            )
        )
        status = fallback.status()
        return {"hops": status.hops, "config": status.config}

    @app.get("/api/fallback/status")
    def fallback_status() -> dict[str, Any]:
        status = fallback.status()
        return {"hops": status.hops, "config": status.config}

    @app.get("/api/cortex/status")
    async def cortex_status() -> dict[str, Any]:
        st = await cortex.status()
        return {
            "online": st.online,
            "base_url": st.base_url,
            "detail": st.detail,
            "raw": st.raw,
        }

    @app.get("/api/cortex/engines")
    async def cortex_engines() -> dict[str, Any]:
        return await cortex.engines()

    @app.get("/api/cortex/models")
    async def cortex_models() -> dict[str, Any]:
        return await cortex.models()

    @app.get("/api/orchestration/selection")
    def get_selection() -> dict[str, Any]:
        return asdict(load_selection())

    @app.put("/api/orchestration/selection")
    def put_selection(body: SelectionUpdate) -> dict[str, Any]:
        saved = save_selection(
            OrchestrationSelection(
                primary_model=body.primary_model,
                fallback_models=body.fallback_models,
                cortex_tier=body.cortex_tier,
                engine_id=body.engine_id,
                notes=body.notes,
            )
        )
        return asdict(saved)

    @app.post("/v1/chat/completions")
    async def v1_chat(body: ChatBody) -> JSONResponse:
        if body.stream:
            return JSONResponse(
                status_code=400,
                content={"error": {"message": "stream not supported yet", "type": "unsupported"}},
            )
        payload = body.model_dump(exclude_none=True)
        status, result = await chat_completions(state_vault, fallback, payload)
        return JSONResponse(status_code=status, content=result)

    webui = bundled_webui_dir()
    if webui.is_dir():

        @app.get("/")
        def index() -> FileResponse:
            return FileResponse(webui / "index.html")

        app.mount("/static", StaticFiles(directory=str(webui)), name="static")

    return app


def run_console(
    *,
    host: str = "127.0.0.1",
    port: int = 5000,
    cortex_url: str = "http://127.0.0.1:8000",
    mock_health: bool = False,
    precheck_interval_s: float = 60.0,
) -> None:
    import uvicorn

    app = create_app(
        cortex_url=cortex_url,
        mock_health=mock_health,
        precheck_interval_s=precheck_interval_s,
    )
    log.info("openvault_console_start", host=host, port=port, cortex_url=cortex_url)
    uvicorn.run(app, host=host, port=port, log_level="info")
