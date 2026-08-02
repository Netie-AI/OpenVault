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
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

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
    cortex_base_url,
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
from openmw.openvault.route.access import (
    AccessIntent,
    AccessKind,
    registry_payload,
    resolve_access,
    uptime_payload,
)
from openmw.openvault.ship.aws_guide import build_aws_render_plan
from openmw.openvault.ship.cicd import detect_cicd
from openmw.openvault.ship.cloud_targets import (
    BillBudget,
    build_ship_blueprint,
    list_targets,
    load_bill_budget,
    save_bill_budget,
)
from openmw.openvault.ship.deploy import (
    build_deploy_plan,
    execute_deploy,
    list_plans,
    load_plan,
    one_press_deploy,
    run_deploy_smoke,
)
from openmw.openvault.ship.detect import detect_project
from openmw.openvault.ship.domain_guide import build_domain_guide
from openmw.openvault.ship.engine import load_deployment, run_ship_engine
from openmw.openvault.ship.gate import check_gate
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
    receive_upload_bytes,
    scan_upload_session,
)
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
from openmw.openvault.vault.proxy import chat_completions, prepare_chat_stream
from openmw.openvault.vault.ratelimit import (
    DEFAULT_MAX_OUTPUT_TOKENS,
    DEFAULT_TIER,
    TokenBudgetLimiter,
    estimate_prompt_tokens,
    usage_total_tokens,
)
from openmw.openvault.vault.redis_store import try_make_redis_store
from openmw.openvault.vault.secrets import SecretKind, SecretStore, SecretValidationError
from openmw.openvault.vault.seed import seed_essentials
from openmw.openvault.vault.store import KeyRole, KeyVault, ProviderKind

log = structlog.get_logger()

# Guards on the custody routes. See reveal_secret for the reasoning.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
_REVEAL_INTENT_HEADER = "X-OpenVault-Reveal"


def _write_secret_audit(entry: dict[str, Any]) -> None:
    """Append one line to the secret-access audit log.

    Best-effort by design: an audit write that fails must not deny the caller
    an operation they are entitled to, but it must be loud. Kept separate from
    control_audit.jsonl so custody access can be shipped or reviewed on its own.

    Callers pass only identifiers and metadata. Nothing in ``entry`` may be
    derived from a decrypted payload — the audit file is not a second place a
    secret is allowed to exist, and tests pin that.
    """
    import json
    from datetime import datetime, timezone

    from openmw.openvault.paths import ensure_home

    entry = {"ts": datetime.now(timezone.utc).isoformat(), **entry}
    log.info(str(entry.get("event", "secret_event")), **{
        k: v for k, v in entry.items() if k not in ("ts", "event", "user_agent")
    })
    try:
        path = ensure_home() / "secret_audit.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")
    except OSError as exc:
        log.error("secret_audit_write_failed", error=str(exc), entry_event=entry.get("event"))


def _normalise_host(host: str) -> str:
    """Reduce a peer address to a bare hostname or IP for comparison.

    Three forms all mean "this machine" and only one of them used to match:

    * ``127.0.0.1`` / ``::1``  -- matched already.
    * ``::ffff:127.0.0.1``     -- the IPv4-mapped IPv6 form. A dual-stack socket
      reports this, and Node forwards it verbatim in ``X-Forwarded-For``, so
      every mutation issued through the Next console arrived looking remote and
      was refused. The console could list keys (reads are ungated) but could not
      create, rotate or delete one.
    * ``[::1]:5000`` / ``127.0.0.1:5000`` -- bracketed or port-suffixed.

    This is deliberately the same normalisation the TypeScript guard already
    performs in ``apps/web/src/server/authz/routeGuard.ts``. One rule, two
    implementations, and they had drifted -- the drift *was* the bug.

    Widening ``_LOOPBACK_HOSTS`` instead would be the wrong fix: it would have to
    enumerate every spelling forever, and ``::ffff:1.2.3.4`` must still be
    remote. Normalising first keeps the allowlist exact.
    """
    value = (host or "").strip()
    if value.startswith("["):
        end = value.find("]")
        value = value[1:end] if end >= 0 else value[1:]
    elif value.count(":") == 1:
        value = value.split(":")[0]
    if value.lower().startswith("::ffff:"):
        value = value[len("::ffff:"):]
    return value.lower()


