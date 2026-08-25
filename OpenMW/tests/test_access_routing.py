"""The access surface: resolve where a thing lives, then gate whether to go.

Two things these tests exist to stop:

1. **This module quietly becoming a store or an orchestrator.** PRODUCT_ROLES
   lock 5 says no second vault and no third orchestrator, and Cortex already
   owns ``/api/memory/*``. So a memory resolve must return Cortex's *location*
   and a verdict, never memory content — pinned by
   ``test_memory_resolves_to_cortex_and_returns_no_content``.
2. **A location handed out without a verdict.** "Omni-retrieve without OpenVault
   as gate = unsafe" is the contract's own wording; every resolve carries a gate
   decision, including the ones that say no.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from openmw.openvault.app import create_app
from openmw.openvault.route.access import SIGNPOST_FORBIDDEN_FIELDS


def _client() -> TestClient:
    app = create_app(mock_health=True, enable_precheck_loop=False)
    return TestClient(app, client=("127.0.0.1", 5555))


def _add_key(client: TestClient, provider: str = "groq", secret: str = "gsk-x") -> dict:
    res = client.post(
        "/api/keys",
        json={"label": provider.upper(), "provider": provider, "secret": secret, "role": "primary"},
    )
    assert res.status_code == 200, res.text
    return res.json()


# --- registry ---


def test_registry_lists_every_kind(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    body = _client().get("/api/access/registry").json()

    assert set(body["kinds"]) == {
        "memory",
        "api",
        "component",
        "runtime",
        "model",
        "service",
        "skill",
        "mcp",
    }
    ids = {e["id"] for e in body["entries"]}
    assert "cortex.memory" in ids
    assert "cortex.skills" in ids
    assert "cortex.mcp" in ids
    assert "runtime.crew" in ids
    assert "service.freeroute" in ids
    assert "service.freebuild" in ids
    assert "model.slots" in ids


def test_registry_reflects_real_vault_state(tmp_path, monkeypatch):
    """API entries are derived from keys actually held, not a hardcoded list."""
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    client = _client()

    before = {e["id"] for e in client.get("/api/access/registry").json()["entries"]}
    assert "provider.groq" not in before

    _add_key(client, "groq")
    after = client.get("/api/access/registry").json()["entries"]
    groq = next(e for e in after if e["id"] == "provider.groq")
    assert groq["owner"] == "openvault"
    assert groq["meta"]["active_keys"] == 1


def test_registry_filters_by_kind(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    body = _client().get("/api/access/registry", params={"kind": "memory"}).json()
    assert {e["kind"] for e in body["entries"]} == {"memory"}


def test_registry_never_carries_a_secret(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    client = _client()
    _add_key(client, "groq", "gsk-supersecret-value")

    assert "gsk-supersecret-value" not in client.get("/api/access/registry").text


# Skill bodies, system prompts, and MCP tool schemas belong in Cortex (DR-0012).
# A registry that starts carrying them has become the second store lock 5 forbids.


def test_access_registry_is_not_a_skill_store(tmp_path, monkeypatch):
    """DR-0012: skill/mcp kinds are signposts; they do not hold skill text."""
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    body = _client().get("/api/access/registry").json()

    assert "skill" in body["kinds"]
    assert "mcp" in body["kinds"]
    assert "run the agent loop" in body["policy"]
    assert "hold skill bodies" in body["policy"]
    for entry in body["entries"]:
        assert not (SIGNPOST_FORBIDDEN_FIELDS & set(entry))
        meta = entry.get("meta") or {}
        assert not (SIGNPOST_FORBIDDEN_FIELDS & set(meta))
        if entry["kind"] in ("skill", "mcp"):
            assert entry["owner"] == "cortex"


# --- resolve: location + verdict, never content ---


def test_memory_resolves_to_cortex_and_returns_no_content(tmp_path, monkeypatch):
    """Lock 5: Cortex owns memory. We hand back an address, not the memory."""
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    client = _client()
    _add_key(client, "ollama", "local")

    body = client.post(
        "/api/access/resolve", json={"kind": "memory", "id": "cortex.memory", "intent": "read"}
    ).json()

    assert body["found"] is True
    assert body["owner"] == "cortex"
    assert body["location"].endswith("/api/memory")
    # A location and a verdict. Nothing that looks like stored content.
    assert set(body) == {
        "found",
        "allowed",
        "kind",
        "id",
        "intent",
        "owner",
        "location",
        "reasons",
        "gate",
        "entry",
    }


def test_skill_resolves_to_cortex_and_returns_no_content(tmp_path, monkeypatch):
    """DR-0012: Cortex owns skill bodies. We hand back an address, not the skill."""
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    client = _client()
    _add_key(client, "ollama", "local")

    body = client.post(
        "/api/access/resolve",
        json={"kind": "skill", "id": "cortex.skills", "intent": "invoke"},
    ).json()

    assert body["found"] is True
    assert body["owner"] == "cortex"
    assert body["location"].endswith("/api/skills")
    assert not (SIGNPOST_FORBIDDEN_FIELDS & set(body))
    assert not (SIGNPOST_FORBIDDEN_FIELDS & set(body.get("entry") or {}))


def test_mcp_resolves_to_cortex_and_returns_no_content(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    client = _client()
    _add_key(client, "ollama", "local")

    body = client.post(
        "/api/access/resolve",
        json={"kind": "mcp", "id": "cortex.mcp", "intent": "invoke"},
    ).json()

    assert body["found"] is True
    assert body["owner"] == "cortex"
    assert body["location"].endswith("/api/mcp")


def test_crew_gate_is_resolve_plus_audit(tmp_path, monkeypatch):
    """Cortex crew talks here. OpenVault still does not spawn the child."""
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    client = _client()
    _add_key(client, "ollama", "local")

    body = client.post(
        "/api/crew/gate",
        json={
            "kind": "skill",
            "id": "cortex.skills",
            "intent": "invoke",
            "parent_run_id": "run-parent-1",
            "child_id": "child-outreach",
            "deficit": "need skill outreach.human-email",
        },
    ).json()

    assert body["found"] is True
    assert body["owner"] == "cortex"
    assert body["parent_run_id"] == "run-parent-1"
    assert body["child_id"] == "child-outreach"
    assert body["deficit"] == "need skill outreach.human-email"
    assert "skill_body" not in body
    assert "skill_body" not in (body.get("entry") or {})


def test_every_resolve_carries_a_gate_decision(tmp_path, monkeypatch):
    """'Omni-retrieve without OpenVault as gate = unsafe' — PRODUCT_ROLES."""
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    client = _client()
    _add_key(client, "ollama", "local")

    body = client.post(
        "/api/access/resolve", json={"kind": "service", "id": "service.freebuild", "intent": "read"}
    ).json()
    assert body["gate"]["action"] == "retrieve"
    assert "allowed" in body["gate"]


def test_unknown_id_is_a_verdict_not_a_404(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    res = _client().post(
        "/api/access/resolve", json={"kind": "memory", "id": "nope.nothing", "intent": "read"}
    )
    assert res.status_code == 200
    body = res.json()
    assert body["found"] is False
    assert body["allowed"] is False
    assert "nope.nothing" in body["reasons"][0]


def test_deploy_intent_is_blocked_by_an_empty_vault(tmp_path, monkeypatch):
    """Deploy escalates to the leave-machine gate, which needs real keys."""
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    body = (
        _client()
        .post(
            "/api/access/resolve",
            json={"kind": "service", "id": "service.freebuild", "intent": "deploy"},
        )
        .json()
    )

    assert body["found"] is True
    assert body["allowed"] is False
    assert body["gate"]["action"] == "deploy"
    assert any("no enabled keys" in r for r in body["reasons"])


def test_local_runtime_read_is_allowed_without_cloud_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    client = _client()
    _add_key(client, "ollama", "local")

    body = client.post(
        "/api/access/resolve", json={"kind": "runtime", "id": "runtime.local", "intent": "read"}
    ).json()
    assert body["allowed"] is True


def test_intent_maps_to_the_right_gate_action(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    client = _client()
    _add_key(client, "ollama", "local")

    for intent, action in (
        ("read", "retrieve"),
        ("write", "run"),
        ("invoke", "run"),
        ("deploy", "deploy"),
        ("leave", "leave"),
        ("connect", "connect"),
    ):
        body = client.post(
            "/api/access/resolve",
            json={"kind": "model", "id": "model.slots", "intent": intent},
        ).json()
        assert body["gate"]["action"] == action, intent


def test_bogus_intent_is_rejected_by_validation(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    res = _client().post(
        "/api/access/resolve",
        json={"kind": "model", "id": "model.slots", "intent": "exfiltrate"},
    )
    assert res.status_code == 422


# --- uptime ---


def test_uptime_reports_self_and_surfaces(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    body = _client().get("/api/access/uptime").json()

    assert body["openvault"]["up"] is True
    assert body["openvault"]["uptime_seconds"] >= 0
    ids = {s["id"] for s in body["surfaces"]}
    assert {"cortex", "openide", "openvault"} <= ids


def test_unprobed_surface_is_null_not_down(tmp_path, monkeypatch):
    """Collapsing 'never probed' into 'down' trains everyone to ignore the panel."""
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    body = _client().get("/api/access/uptime").json()

    cortex = next(s for s in body["surfaces"] if s["id"] == "cortex")
    assert cortex["up"] is None
    assert body["summary"]["unknown"] >= 1
    assert (
        body["summary"]["up"] + body["summary"]["down"] + body["summary"]["unknown"]
        == body["summary"]["total"]
    )


# --- Open* → Free* aliases ---


def test_free_paths_and_legacy_aliases_agree(tmp_path, monkeypatch):
    """A rename that breaks callers on the day it lands is a rename that gets reverted."""
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    client = _client()

    for new, old in (
        ("/api/freebuild", "/api/openship"),
        ("/api/freeroute/ratelimit", "/api/openfree/ratelimit"),
        ("/api/ship/freebuild/status", "/api/ship/openship/status"),
    ):
        a, b = client.get(new), client.get(old)
        assert a.status_code == 200, new
        assert a.status_code == b.status_code, f"{new} vs {old}"
        assert a.json() == b.json(), f"{new} vs {old}"


def test_no_route_path_is_registered_twice(tmp_path, monkeypatch):
    """A stacked-decorator alias that accidentally repeats the canonical path.

    FastAPI accepts duplicate registrations and silently serves the first match,
    so the legacy path just vanishes with no error anywhere. That is exactly how
    a bulk rename ate the aliases in this file's own history — the sweep rewrote
    the alias decorators along with everything else, and nothing complained.
    """
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    app = create_app(mock_health=True, enable_precheck_loop=False)

    seen: set[tuple[str, str]] = set()
    dupes: list[tuple[str, str]] = []
    for route in app.routes:
        path = getattr(route, "path", "")
        for method in sorted(getattr(route, "methods", None) or []):
            if method in ("HEAD", "OPTIONS"):
                continue
            if (method, path) in seen:
                dupes.append((method, path))
            seen.add((method, path))
    assert not dupes, f"duplicate route registrations: {dupes}"


def test_only_the_free_paths_are_documented(tmp_path, monkeypatch):
    """Legacy aliases keep working but must not appear in the OpenAPI schema."""
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    paths = _client().get("/openapi.json").json()["paths"]

    assert "/api/freebuild" in paths
    assert "/api/freeroute/ratelimit" in paths
    assert "/api/freeide/ready" in paths
    for legacy in ("/api/openship", "/api/openfree/ratelimit", "/api/openide/ready"):
        assert legacy not in paths, f"{legacy} should be hidden from the schema"
