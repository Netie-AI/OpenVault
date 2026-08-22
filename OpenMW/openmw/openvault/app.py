"""FastAPI application for the OpenVault local console."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict
from typing import Any, Literal

import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from openmw.demo_payload import bundled_webui_dir
from openmw.openvault.cloud.firewall import evaluate_action, list_rules
from openmw.openvault.cloud.lan_discover import discover_lan_devices
from openmw.openvault.cloud.multiplayer import (
    create_session,
    get_session,
    join_session,
    list_sessions,
    post_event,
)
from openmw.openvault.cloud.share_store import ShareStore
from openmw.openvault.control.actions import run_control_action
from openmw.openvault.control.capabilities import probe_control_capabilities
from openmw.openvault.health.devices import devices_payload
from openmw.openvault.mesh.cortex_client import CortexClient
from openmw.openvault.mesh.local_mesh import (
    OPENIDE_DEFAULT_URL,
    announce_peer,
    build_connect_pack,
    decide_handshake,
    load_mesh,
    openide_invoke,
    refresh_mesh,
    save_mesh,
)
from openmw.openvault.mesh.orchestration import (
    OrchestrationSelection,
    load_selection,
    save_selection,
)
from openmw.openvault.mesh.slots import list_slots
from openmw.openvault.observe.path import bottleneck_payload, observe_path_payload
from openmw.openvault.ship.cicd import detect_cicd
from openmw.openvault.ship.cloud_targets import (
    BillBudget,
    build_ship_blueprint,
    list_targets,
    load_bill_budget,
    save_bill_budget,
)
from openmw.openvault.ship.aws_guide import build_aws_render_plan
from openmw.openvault.ship.deploy import (
    build_deploy_plan,
    execute_deploy,
    list_plans,
    load_plan,
    one_press_deploy,
    run_deploy_smoke,
)
from openmw.openvault.ship.domain_guide import build_domain_guide
from openmw.openvault.ship.engine import load_deployment, run_ship_engine
from openmw.openvault.ship.github_auth import (
    clear_pat,
    connection_status,
    list_branches,
    list_repos,
    save_pat,
    start_gh_login,
)
from openmw.openvault.ship.library import (
    create_upload_session,
    inspect_folder,
    inspect_github_url,
    library_home,
    scan_upload_session,
)
from openmw.openvault.ship.gate import check_gate
from openmw.openvault.ship.openship import (
    build_openship_plan,
    execute_openship_plan,
    list_ship_plans,
    load_ship_plan,
)
from openmw.openvault.ship.openship_client import OpenShipClient, adapter_status
from openmw.openvault.ship.playwright_smoke import load_smoke, run_playwright_smoke
from openmw.openvault.vault.accounts import AccountStore, AuthProvider
from openmw.openvault.vault.airgpt_keyvault import keyvault_snapshot, upsert_env_secret
from openmw.openvault.vault.env_ingest import ingest_environment, scan_environment
from openmw.openvault.vault.fallback import FallbackConfig, FallbackManager
from openmw.openvault.vault.precheck import PrecheckLoop, precheck_all, precheck_one
from openmw.openvault.vault.providers import (
    catalog_coverage_report,
    check_provider_downtime,
    get_provider,
    list_catalog,
)
from openmw.openvault.vault.proxy import chat_completions
from openmw.openvault.vault.ratelimit import (
    DEFAULT_MAX_OUTPUT_TOKENS,
    DEFAULT_TIER,
    TokenBudgetLimiter,
    estimate_prompt_tokens,
    usage_total_tokens,
)
from openmw.openvault.vault.redis_store import try_make_redis_store
from openmw.openvault.vault.seed import seed_essentials
from openmw.openvault.vault.store import KeyRole, KeyVault, ProviderKind

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


class OnePressDeployBody(BaseModel):
    project_path: str
    subdomain: str = ""
    intent: str = "deploy_to_web"
    source: str = "manual"
    open_console: bool = True
    sending_ip: str | None = None
    console_base: str = "http://127.0.0.1:5000"
    smoke_url: str = ""
    run_smoke: bool = False
    simulate: bool = True
    auto_execute: bool = True
    target: Literal[
        "cursor_origin", "openship_cloud", "vps_ssh", "aws_guide", "local_demo"
    ] = "local_demo"
    github_url: str = ""
    vps_host: str = ""
    cloud_tier: str = "low"
    monthly_cap_usd: float | None = None


class DomainGuideBody(BaseModel):
    hostname: str
    target_a: str = "<YOUR_SERVER_IP>"
    target_cname: str = ""
    include_www: bool = True
    include_mail: bool = True


class ShipBlueprintBody(BaseModel):
    target: Literal[
        "cursor_origin", "openship_cloud", "vps_ssh", "aws_guide", "local_demo"
    ] = "openship_cloud"
    project_path: str = ""
    hostname: str = ""
    github_url: str = ""
    vps_host: str = ""
    cloud_tier: str = "low"
    monthly_cap_usd: float | None = None


class BillBudgetBody(BaseModel):
    monthly_cap_usd: float = 25.0
    spent_usd_estimate: float | None = None
    soft_warn_pct: float = 80.0
    hard_stop: bool = True


class GitHubPatBody(BaseModel):
    token: str
    note: str = ""


class ShipEngineBody(BaseModel):
    target: Literal[
        "cursor_origin", "openship_cloud", "vps_ssh", "aws_guide", "local_demo"
    ] = "local_demo"
    project_path: str = ""
    github_url: str = ""
    hostname: str = ""
    vps_host: str = ""
    cloud_tier: str = "low"
    monthly_cap_usd: float | None = None
    run_build: bool = False
    prefer_remote_openship: bool = False


class LibraryInspectBody(BaseModel):
    path: str = ""
    github_url: str = ""


class DetectBody(BaseModel):
    project_path: str


class SeedBody(BaseModel):
    consumers: list[str] = Field(default_factory=lambda: ["cortex", "airgpt", "openvault"])
    include_local_placeholders: bool = True


class EnvIngestBody(BaseModel):
    # dry_run default mirrors the control tier: never write until asked.
    dry_run: bool = True
    include_unknown: bool = False


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


class ControlActionBody(BaseModel):
    action: str
    dry_run: bool = True
    confirm: bool = False
    percent: int = 50


class GateCheckBody(BaseModel):
    action: Literal["retrieve", "run", "deploy", "leave", "connect"] = "run"
    project_path: str = ""
    destination: str = ""
    required_providers: list[str] = Field(default_factory=list)
    # Client bypass flags — always denied by firewall (never honored).
    bypass: bool = False
    bypass_gate: bool = False
    force: bool = False
    skip_rules: bool = False


class CloudShareBody(BaseModel):
    title: str
    slug: str = ""
    summary: str = ""
    source_path: str = ""
    owner: str = "local"
    visibility: Literal["lan", "loopback", "invite"] = "lan"
    peers_allowed: list[str] = Field(default_factory=list)
    env_edge: dict[str, str] = Field(default_factory=dict)
    peer_ip: str = ""
    bypass: bool = False
    force: bool = False


class CloudFirewallBody(BaseModel):
    action: str
    destination: str = ""
    peer_ip: str = ""
    bypass: bool = False
    bypass_gate: bool = False
    force: bool = False
    skip_rules: bool = False
    gate_allowed: bool | None = None


class CloudSessionBody(BaseModel):
    title: str
    owner: str = "local"
    share_id: str = ""
    bypass: bool = False


class CloudJoinBody(BaseModel):
    user: str = "guest"
    peer_ip: str = ""
    bypass: bool = False
    force: bool = False


class CloudEventBody(BaseModel):
    user: str = "guest"
    event_type: str = "note"
    detail: str = ""


class KeyvaultUpsertBody(BaseModel):
    env_key: str = ""
    secret: str = ""
    label: str = ""
    base_url: str = ""
    provider: str = ""
    # Optional batch: {ENV_KEY: secret, ...}
    secrets: dict[str, str] = Field(default_factory=dict)


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def _require_loopback(request: Request, action: str) -> None:
    host = request.client.host if request.client is not None else ""
    if host not in _LOOPBACK_HOSTS:
        raise HTTPException(status_code=403, detail=f"{action} is loopback-only")


def _require_reveal_intent(request: Request) -> None:
    if request.headers.get("x-openvault-reveal") != "intentional":
        raise HTTPException(
            status_code=428,
            detail="X-OpenVault-Reveal: intentional required",
        )


def _audit_custody(event: str, request: Request, **fields: object) -> None:
    client = request.client.host if request.client is not None else ""
    log.info("openvault_custody", action=event, client=client, **fields)


def create_app(
    *,
    vault: KeyVault | None = None,
    accounts: AccountStore | None = None,
    cortex_url: str = "http://127.0.0.1:8000",
    openide_url: str = OPENIDE_DEFAULT_URL,
    precheck_interval_s: float = 60.0,
    mock_health: bool = False,
    enable_precheck_loop: bool = True,
) -> FastAPI:
    state_vault = vault if vault is not None else KeyVault()
    state_accounts = accounts if accounts is not None else AccountStore()
    fallback = FallbackManager(state_vault)
    redis_store = try_make_redis_store()
    limiter = TokenBudgetLimiter(store=redis_store) if redis_store is not None else TokenBudgetLimiter()
    if redis_store is not None:
        log.info("openvault_ratelimit_backend", backend="RedisBucketStore")
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

    # Stage-3 integrator mount: routers own their paths; app.py only wires them.
    from openmw.openvault.routers.keys import router as keys_router
    from openmw.openvault.routers.route import router as route_router
    from openmw.openvault.routers.sentinel import router as sentinel_router
    from openmw.openvault.routers.ship import router as ship_router

    app.include_router(ship_router)
    app.include_router(sentinel_router)
    app.include_router(route_router)
    app.include_router(keys_router)

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

    @app.get("/api/observe/path")
    def observe_path() -> dict[str, Any]:
        return observe_path_payload(prefer_live=True)

    @app.get("/api/slots")
    async def slots_registry() -> dict[str, Any]:
        return await list_slots(cortex)

    @app.get("/api/control/capabilities")
    def control_capabilities() -> dict[str, Any]:
        return probe_control_capabilities()

    @app.post("/api/control/action")
    def control_action(body: ControlActionBody) -> dict[str, Any]:
        return run_control_action(
            body.action,
            dry_run=body.dry_run,
            confirm=body.confirm,
            percent=body.percent,
        )

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

    @app.get("/api/openide/ready")
    def openide_ready(required_providers: str = "") -> dict[str, Any]:
        """Preflight OpenIDE Run: keys + mesh approval + gate, from the SoT.

        Keys source of truth is OpenVault whenever it is online (PRODUCT_ROLES);
        AirGPT's env.local is only an offline cache.
        """
        providers = [p.strip() for p in required_providers.split(",") if p.strip()]
        state = refresh_mesh()
        pack = build_connect_pack(state)
        peer = state.peers.get("openide")
        gate = check_gate(
            action="run",
            vault=state_vault,
            fallback=fallback,
            required_providers=providers or None,
        ).to_dict()
        keys_ready = bool(gate.get("keys_ready"))
        return {
            "ok": True,
            "ready": keys_ready and bool(gate.get("allowed")),
            "keys_ready": keys_ready,
            "keys_source_of_truth": "openvault",
            "openide": {
                "base_url": pack["openide"]["base_url"],
                "approved": bool(peer.approved) if peer is not None else False,
                "status": peer.status if peer is not None else "unknown",
            },
            "gate": gate,
            "perfect_local": pack["perfect_local"],
        }

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

    @app.get("/api/keyvault/snapshot")
    def api_keyvault_snapshot() -> dict[str, Any]:
        """AirGPT/OpenIDE Key Vault UI — OpenVault is SoT (PRODUCT_ROLES)."""
        return keyvault_snapshot(state_vault, openvault_url="http://127.0.0.1:5000")

    @app.post("/api/keyvault/upsert")
    def api_keyvault_upsert(body: KeyvaultUpsertBody) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        batch = dict(body.secrets or {})
        if body.env_key and body.secret:
            batch[body.env_key] = body.secret
        if not batch:
            raise HTTPException(status_code=400, detail="env_key+secret or secrets required")
        for env_key, secret in batch.items():
            results.append(
                upsert_env_secret(
                    state_vault,
                    env_key=env_key,
                    secret=secret,
                    label=body.label if len(batch) == 1 else env_key,
                    base_url=body.base_url if len(batch) == 1 else "",
                    provider_hint=body.provider if len(batch) == 1 else "",
                )
            )
        ok = all(r.get("ok") for r in results)
        return {"ok": ok, "results": results, "snapshot": keyvault_snapshot(state_vault)}

    @app.post("/api/gate/check")
    def api_gate_check(body: GateCheckBody) -> dict[str, Any]:
        """Cortex/apps ask; OpenVault alone allows retrieve/run/deploy/leave."""
        # Hard-block bypass attempts before vault logic
        fw = evaluate_action(
            "bypass_gate"
            if (body.bypass or body.bypass_gate or body.force or body.skip_rules)
            else "run_local",
            destination=body.destination,
            client_flags={
                "bypass": body.bypass,
                "bypass_gate": body.bypass_gate,
                "force": body.force,
                "skip_rules": body.skip_rules,
            },
        )
        if not fw.allowed and (body.bypass or body.bypass_gate or body.force or body.skip_rules):
            return {
                "allowed": False,
                "action": body.action,
                "reasons": fw.reasons,
                "keys_ready": False,
                "locate": {},
                "required_providers": body.required_providers,
                "firewall": fw.to_dict(),
            }
        decision = check_gate(
            action=body.action,
            vault=state_vault,
            fallback=fallback,
            project_path=body.project_path,
            destination=body.destination,
            required_providers=body.required_providers or None,
        )
        out = decision.to_dict()
        out["firewall"] = fw.to_dict()
        return out

    # --- Small Software LAN cloud ---
    state_shares = ShareStore()

    @app.get("/api/cloud/rules")
    def cloud_rules() -> dict[str, Any]:
        return {"ok": True, "rules": list_rules()}

    @app.get("/api/cloud/devices")
    def cloud_devices() -> dict[str, Any]:
        return discover_lan_devices()

    @app.post("/api/cloud/firewall/check")
    def cloud_firewall_check(body: CloudFirewallBody) -> dict[str, Any]:
        decision = evaluate_action(
            body.action,
            destination=body.destination,
            peer_ip=body.peer_ip,
            client_flags={
                "bypass": body.bypass,
                "bypass_gate": body.bypass_gate,
                "force": body.force,
                "skip_rules": body.skip_rules,
            },
            gate_allowed=body.gate_allowed,
        )
        return decision.to_dict()

    @app.get("/api/cloud/shares")
    def cloud_list_shares() -> dict[str, Any]:
        return {"ok": True, "shares": [s.to_dict() for s in state_shares.list_shares()]}

    @app.post("/api/cloud/shares")
    def cloud_publish_share(body: CloudShareBody) -> dict[str, Any]:
        fw = evaluate_action(
            "share_lan",
            destination=body.source_path or "lan",
            peer_ip=body.peer_ip,
            client_flags={"bypass": body.bypass, "force": body.force},
        )
        if not fw.allowed:
            raise HTTPException(status_code=403, detail=fw.to_dict())
        app_share = state_shares.publish(
            title=body.title,
            slug=body.slug,
            summary=body.summary,
            source_path=body.source_path,
            owner=body.owner,
            visibility=body.visibility,
            peers_allowed=body.peers_allowed,
            env_edge=body.env_edge,
        )
        return {"ok": True, "share": app_share.to_dict(), "firewall": fw.to_dict()}

    @app.get("/api/cloud/shares/{share_id}")
    def cloud_get_share(share_id: str) -> dict[str, Any]:
        row = state_shares.get(share_id) or state_shares.get_by_code(share_id)
        if row is None:
            raise HTTPException(status_code=404, detail="share not found")
        return {"ok": True, "share": row.to_dict()}

    @app.get("/api/cloud/sessions")
    def cloud_list_sessions() -> dict[str, Any]:
        return {"ok": True, "sessions": [s.to_dict() for s in list_sessions()]}

    @app.post("/api/cloud/sessions")
    def cloud_create_session(body: CloudSessionBody) -> dict[str, Any]:
        fw = evaluate_action(
            "join_session",
            client_flags={"bypass": body.bypass},
        )
        if not fw.allowed:
            raise HTTPException(status_code=403, detail=fw.to_dict())
        sess = create_session(title=body.title, owner=body.owner, share_id=body.share_id)
        return {"ok": True, "session": sess.to_dict(), "firewall": fw.to_dict()}

    @app.get("/api/cloud/sessions/{session_id}")
    def cloud_get_session(session_id: str) -> dict[str, Any]:
        sess = get_session(session_id)
        if sess is None:
            raise HTTPException(status_code=404, detail="session not found")
        return {"ok": True, "session": sess.to_dict()}

    @app.post("/api/cloud/sessions/{session_id}/join")
    def cloud_join_session(session_id: str, body: CloudJoinBody) -> dict[str, Any]:
        fw = evaluate_action(
            "join_session",
            peer_ip=body.peer_ip or "127.0.0.1",
            client_flags={"bypass": body.bypass, "force": body.force},
        )
        if not fw.allowed:
            raise HTTPException(status_code=403, detail=fw.to_dict())
        sess = join_session(session_id, user=body.user, peer_ip=body.peer_ip)
        if sess is None:
            raise HTTPException(status_code=404, detail="session not found")
        return {"ok": True, "session": sess.to_dict(), "firewall": fw.to_dict()}

    @app.post("/api/cloud/sessions/{session_id}/events")
    def cloud_session_event(session_id: str, body: CloudEventBody) -> dict[str, Any]:
        sess = post_event(
            session_id,
            user=body.user,
            event_type=body.event_type,
            detail=body.detail,
        )
        if sess is None:
            raise HTTPException(status_code=404, detail="session not found")
        return {"ok": True, "session": sess.to_dict()}

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

    # POST /api/detect lives on ship_router (empty/relative path → 400).

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

    @app.post("/api/deploy/one-press")
    async def deploy_one_press(body: OnePressDeployBody) -> dict[str, Any]:
        """One-press via in-process ship engine (OpenShip concepts stolen locally)."""
        st = await cortex.status()
        engine = run_ship_engine(
            target=body.target,
            project_path=body.project_path,
            github_url=body.github_url,
            hostname=body.subdomain,
            vps_host=body.vps_host,
            cloud_tier=body.cloud_tier,
            monthly_cap_usd=body.monthly_cap_usd,
            run_build=False,
            prefer_remote_openship=False,
        )
        if not engine.get("ok") and engine.get("error"):
            return {**engine, "open_console": body.open_console, "cortex_online": st.online}

        # Also keep custody gates artifact for vault/keys checklist
        simulate = body.simulate or body.target in ("local_demo", "aws_guide")
        if body.target == "openship_cloud" and not adapter_status().get("api_ready"):
            simulate = True
        work = (engine.get("deployment") or {}).get("project_path") or body.project_path
        payload = one_press_deploy(
            project_path=work or body.project_path or ".",
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
            simulate=simulate,
            auto_execute=body.auto_execute,
        )
        payload["open_console"] = body.open_console
        payload["engine"] = engine
        payload["blueprint"] = (engine.get("deployment") or {}).get("blueprint") or {}
        payload["target"] = body.target
        payload["github_url"] = body.github_url
        payload["domain_guide"] = payload.get("domain_guide") or {}
        if engine.get("deployment"):
            payload["deployment_id"] = engine["deployment"].get("deployment_id")
            payload["ship_steps"] = engine["deployment"].get("steps")
        return payload

    @app.get("/api/ship/library")
    def ship_library() -> dict[str, Any]:
        return library_home()

    @app.post("/api/ship/pick-folder")
    def ship_pick_folder() -> dict[str, Any]:
        """Native OS dialog — web file inputs cannot return absolute paths."""
        from openmw.openvault.ship.pick_folder import pick_local_folder

        return pick_local_folder()

    @app.post("/api/ship/library/inspect")
    def ship_library_inspect(body: LibraryInspectBody) -> dict[str, Any]:
        if body.github_url.strip():
            return inspect_github_url(body.github_url)
        if body.path.strip():
            return inspect_folder(body.path)
        raise HTTPException(status_code=400, detail="path or github_url required")

    @app.post("/api/ship/library/upload-session")
    def ship_upload_session() -> dict[str, Any]:
        return create_upload_session().to_dict()

    @app.post("/api/ship/library/upload-session/{session_id}/scan")
    def ship_upload_scan(session_id: str) -> dict[str, Any]:
        return scan_upload_session(session_id)

    @app.get("/api/ship/github/status")
    def ship_github_status() -> dict[str, Any]:
        return connection_status().to_dict()

    @app.post("/api/ship/github/connect")
    def ship_github_connect() -> dict[str, Any]:
        """Highest ship scopes via gh auth login (OpenShip local-auth steal)."""
        return start_gh_login()

    @app.post("/api/ship/github/pat")
    def ship_github_pat(body: GitHubPatBody) -> dict[str, Any]:
        return save_pat(body.token, note=body.note).to_dict()

    @app.delete("/api/ship/github/pat")
    def ship_github_pat_clear() -> dict[str, Any]:
        clear_pat()
        return connection_status().to_dict()

    @app.get("/api/ship/github/repos")
    def ship_github_repos() -> dict[str, Any]:
        return list_repos()

    @app.get("/api/ship/github/repos/{owner}/{repo}/branches")
    def ship_github_branches(owner: str, repo: str) -> dict[str, Any]:
        return list_branches(owner, repo)

    @app.post("/api/ship/engine")
    def ship_engine_run(body: ShipEngineBody) -> dict[str, Any]:
        return run_ship_engine(
            target=body.target,
            project_path=body.project_path,
            github_url=body.github_url,
            hostname=body.hostname,
            vps_host=body.vps_host,
            cloud_tier=body.cloud_tier,
            monthly_cap_usd=body.monthly_cap_usd,
            run_build=body.run_build,
            prefer_remote_openship=body.prefer_remote_openship,
        )

    @app.get("/api/ship/engine/{deployment_id}")
    def ship_engine_get(deployment_id: str) -> dict[str, Any]:
        dep = load_deployment(deployment_id)
        if dep is None:
            raise HTTPException(status_code=404, detail="deployment not found")
        return dep.to_dict()

    @app.get("/api/ship/targets")
    def ship_targets() -> dict[str, Any]:
        return list_targets()

    @app.post("/api/ship/blueprint")
    def ship_blueprint(body: ShipBlueprintBody) -> dict[str, Any]:
        return build_ship_blueprint(
            target=body.target,
            project_path=body.project_path,
            hostname=body.hostname,
            github_url=body.github_url,
            vps_host=body.vps_host,
            cloud_tier=body.cloud_tier,
            monthly_cap_usd=body.monthly_cap_usd,
        )

    @app.get("/api/ship/budget")
    def ship_budget_get() -> dict[str, Any]:
        return load_bill_budget().to_dict()

    @app.put("/api/ship/budget")
    def ship_budget_put(body: BillBudgetBody) -> dict[str, Any]:
        current = load_bill_budget()
        budget = BillBudget(
            monthly_cap_usd=body.monthly_cap_usd,
            spent_usd_estimate=(
                float(body.spent_usd_estimate)
                if body.spent_usd_estimate is not None
                else current.spent_usd_estimate
            ),
            soft_warn_pct=body.soft_warn_pct,
            hard_stop=body.hard_stop,
        )
        return save_bill_budget(budget).to_dict()

    @app.get("/api/ship/openship/status")
    def ship_openship_status() -> dict[str, Any]:
        status = adapter_status()
        client = OpenShipClient()
        live: dict[str, Any] = {}
        if client.available:
            live["cloud"] = client.cloud_status()
            live["billing"] = client.billing_state()
            client.close()
        return {**status, "live": live}

    @app.post("/api/ship/aws-plan")
    def ship_aws_plan(body: DomainGuideBody) -> dict[str, Any]:
        return build_aws_render_plan(hostname=body.hostname).to_dict()

    @app.post("/api/deploy/domain-guide")
    def deploy_domain_guide(body: DomainGuideBody) -> dict[str, Any]:
        return build_domain_guide(
            body.hostname,
            target_a=body.target_a,
            target_cname=body.target_cname,
            include_www=body.include_www,
            include_mail=body.include_mail,
        ).to_dict()

    @app.post("/api/deploy/cicd")
    def deploy_cicd_scan(body: DetectBody) -> dict[str, Any]:
        return detect_cicd(body.project_path).to_dict()

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

    @app.get("/api/vault/env-scan")
    def vault_env_scan(include_unknown: bool = False) -> dict[str, Any]:
        """Read-only: which env vars could be auto-vaulted. Masked values only."""
        candidates = scan_environment(include_unknown=include_unknown)
        return {
            "ok": True,
            "count": len(candidates),
            "candidates": [c.to_dict() for c in candidates],
        }

    @app.post("/api/vault/ingest-env")
    def vault_ingest_env(body: EnvIngestBody) -> dict[str, Any]:
        """Auto-retrieve provider secrets from the environment into the vault."""
        return ingest_environment(
            state_vault,
            dry_run=body.dry_run,
            include_unknown=body.include_unknown,
        )

    @app.get("/api/openfree/ratelimit")
    def openfree_ratelimit(identity: str = "local", tier: str = DEFAULT_TIER) -> dict[str, Any]:
        """OpenFree token-budget snapshot for a caller (tiers + remaining)."""
        return limiter.status(identity, tier=tier)

    @app.post("/v1/chat/completions")
    async def v1_chat(body: ChatBody, request: Request) -> JSONResponse:
        if body.stream:
            return JSONResponse(
                status_code=400,
                content={"error": {"message": "stream not supported yet", "type": "unsupported"}},
            )
        client_host = request.client.host if request.client is not None else "local"
        identity = request.headers.get("x-openfree-identity") or client_host
        tier = request.headers.get("x-openfree-tier") or DEFAULT_TIER
        # Reserve request + (prompt + max_tokens) budget up front (denial-of-wallet guard).
        prompt_tokens = estimate_prompt_tokens(body.messages)
        max_tokens = body.max_tokens if body.max_tokens is not None else DEFAULT_MAX_OUTPUT_TOKENS
        decision = limiter.reserve(
            identity, tier=tier, prompt_tokens=prompt_tokens, max_tokens=max_tokens
        )
        if not decision.allowed:
            return JSONResponse(
                status_code=429,
                content={
                    "error": {
                        "message": "OpenFree token budget exceeded; retry later",
                        "type": "rate_limited",
                        "limited_by": decision.limited_by,
                    }
                },
                headers=decision.headers(),
            )
        payload = body.model_dump(exclude_none=True)
        status, result = await chat_completions(state_vault, fallback, payload)
        # Refund the reservation down to actual usage (0 on upstream failure).
        actual = usage_total_tokens(result) if 200 <= status < 300 else 0
        if actual is None:
            actual = decision.reserved_tokens
        limiter.settle(
            identity,
            tier=tier,
            reserved_tokens=decision.reserved_tokens,
            actual_tokens=actual,
        )
        return JSONResponse(
            status_code=status,
            content=result,
            headers=limiter.headers_for(identity, tier=tier),
        )

    webui = bundled_webui_dir()
    if webui.is_dir():
        app_url = os.environ.get("OPENVAULT_APP_URL", "http://127.0.0.1:3010/")

        @app.get("/")
        def index() -> HTMLResponse:
            # Prefer the Next/Electron OpenVault app — legacy HTML is /legacy
            return HTMLResponse(
                f"""<!doctype html><meta charset=utf-8>
<title>OpenVault</title>
<meta http-equiv="refresh" content="0;url={app_url}">
<body style="font-family:system-ui;background:#0c0c0e;color:#eee;padding:40px">
<p>OpenVault app → <a href="{app_url}">{app_url}</a></p>
<p style="opacity:.6"><a href="/legacy">Legacy HTML console</a> (deprecated)</p>
</body>"""
            )

        @app.get("/legacy")
        def legacy_html() -> FileResponse:
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