def _client_host(request: Request) -> str:
    return request.client.host if request.client else ""


def _require_loopback(request: Request, action: str) -> str:
    """Reject anything that did not come from this machine.

    Applied to every custody mutation, not just reveal. Deleting or rotating a
    key is not a read, but it is just as final: a remote ``DELETE /api/keys/id``
    destroys a credential the user may have no other copy of, and a remote
    rotate silently swaps the secret every other surface is about to fetch.
    The bind address defaults to 127.0.0.1, but a default is a preference, not
    a control — this is the control.
    """
    host = _client_host(request)
    if _normalise_host(host) not in _LOOPBACK_HOSTS:
        log.warning("custody_request_rejected", reason="non_loopback", action=action, client=host)
        raise HTTPException(status_code=403, detail=f"{action} is loopback-only")
    return host


def _require_reveal_intent(request: Request) -> None:
    """Demand the custom intent header on any route that returns plaintext.

    A browser cannot attach a custom header cross-origin without surviving a
    preflight, so a page the user happens to have open cannot turn a drive-by
    GET into an exfiltration. Reserved for plaintext reads; mutations get
    loopback + audit but not this header, because the shipped consoles issue
    them without it and a gate that breaks the only UI gets deleted.
    """
    if request.headers.get(_REVEAL_INTENT_HEADER, "").strip().lower() != "intentional":
        raise HTTPException(
            status_code=428,
            detail=(
                f"secret reveal requires the {_REVEAL_INTENT_HEADER}: intentional "
                "header, sent from an explicit user action"
            ),
        )


def _audit_secret_reveal(key_id: str, client_host: str, user_agent: str) -> None:
    _write_secret_audit(
        {
            "event": "secret_reveal",
            "key_id": key_id,
            "client": client_host,
            "user_agent": user_agent[:200],
        }
    )


def _audit_custody(event: str, request: Request, **fields: Any) -> None:
    """Audit a custody mutation (create / update / delete / revoke / rotate)."""
    _write_secret_audit(
        {
            "event": event,
            "client": _client_host(request),
            "user_agent": request.headers.get("user-agent", "")[:200],
            **fields,
        }
    )


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


class AccessResolveBody(BaseModel):
    kind: AccessKind
    id: str
    intent: AccessIntent = "read"
    required_providers: list[str] = Field(default_factory=list)


class PasswordCreate(BaseModel):
    label: str
    password: str
    username: str = ""
    url: str = ""
    account_id: str | None = None


class CardCreate(BaseModel):
    label: str
    pan: str
    exp_month: int
    exp_year: int
    cardholder: str = ""
    account_id: str | None = None
    # Present so the refusal is explicit rather than a silently ignored field.
    # See vault/secrets.create_card.
    cvv: str | None = None


class SecretUpdate(BaseModel):
    label: str | None = None
    username: str | None = None
    url: str | None = None
    cardholder: str | None = None
    exp_month: int | None = None
    exp_year: int | None = None
    account_id: str | None = None


class SecretRotate(BaseModel):
    new_password: str | None = None
    new_pan: str | None = None
    exp_month: int | None = None
    exp_year: int | None = None
    label_suffix: str = "rotated"


class SecretRevoke(BaseModel):
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
    """An OpenAI-shaped chat request.

    `extra="allow"` is load-bearing. Pydantic drops undeclared fields silently, so
    this model used to delete `tools` and `tool_choice` on the way through - a caller
    could send 15 tool definitions, get a 200 back, and be told by the model that it
    had no tools. Nothing logged it, because nothing knew it had happened.

    A proxy has to be transparent about the fields it does not itself interpret;
    `top_p`, `stop`, `response_format`, `seed` and everything OpenAI adds next were
    being lost the same way. `tools` and `tool_choice` are also declared explicitly so
    they appear in openapi.json rather than being invisible to schema consumers.
    """

    model_config = ConfigDict(extra="allow")

    model: str = "auto"
    messages: list[dict[str, Any]]
    temperature: float | None = None
    max_tokens: int | None = None
    stream: bool | None = False
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] | None = None


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
        "cloudflare_pages", "openship_cloud", "vps_ssh", "aws_guide", "local_demo"
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
        "cloudflare_pages", "openship_cloud", "vps_ssh", "aws_guide", "local_demo"
    ] = "cloudflare_pages"
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
        "cloudflare_pages", "openship_cloud", "vps_ssh", "aws_guide", "local_demo"
    ] = "cloudflare_pages"
    project_path: str = ""
    github_url: str = ""
    hostname: str = ""
    vps_host: str = ""
    cloud_tier: str = "low"
    monthly_cap_usd: float | None = None
    run_build: bool = False
    prefer_remote_openship: bool = False


