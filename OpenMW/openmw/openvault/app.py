"""FastAPI application for the OpenVault local console."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict
from typing import Any, Literal

import structlog
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from openmw.demo_payload import bundled_webui_dir
from openmw.openvault.accounts import AccountStore, AuthProvider
from openmw.openvault.cortex_client import CortexClient
from openmw.openvault.deploy import (
    build_deploy_plan,
    execute_deploy,
    list_plans,
    load_plan,
    run_deploy_smoke,
)
from openmw.openvault.detect import detect_project
from openmw.openvault.fallback import FallbackConfig, FallbackManager
from openmw.openvault.health import bottleneck_payload, devices_payload
from openmw.openvault.local_mesh import (
    announce_peer,
    build_connect_pack,
    decide_handshake,
    load_mesh,
    openide_invoke,
    refresh_mesh,
    save_mesh,
)
from openmw.openvault.openship import (
    build_openship_plan,
    execute_openship_plan,
    list_ship_plans,
    load_ship_plan,
)
from openmw.openvault.orchestration import OrchestrationSelection, load_selection, save_selection
from openmw.openvault.playwright_smoke import load_smoke, run_playwright_smoke
from openmw.openvault.precheck import PrecheckLoop, precheck_all, precheck_one
from openmw.openvault.providers import (
    catalog_coverage_report,
    check_provider_downtime,
    get_provider,
    list_catalog,
)
from openmw.openvault.proxy import chat_completions
from openmw.openvault.seed import seed_essentials
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
    account_id: str | None = None


class KeyUpdate(BaseModel):
    label: str | None = None
    provider: ProviderKind | None = None
    secret: str | None = None
    role: KeyRole | None = None
    base_url: str | None = None
    priority: int | None = None
    enabled: bool | None = None
    account_id: str | None = None


class KeyRotate(BaseModel):
    new_secret: str
    label_suffix: str = "rotated"


class KeyRevoke(BaseModel):
    reason: str = "operator_revoke"


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


class DeployFromCortex(BaseModel):
    project_path: str
    subdomain: str = ""
    intent: str = "deploy_to_web"
    source: str = "cortex"
    open_console: bool = True
    sending_ip: str | None = None
    console_base: str = "http://127.0.0.1:5000"
    smoke_url: str = ""
    run_smoke: bool = False


class DetectBody(BaseModel):
    project_path: str


class SeedBody(BaseModel):
    consumers: list[str] = Field(default_factory=lambda: ["cortex", "airgpt", "openvault"])
    include_local_placeholders: bool = True


class AccountCreate(BaseModel):
    display_name: str
    email: str | None = None
    auth_provider: AuthProvider = "netie_email"
    local_part: str | None = None
    operator_notes: str = ""
    allocate_relay: bool = True


class AccountKeyCreate(BaseModel):
    label: str
    provider: ProviderKind
    secret: str
    role: KeyRole = "backup"
    base_url: str = ""
    priority: int = 100


class IncidentBody(BaseModel):
    reason: str = "compromised_or_manipulated"
    replacement_secrets: dict[str, str] = Field(default_factory=dict)
    suspend_account: bool = True


class OpenShipBody(BaseModel):
    project_path: str
    subdomain: str
    action: Literal["install", "update", "rollback"] = "install"
    sending_ip: str | None = None
    execute: bool = False
    simulate: bool | None = None


class SmokeBody(BaseModel):
    url: str
    mode: str | None = None


class DeploySmokeBody(BaseModel):
    url: str | None = None


class DeployExecuteBody(BaseModel):
    simulate: bool | None = None


class HandshakeBody(BaseModel):
    peer_kind: Literal["openvault", "cortex", "openide", "airgpt", "rust_console"]
    name: str
    base_url: str
    capabilities: list[str] = Field(default_factory=list)
    auto_approve: bool | None = None


class HandshakeDecision(BaseModel):
    approve: bool = True
    note: str = ""


class OpenIdeInvoke(BaseModel):
    action: str
    username: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


class MeshConfigUpdate(BaseModel):
    auto_approve_loopback: bool = True
    cortex_url: str | None = None
    openide_url: str | None = None
    rust_console_url: str | None = None


def create_app(
    *,
    vault: KeyVault | None = None,
    accounts: AccountStore | None = None,
    cortex_url: str = "http://127.0.0.1:8000",
    openide_url: str = "http://127.0.0.1:5100",
    precheck_interval_s: float = 60.0,
    mock_health: bool = False,
    enable_precheck_loop: bool = True,
) -> FastAPI:
    state_vault = vault if vault is not None else KeyVault()
    state_accounts = accounts if accounts is not None else AccountStore()
    fallback = FallbackManager(state_vault)
    cortex = CortexClient(cortex_url)
    loop_holder: dict[str, PrecheckLoop | None] = {"loop": None}
    task_holder: dict[str, asyncio.Task[None] | None] = {"task": None}

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        mesh = load_mesh()
        mesh.peers["cortex"].base_url = cortex_url.rstrip("/")
        mesh.peers["openide"].base_url = openide_url.rstrip("/")
        save_mesh(mesh)
        refresh_mesh(mesh)
        if enable_precheck_loop:
            pre_loop = PrecheckLoop(state_vault, interval_s=precheck_interval_s)
            loop_holder["loop"] = pre_loop
            task_holder["task"] = asyncio.create_task(pre_loop.run_forever())
            log.info("openvault_precheck_loop_started", interval_s=precheck_interval_s)
        log.info(
            "openvault_local_mesh_ready",
            cortex_url=cortex_url,
            openide_url=openide_url,
        )
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
    def healthz() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "openvault",
            "mesh": ["openvault", "cortex", "openide", "rust_console"],
        }

    @app.get("/api/health/devices")
    def health_devices() -> dict[str, Any]:
        return devices_payload(use_live_detect=not mock_health)

    @app.get("/api/health/bottleneck")
    def health_bottleneck() -> dict[str, Any]:
        return bottleneck_payload()

    # --- Local mesh: OpenVault ↔ Cortex ↔ OpenIDE ---

    @app.get("/api/local/mesh")
    def local_mesh_status() -> dict[str, Any]:
        state = refresh_mesh()
        pack = build_connect_pack(state)
        return {
            "mesh": state.to_dict(),
            "connect_pack": pack,
            "perfect_local": pack["perfect_local"],
        }

    @app.post("/api/local/mesh/refresh")
    def local_mesh_refresh() -> dict[str, Any]:
        state = refresh_mesh()
        return {"mesh": state.to_dict(), "connect_pack": build_connect_pack(state)}

    @app.put("/api/local/mesh/config")
    def local_mesh_config(body: MeshConfigUpdate) -> dict[str, Any]:
        state = load_mesh()
        state.auto_approve_loopback = body.auto_approve_loopback
        if body.cortex_url:
            state.peers["cortex"].base_url = body.cortex_url.rstrip("/")
        if body.openide_url:
            state.peers["openide"].base_url = body.openide_url.rstrip("/")
        if body.rust_console_url:
            state.peers["rust_console"].base_url = body.rust_console_url.rstrip("/")
        save_mesh(state)
        state = refresh_mesh(state)
        return {"mesh": state.to_dict(), "connect_pack": build_connect_pack(state)}

    @app.post("/api/local/handshake")
    def local_handshake(body: HandshakeBody) -> dict[str, Any]:
        return announce_peer(
            peer_kind=body.peer_kind,
            name=body.name,
            base_url=body.base_url,
            capabilities=body.capabilities,
            auto_approve=body.auto_approve,
        )

    @app.post("/api/local/handshake/{request_id}/decide")
    def local_handshake_decide(request_id: str, body: HandshakeDecision) -> dict[str, Any]:
        try:
            return decide_handshake(request_id, approve=body.approve, note=body.note)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="handshake not found") from exc

    @app.get("/api/local/connect-pack")
    def local_connect_pack() -> dict[str, Any]:
        return build_connect_pack(refresh_mesh())

    @app.post("/api/openide/invoke")
    def api_openide_invoke(body: OpenIdeInvoke) -> dict[str, Any]:
        return openide_invoke(
            action=body.action,
            username=body.username,
            payload=body.payload,
        )

    # --- Accounts / custody ---

    @app.get("/api/accounts")
    def list_accounts() -> dict[str, Any]:
        return {"accounts": [a.to_dict() for a in state_accounts.list_accounts()]}

    @app.post("/api/accounts")
    def create_account(body: AccountCreate) -> dict[str, Any]:
        try:
            record = state_accounts.create(
                display_name=body.display_name,
                email=body.email,
                auth_provider=body.auth_provider,
                local_part=body.local_part,
                operator_notes=body.operator_notes,
                allocate_relay=body.allocate_relay,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return record.to_dict()

    @app.get("/api/accounts/{account_id}")
    def get_account(account_id: str) -> dict[str, Any]:
        bundle = state_accounts.operator_access_bundle(account_id)
        if bundle is None:
            raise HTTPException(status_code=404, detail="account not found")
        keys = [asdict(k) for k in state_vault.list_keys(account_id=account_id)]
        bundle["keys"] = keys
        return bundle

    @app.post("/api/accounts/{account_id}/relay")
    def account_relay(account_id: str) -> dict[str, Any]:
        record = state_accounts.allocate_relay(account_id)
        if record is None:
            raise HTTPException(status_code=404, detail="account not found")
        return record.to_dict()

    @app.post("/api/accounts/{account_id}/keys")
    def account_create_key(account_id: str, body: AccountKeyCreate) -> dict[str, Any]:
        if state_accounts.get(account_id) is None:
            raise HTTPException(status_code=404, detail="account not found")
        record = state_vault.create(
            label=body.label,
            provider=body.provider,
            secret=body.secret,
            role=body.role,
            base_url=body.base_url,
            priority=body.priority,
            account_id=account_id,
        )
        return asdict(record)

    @app.post("/api/accounts/{account_id}/incident")
    def account_incident(account_id: str, body: IncidentBody) -> dict[str, Any]:
        if state_accounts.get(account_id) is None:
            raise HTTPException(status_code=404, detail="account not found")
        result = state_vault.incident_kill(
            account_id,
            reason=body.reason,
            replacement_secrets=body.replacement_secrets or None,
        )
        account = None
        if body.suspend_account:
            account = state_accounts.set_status(
                account_id,
                "compromised",
                operator_notes=f"incident: {body.reason}",
            )
        else:
            account = state_accounts.get(account_id)
        payload: dict[str, Any] = dict(result)
        payload["account"] = account.to_dict() if account is not None else None
        return payload

    # --- Keys ---

    @app.get("/api/keys")
    def list_keys(account_id: str | None = None) -> dict[str, Any]:
        return {"keys": [asdict(k) for k in state_vault.list_keys(account_id=account_id)]}

    @app.post("/api/keys")
    def create_key(body: KeyCreate) -> dict[str, Any]:
        if body.account_id and state_accounts.get(body.account_id) is None:
            raise HTTPException(status_code=404, detail="account not found")
        record = state_vault.create(
            label=body.label,
            provider=body.provider,
            secret=body.secret,
            role=body.role,
            base_url=body.base_url,
            priority=body.priority,
            enabled=body.enabled,
            account_id=body.account_id,
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
            account_id=body.account_id,
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

    @app.post("/api/keys/{key_id}/revoke")
    def revoke_key(key_id: str, body: KeyRevoke) -> dict[str, Any]:
        record = state_vault.revoke(key_id, reason=body.reason)
        if record is None:
            raise HTTPException(status_code=404, detail="key not found")
        return asdict(record)

    @app.post("/api/keys/{key_id}/rotate")
    def rotate_key(key_id: str, body: KeyRotate) -> dict[str, Any]:
        record = state_vault.rotate(
            key_id, new_secret=body.new_secret, label_suffix=body.label_suffix
        )
        if record is None:
            raise HTTPException(status_code=404, detail="key not found")
        return asdict(record)

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

    @app.post("/api/detect")
    def api_detect(body: DetectBody) -> dict[str, Any]:
        return detect_project(body.project_path).to_dict()

    @app.post("/api/deploy/from-cortex")
    async def deploy_from_cortex(body: DeployFromCortex) -> dict[str, Any]:
        st = await cortex.status()
        plan = build_deploy_plan(
            project_path=body.project_path,
            subdomain=body.subdomain,
            intent=body.intent,
            source=body.source,
            vault=state_vault,
            fallback=fallback,
            cortex_online=st.online,
            sending_ip=body.sending_ip,
            console_base=body.console_base,
            smoke_url=body.smoke_url,
            run_smoke=body.run_smoke,
        )
        payload = plan.to_dict()
        payload["open_console"] = body.open_console
        return payload

    @app.get("/api/deploy/{deploy_id}")
    def get_deploy(deploy_id: str) -> dict[str, Any]:
        plan = load_plan(deploy_id)
        if plan is None:
            raise HTTPException(status_code=404, detail="deploy plan not found")
        return plan.to_dict()

    @app.get("/api/deploy")
    def get_deploys() -> dict[str, Any]:
        return {"deploys": list_plans()}

    @app.post("/api/deploy/{deploy_id}/playwright-smoke")
    def deploy_playwright_smoke(deploy_id: str, body: DeploySmokeBody) -> dict[str, Any]:
        plan = load_plan(deploy_id)
        if plan is None:
            raise HTTPException(status_code=404, detail="deploy plan not found")
        updated = run_deploy_smoke(plan, url=body.url)
        return updated.to_dict()

    @app.post("/api/deploy/{deploy_id}/execute")
    def deploy_execute(deploy_id: str, body: DeployExecuteBody) -> dict[str, Any]:
        plan = load_plan(deploy_id)
        if plan is None:
            raise HTTPException(status_code=404, detail="deploy plan not found")
        updated = execute_deploy(plan, simulate=body.simulate)
        return updated.to_dict()

    # --- OpenShip full clone surface ---

    @app.post("/api/openship/plan")
    def openship_plan(body: OpenShipBody) -> dict[str, Any]:
        plan = build_openship_plan(
            project_path=body.project_path,
            subdomain=body.subdomain,
            action=body.action,
            sending_ip=body.sending_ip,
        )
        if body.execute:
            plan = execute_openship_plan(plan, simulate=body.simulate)
        return plan.to_dict()

    @app.get("/api/openship")
    def openship_list() -> dict[str, Any]:
        return {"ships": list_ship_plans()}

    @app.get("/api/openship/{ship_id}")
    def openship_get(ship_id: str) -> dict[str, Any]:
        plan = load_ship_plan(ship_id)
        if plan is None:
            raise HTTPException(status_code=404, detail="openship plan not found")
        return plan.to_dict()

    @app.post("/api/openship/{ship_id}/execute")
    def openship_execute(ship_id: str, body: DeployExecuteBody) -> dict[str, Any]:
        plan = load_ship_plan(ship_id)
        if plan is None:
            raise HTTPException(status_code=404, detail="openship plan not found")
        return execute_openship_plan(plan, simulate=body.simulate).to_dict()

    # --- Playwright smoke ---

    @app.post("/api/playwright/smoke")
    def playwright_smoke(body: SmokeBody) -> dict[str, Any]:
        return run_playwright_smoke(body.url, mode=body.mode).to_dict()

    @app.get("/api/playwright/smoke/{smoke_id}")
    def playwright_smoke_get(smoke_id: str) -> dict[str, Any]:
        payload = load_smoke(smoke_id)
        if payload is None:
            raise HTTPException(status_code=404, detail="smoke artifact not found")
        return payload

    @app.get("/api/providers/catalog")
    def providers_catalog(
        free_only: bool = False,
        needed_by: str | None = None,
    ) -> dict[str, Any]:
        return {
            "providers": list_catalog(free_only=free_only, needed_by=needed_by),
            "count": len(list_catalog(free_only=free_only, needed_by=needed_by)),
        }

    @app.get("/api/providers/free")
    def providers_free() -> dict[str, Any]:
        rows = list_catalog(free_only=True)
        return {
            "providers": rows,
            "count": len(rows),
            "help": "Use register_url to create keys, then POST /api/keys and precheck",
        }

    @app.get("/api/providers/coverage")
    def providers_coverage() -> dict[str, Any]:
        ids = {str(k.provider) for k in state_vault.list_keys()}
        return catalog_coverage_report(ids)

    @app.post("/api/providers/{provider_id}/downtime-check")
    async def provider_downtime(provider_id: str) -> dict[str, Any]:
        spec = get_provider(provider_id)
        if spec is None:
            raise HTTPException(status_code=404, detail="unknown provider in catalog")
        result = await check_provider_downtime(spec)
        return result.to_dict()

    @app.post("/api/providers/check-all-free")
    async def check_all_free() -> dict[str, Any]:
        results = []
        for row in list_catalog(free_only=True):
            spec = get_provider(row["id"])
            if spec is None:
                continue
            results.append((await check_provider_downtime(spec)).to_dict())
        online = sum(1 for r in results if r["online"])
        return {
            "results": results,
            "online": online,
            "total": len(results),
            "all_ok": online == len(results) and len(results) > 0,
        }

    @app.post("/api/vault/seed-essentials")
    def vault_seed(body: SeedBody) -> dict[str, Any]:
        return seed_essentials(
            state_vault,
            consumers=tuple(body.consumers),
            include_local_placeholders=body.include_local_placeholders,
        )

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