class ShipPreflightBody(BaseModel):
    target: Literal[
        "cloudflare_pages", "openship_cloud", "vps_ssh", "aws_guide", "local_demo"
    ] = "cloudflare_pages"


class ShipRecommendBody(BaseModel):
    project_path: str = ""
    stack: dict[str, Any] = Field(default_factory=dict)


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
    project_id: str | None = None
    github_url: str | None = None
    deploy_target: str = "cloud"


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


def create_app(
    *,
    vault: KeyVault | None = None,
    accounts: AccountStore | None = None,
    secrets: SecretStore | None = None,
    cortex_url: str | None = None,
    openide_url: str = OPENIDE_DEFAULT_URL,
    precheck_interval_s: float = 60.0,
    mock_health: bool = False,
    enable_precheck_loop: bool = True,
) -> FastAPI:
    if cortex_url is None:
        cortex_url = cortex_base_url()
    state_vault = vault if vault is not None else KeyVault()
    state_accounts = accounts if accounts is not None else AccountStore()
    state_secrets = secrets if secrets is not None else SecretStore()
    # Our own address, as the registry should advertise it. Matches how
    # mesh/local_mesh.py builds the self peer, so the two never disagree.
    self_url = f"http://127.0.0.1:{os.environ.get('OPENVAULT_PORT', '5000')}"
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
    from openmw.openvault.routers.health import build_health_router
    from openmw.openvault.routers.keys import router as keys_router
    from openmw.openvault.routers.route import router as route_router
    from openmw.openvault.routers.sentinel import router as sentinel_router
    from openmw.openvault.routers.ship import router as ship_router

    app.include_router(ship_router)
    app.include_router(sentinel_router)
    app.include_router(route_router)
    app.include_router(keys_router)
    app.include_router(build_health_router(state_vault))

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

    # --- Local mesh: OpenVault ↔ Cortex ↔ FreeIDE ---

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

    @app.get("/api/freeide/ready")
    @app.get("/api/openide/ready", include_in_schema=False)
    def freeide_ready(required_providers: str = "") -> dict[str, Any]:
        """Preflight FreeIDE Run: keys + mesh approval + gate, from the SoT.

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

    @app.post("/api/freeide/invoke")
    @app.post("/api/openide/invoke", include_in_schema=False)
    def api_freeide_invoke(body: OpenIdeInvoke) -> dict[str, Any]:
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
    def account_create_key(
        account_id: str, body: AccountKeyCreate, request: Request
    ) -> dict[str, Any]:
        _require_loopback(request, "key create")
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
        _audit_custody(
            "key_create", request, key_id=record.id, provider=record.provider, account_id=account_id
        )
        return asdict(record)

    @app.post("/api/accounts/{account_id}/incident")
    def account_incident(account_id: str, body: IncidentBody, request: Request) -> dict[str, Any]:
        # Kills every key on the account in one call — the single most
        # destructive custody route in the app, and until now the least guarded.
        _require_loopback(request, "account incident kill")
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
        _audit_custody(
            "account_incident_kill",
            request,
            account_id=account_id,
            reason=body.reason,
            killed=len(result.get("killed", []) or []),
        )
        return payload

    # --- Keys ---

    @app.get("/api/keys")
    def list_keys(account_id: str | None = None) -> dict[str, Any]:
        return {"keys": [asdict(k) for k in state_vault.list_keys(account_id=account_id)]}

    @app.get("/api/keyvault/snapshot")
    def api_keyvault_snapshot() -> dict[str, Any]:
        """AirGPT/FreeIDE Key Vault UI — OpenVault is SoT (PRODUCT_ROLES)."""
        return keyvault_snapshot(state_vault, openvault_url="http://127.0.0.1:5000")

    @app.post("/api/keyvault/upsert")
    def api_keyvault_upsert(body: KeyvaultUpsertBody, request: Request) -> dict[str, Any]:
        # Same custody write as POST /api/keys, reached by env_key instead of
        # provider — so it gets the same gate. Otherwise it is a way around one.
        _require_loopback(request, "keyvault upsert")
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
        # env_key names the slot, never the value.
        _audit_custody("keyvault_upsert", request, env_keys=sorted(batch), ok=ok)
        return {"ok": ok, "results": results, "snapshot": keyvault_snapshot(state_vault)}

    # --- Access routing: where does it live, and may this caller go there? ---
    #
    # The layer above /api/gate/check. The gate answers "may I do X"; this
    # answers "where is X, who owns it, and may I". Deliberately not a store
    # and not an orchestrator — memory belongs to Cortex, the agent loop belongs
    # to Cortex, and this returns locations and verdicts, never content.
    # See route/access.py and PRODUCT_ROLES.md locks 2, 3 and 5.

    @app.get("/api/access/registry")
    def api_access_registry(kind: AccessKind | None = None) -> dict[str, Any]:
        """Everything OpenVault can route to, derived from live mesh + vault state."""
        return registry_payload(vault=state_vault, kind=kind, openvault_url=self_url)

    @app.post("/api/access/resolve")
    def api_access_resolve(body: AccessResolveBody) -> dict[str, Any]:
        """Locate one resource and gate it in a single answer.

        Returns 200 with ``found: false`` for an unknown id rather than a 404:
        a resolver that stays silent about what it does not know is one callers
        learn to route around. The verdict is always present.
        """
        result = resolve_access(
            vault=state_vault,
            kind=body.kind,
            resource_id=body.id,
            intent=body.intent,
            fallback=fallback,
            openvault_url=self_url,
            required_providers=list(body.required_providers) or None,
        )
        if not result.allowed:
            log.info(
                "access_denied",
                kind=result.kind,
                resource=result.id,
                intent=result.intent,
                reasons=result.reasons,
            )
        return result.to_dict()

    @app.get("/api/access/uptime")
    def api_access_uptime() -> dict[str, Any]:
        """Uptime for this process and every mesh surface. ``up: null`` = unprobed."""
        return uptime_payload()

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
    def create_key(body: KeyCreate, request: Request) -> dict[str, Any]:
        _require_loopback(request, "key create")
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
        _audit_custody("key_create", request, key_id=record.id, provider=record.provider)
        return asdict(record)

    @app.patch("/api/keys/{key_id}")
    def patch_key(key_id: str, body: KeyUpdate, request: Request) -> dict[str, Any]:
        _require_loopback(request, "key update")
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
        # Whether the payload itself changed is the part that matters on review.
        _audit_custody(
            "key_update", request, key_id=key_id, secret_replaced=body.secret is not None
        )
        return asdict(record)

    @app.delete("/api/keys/{key_id}")
    def delete_key(key_id: str, request: Request) -> dict[str, bool]:
        _require_loopback(request, "key delete")
        ok = state_vault.delete(key_id)
        if not ok:
            raise HTTPException(status_code=404, detail="key not found")
        _audit_custody("key_delete", request, key_id=key_id)
        return {"deleted": True}

    @app.post("/api/keys/{key_id}/revoke")
    def revoke_key(key_id: str, body: KeyRevoke, request: Request) -> dict[str, Any]:
        _require_loopback(request, "key revoke")
        record = state_vault.revoke(key_id, reason=body.reason)
        if record is None:
            raise HTTPException(status_code=404, detail="key not found")
        _audit_custody("key_revoke", request, key_id=key_id, reason=body.reason)
        return asdict(record)

    @app.post("/api/keys/{key_id}/rotate")
    def rotate_key(key_id: str, body: KeyRotate, request: Request) -> dict[str, Any]:
        _require_loopback(request, "key rotate")
        record = state_vault.rotate(
            key_id, new_secret=body.new_secret, label_suffix=body.label_suffix
        )
        if record is None:
            raise HTTPException(status_code=404, detail="key not found")
        _audit_custody("key_rotate", request, key_id=key_id, replaced_by=record.id)
        return asdict(record)

    @app.get("/api/keys/{key_id}/secret")
    def reveal_secret(key_id: str, request: Request) -> dict[str, str]:
        """Return one decrypted secret. Loopback + explicit intent + audited.

        This is the single most dangerous route in the process: it hands back
        plaintext, and there is no authentication anywhere in this app. Three
        cheap controls, none of which is a substitute for real auth:

        1. Loopback only. A vault reachable from the LAN is not a vault.
        2. A custom request header. Browsers cannot attach one cross-origin
           without a preflight the caller must survive, so a drive-by GET from
           a page the user happens to have open cannot exfiltrate keys.
        3. An audit line per reveal. Hardware actions were already audited
           while secret access was not, which is exactly backwards.

        Tracked in docs/BACKEND_HONESTY_AUDIT.md section 1.
        """
        client_host = _require_loopback(request, "secret reveal")
        _require_reveal_intent(request)

        secret = state_vault.get_secret(key_id)
        if secret is None:
            raise HTTPException(status_code=404, detail="key not found")

        _audit_secret_reveal(key_id, client_host, request.headers.get("user-agent", ""))
        return {"id": key_id, "secret": secret}

    # --- Passwords + payment cards (vault/secrets.py) ---
    #
    # Same custody, same master key, same gate pattern as /api/keys. Separate
    # route family because these are not routable credentials: nothing in
    # fallback, precheck, or the proxy should ever see a card.

    @app.get("/api/secrets")
    def list_secrets(
        kind: SecretKind | None = None, account_id: str | None = None
    ) -> dict[str, Any]:
        """Masked list. Safe to poll — no decryption happens on this path."""
        records = state_secrets.list_secrets(kind=kind, account_id=account_id)
        return {"secrets": [asdict(s) for s in records]}

    @app.post("/api/secrets/passwords")
    def create_password(body: PasswordCreate, request: Request) -> dict[str, Any]:
        _require_loopback(request, "password create")
        if body.account_id and state_accounts.get(body.account_id) is None:
            raise HTTPException(status_code=404, detail="account not found")
        try:
            record = state_secrets.create_password(
                label=body.label,
                password=body.password,
                username=body.username,
                url=body.url,
                account_id=body.account_id,
            )
        except SecretValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        _audit_custody("password_create", request, secret_id=record.id, label=record.label)
        return asdict(record)

    @app.post("/api/secrets/cards")
    def create_card(body: CardCreate, request: Request) -> dict[str, Any]:
        _require_loopback(request, "card create")
        if body.account_id and state_accounts.get(body.account_id) is None:
            raise HTTPException(status_code=404, detail="account not found")
        try:
            record = state_secrets.create_card(
                label=body.label,
                pan=body.pan,
                exp_month=body.exp_month,
                exp_year=body.exp_year,
                cardholder=body.cardholder,
                account_id=body.account_id,
                cvv=body.cvv,
            )
        except SecretValidationError as exc:
            # The PAN is in the request body; it must not reach the audit file
            # or the log, so the rejection carries only the reason.
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        _audit_custody(
            "card_create", request, secret_id=record.id, brand=record.brand, last4=record.last4
        )
        return asdict(record)

    @app.patch("/api/secrets/{secret_id}")
    def patch_secret(secret_id: str, body: SecretUpdate, request: Request) -> dict[str, Any]:
        _require_loopback(request, "secret update")
        try:
            record = state_secrets.update(
                secret_id,
                label=body.label,
                username=body.username,
                url=body.url,
                cardholder=body.cardholder,
                exp_month=body.exp_month,
                exp_year=body.exp_year,
                account_id=body.account_id,
            )
        except SecretValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if record is None:
            raise HTTPException(status_code=404, detail="secret not found")
        _audit_custody("secret_update", request, secret_id=secret_id)
        return asdict(record)

    @app.delete("/api/secrets/{secret_id}")
    def delete_secret(secret_id: str, request: Request) -> dict[str, bool]:
        _require_loopback(request, "secret delete")
        if not state_secrets.delete(secret_id):
            raise HTTPException(status_code=404, detail="secret not found")
        _audit_custody("secret_delete", request, secret_id=secret_id)
        return {"deleted": True}

    @app.post("/api/secrets/{secret_id}/revoke")
    def revoke_secret(secret_id: str, body: SecretRevoke, request: Request) -> dict[str, Any]:
        _require_loopback(request, "secret revoke")
        record = state_secrets.revoke(secret_id, reason=body.reason)
        if record is None:
            raise HTTPException(status_code=404, detail="secret not found")
        _audit_custody("secret_revoke", request, secret_id=secret_id, reason=body.reason)
        return asdict(record)

    @app.post("/api/secrets/{secret_id}/rotate")
    def rotate_secret(secret_id: str, body: SecretRotate, request: Request) -> dict[str, Any]:
        _require_loopback(request, "secret rotate")
        try:
            record = state_secrets.rotate(
                secret_id,
                new_password=body.new_password,
                new_pan=body.new_pan,
                exp_month=body.exp_month,
                exp_year=body.exp_year,
                label_suffix=body.label_suffix,
            )
        except SecretValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if record is None:
            raise HTTPException(status_code=404, detail="secret not found")
        _audit_custody("secret_rotate", request, secret_id=secret_id, replaced_by=record.id)
        return asdict(record)

    @app.get("/api/secrets/{secret_id}/reveal")
    def reveal_secret_value(secret_id: str, request: Request) -> dict[str, Any]:
        """Return one password or one full PAN. Loopback + intent + audited.

        Identical gate to ``/api/keys/{id}/secret`` — see that route for why
        each of the three controls exists. One addition specific to cards: the
        response echoes ``kind`` and ``last4`` so a caller can confirm it got
        the card it asked for without having to parse the PAN it just received,
        and the audit line records ``last4`` only.
        """
        client_host = _require_loopback(request, "secret reveal")
        _require_reveal_intent(request)

        record = state_secrets.get(secret_id)
        if record is None:
            raise HTTPException(status_code=404, detail="secret not found")

        plaintext = state_secrets.reveal(secret_id)
        if plaintext is None:
            raise HTTPException(status_code=404, detail="secret not found")

        _write_secret_audit(
            {
                "event": "secret_reveal",
                "secret_id": secret_id,
                "kind": record.kind,
                "last4": record.last4,
                "lifecycle": record.lifecycle,
                "client": client_host,
                "user_agent": request.headers.get("user-agent", "")[:200],
            }
        )
        return {
            "id": secret_id,
            "kind": record.kind,
            "last4": record.last4,
            "brand": record.brand,
            "secret": plaintext,
        }

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
        """One-press via in-process ship engine (FreeBuild concepts stolen locally)."""
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

    @app.post("/api/ship/library/upload-session/{session_id}/files")
    async def ship_upload_files(session_id: str, request: Request) -> dict[str, Any]:
        """Accept zip or file bytes into the staging dir (real upload path)."""
        form = await request.form()
        results: list[dict[str, Any]] = []
        for _key, value in form.multi_items():
            filename = getattr(value, "filename", None)
            if not filename or not hasattr(value, "read"):
                continue
            data = await value.read()  # type: ignore[misc]
            rel = str(form.get("relative_path") or "") if _key == "file" else ""
            results.append(
                receive_upload_bytes(
                    session_id,
                    filename=str(filename),
                    data=bytes(data),
                    relative_path=rel,
                )
            )
        if not results:
            raise HTTPException(status_code=400, detail="multipart file field required")
        ok = all(r.get("ok") for r in results)
        total = sum(int(r.get("files_written") or 0) for r in results)
        return {"ok": ok, "files_written": total, "results": results}

    @app.post("/api/ship/library/upload-session/{session_id}/scan")
    def ship_upload_scan(session_id: str) -> dict[str, Any]:
        return scan_upload_session(session_id)

    @app.post("/api/ship/preflight")
    def ship_preflight(body: ShipPreflightBody) -> dict[str, Any]:
        """Refuse before build when token/CLI missing — costs a second, not five minutes."""
        if body.target == "cloudflare_pages":
            from openmw.openvault.ship.hosts.cloudflare_pages import (
                CloudflarePagesAdapter,
                from_vault,
            )

            def _secret(provider: str) -> str | None:
                for rec in state_vault.list_keys():
                    if (
                        rec.enabled
                        and rec.lifecycle == "active"
                        and (
                            rec.provider == provider
                            or provider in (rec.label or "").lower()
                            or "cloudflare" in (rec.label or "").lower()
                        )
                    ):
                        return state_vault.get_secret(rec.id)
                return None

            try:
                adapter = from_vault(_secret)
            except Exception:
                adapter = CloudflarePagesAdapter(
                    api_token=os.environ.get("CLOUDFLARE_API_TOKEN"),
                    account_id=os.environ.get("CLOUDFLARE_ACCOUNT_ID"),
                )
            pre = adapter.preflight()
            return {
                "ok": pre.ready,
                "ready": pre.ready,
                "target": body.target,
                "blocker": pre.blocker,
                "facts": pre.facts,
                "real_publish": True,
            }
        if body.target == "local_demo":
            return {
                "ok": True,
                "ready": True,
                "target": body.target,
                "blocker": "",
                "facts": {"mode": "demo"},
                "real_publish": False,
            }
        return {
            "ok": False,
            "ready": False,
            "target": body.target,
            "blocker": (
                f"{body.target} is not a real host adapter yet — use Cloudflare Pages "
                "for a free first publish, or local_demo to simulate."
            ),
            "facts": {},
            "real_publish": False,
        }

    @app.post("/api/ship/recommend")
    def ship_recommend(body: ShipRecommendBody) -> dict[str, Any]:
        from openmw.openvault.ship.cloud_targets import TARGET_CARDS
        from openmw.openvault.ship.recommend import recommend_target

        stack = dict(body.stack or {})
        if body.project_path.strip() and not stack:
            stack = detect_project(body.project_path.strip()).to_dict()
        sponsored = {t.id for t in TARGET_CARDS if t.sponsored}
        out = recommend_target(stack, sponsored_ids=sponsored)
        out["stack"] = stack
        return out

    @app.get("/api/ship/github/status")
    def ship_github_status() -> dict[str, Any]:
        return connection_status().to_dict()

    @app.post("/api/ship/github/connect")
    def ship_github_connect() -> dict[str, Any]:
        """Highest ship scopes via gh auth login (FreeBuild local-auth steal)."""
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

    @app.get("/api/ship/engine/{deployment_id}/stream", response_model=None)
    async def ship_engine_stream(
        deployment_id: str,
        request: Request,
        lastEventId: int = 0,
    ) -> StreamingResponse:
        """SSE replay of a saved deployment — contract: apps/web/src/lib/sse/frames.ts."""
        from openmw.openvault.ship.stream import stream_deployment

        header_last = 0
        raw = request.headers.get("last-event-id") or request.headers.get("Last-Event-ID")
        if raw and raw.isdigit():
            header_last = int(raw)
        start_after = max(lastEventId, header_last)

        async def _gen() -> AsyncIterator[bytes]:
            async for chunk in stream_deployment(
                deployment_id, last_event_id=start_after, pace_s=0.015
            ):
                yield chunk

        return StreamingResponse(
            _gen(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

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

    @app.get("/api/ship/freebuild/status")
    @app.get("/api/ship/openship/status", include_in_schema=False)
    def ship_freebuild_status() -> dict[str, Any]:
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

    # --- FreeBuild full clone surface ---

    # --- FreeBuild (formerly OpenShip) ---
    #
    # Canonical paths are /api/freebuild*. The /api/freebuild* paths stay
    # registered and behave identically, hidden from the schema so the docs
    # show one name while shipped clients keep working. Deleting them is a
    # separate, announced change — a rename that breaks callers on the day it
    # lands is how a rename gets reverted.

    @app.post("/api/freebuild/plan")
    @app.post("/api/openship/plan", include_in_schema=False)
    def freebuild_plan(body: OpenShipBody) -> dict[str, Any]:
        plan = build_openship_plan(
            project_path=body.project_path,
            subdomain=body.subdomain,
            action=body.action,
            sending_ip=body.sending_ip,
        )
        if body.execute:
            plan = execute_openship_plan(plan, simulate=body.simulate)
        return plan.to_dict()

    @app.get("/api/freebuild")
    @app.get("/api/openship", include_in_schema=False)
    def freebuild_list() -> dict[str, Any]:
        return {"ships": list_ship_plans()}

    @app.get("/api/freebuild/{ship_id}")
    @app.get("/api/openship/{ship_id}", include_in_schema=False)
    def freebuild_get(ship_id: str) -> dict[str, Any]:
        plan = load_ship_plan(ship_id)
        if plan is None:
            raise HTTPException(status_code=404, detail="freebuild plan not found")
        return plan.to_dict()

    @app.post("/api/freebuild/{ship_id}/execute")
    @app.post("/api/openship/{ship_id}/execute", include_in_schema=False)
    def freebuild_execute(ship_id: str, body: DeployExecuteBody) -> dict[str, Any]:
        plan = load_ship_plan(ship_id)
        if plan is None:
            raise HTTPException(status_code=404, detail="freebuild plan not found")
        # Remote FreeBuild refuses without project_id — fail loud before HTTP round-trip.
        from openmw.openvault.ship.openship import adapter_presence

        adapter = adapter_presence()
        force_sim = body.simulate if body.simulate is not None else False
        if (
            not force_sim
            and adapter.get("api_ready")
            and not (body.project_id or "").strip()
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    "project_id required for remote FreeBuild execute — "
                    "pass project_id or set simulate=true for local simulation"
                ),
            )
        return execute_openship_plan(
            plan,
            simulate=body.simulate,
            project_id=body.project_id,
            github_url=body.github_url,
            deploy_target=body.deploy_target,
        ).to_dict()

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
    def vault_ingest_env(body: EnvIngestBody, request: Request) -> dict[str, Any]:
        """Auto-retrieve provider secrets from the environment into the vault."""
        _require_loopback(request, "env ingest")
        result = ingest_environment(
            state_vault,
            dry_run=body.dry_run,
            include_unknown=body.include_unknown,
        )
        if not body.dry_run:
            _audit_custody("env_ingest", request, imported=result.get("imported", 0))
        return result

    @app.get("/api/freeroute/ratelimit")
    @app.get("/api/openfree/ratelimit", include_in_schema=False)
    def freeroute_ratelimit(identity: str = "local", tier: str = DEFAULT_TIER) -> dict[str, Any]:
        """FreeRoute token-budget snapshot for a caller (tiers + remaining).

        Formerly OpenFree; the old path stays as a hidden alias.
        """
        return limiter.status(identity, tier=tier)

    @app.post("/v1/chat/completions", response_model=None)
    async def v1_chat(body: ChatBody, request: Request) -> JSONResponse | StreamingResponse:
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
                        "message": "FreeRoute token budget exceeded; retry later",
                        "type": "rate_limited",
                        "limited_by": decision.limited_by,
                    }
                },
                headers=decision.headers(),
            )
        payload = body.model_dump(exclude_none=True)
        rate_headers = limiter.headers_for(identity, tier=tier)

        if body.stream:
            status, result = await prepare_chat_stream(state_vault, fallback, payload)
            if isinstance(result, dict):
                limiter.settle(
                    identity,
                    tier=tier,
                    reserved_tokens=decision.reserved_tokens,
                    actual_tokens=0,
                )
                return JSONResponse(
                    status_code=status,
                    content=result,
                    headers=limiter.headers_for(identity, tier=tier),
                )

            async def _stream() -> Any:
                try:
                    async for chunk in result:
                        yield chunk
                finally:
                    # Stream usage is usually absent; keep the reservation (no refund).
                    limiter.settle(
                        identity,
                        tier=tier,
                        reserved_tokens=decision.reserved_tokens,
                        actual_tokens=decision.reserved_tokens,
                    )

            return StreamingResponse(
                _stream(),
                status_code=status,
                media_type="text/event-stream",
                headers={
                    **rate_headers,
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                },
            )

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

    app_url = os.environ.get("OPENVAULT_APP_URL", "http://127.0.0.1:3010/")

    @app.get("/")
    def index() -> HTMLResponse:
        """Custody API root — send humans to the Next app on :3010."""
        return HTMLResponse(
            f"""<!doctype html><meta charset=utf-8>
<title>OpenVault</title>
<meta http-equiv="refresh" content="0;url={app_url}">
<body style="font-family:system-ui;background:#0c0c0e;color:#eee;padding:40px">
<p>OpenVault app → <a href="{app_url}">{app_url}</a></p>
<p style="opacity:.6">API health: <a href="/api/healthz">/api/healthz</a></p>
</body>"""
        )

    return app


def run_console(
    *,
    host: str = "127.0.0.1",
    port: int = 5000,
    cortex_url: str | None = None,
    mock_health: bool = False,
    precheck_interval_s: float = 60.0,
) -> None:
    import uvicorn

    if cortex_url is None:
        cortex_url = cortex_base_url()
    app = create_app(
        cortex_url=cortex_url,
        mock_health=mock_health,
        precheck_interval_s=precheck_interval_s,
    )
    log.info("openvault_console_start", host=host, port=port, cortex_url=cortex_url)
    uvicorn.run(app, host=host, port=port, log_level="info")
