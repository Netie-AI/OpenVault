"""Add a catalog provider key after a 1-token chat and HMAC dedupe.

Provider cards call :func:`add_tested_key`. The CLI is ``openvault add``.
The key is read from a hidden prompt or stdin, never from argv or an env var.
"""

from __future__ import annotations

import getpass
import hashlib
import hmac
import os
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TextIO, cast

import httpx
import structlog

from openmw.openvault.vault.airgpt_keyvault import PROVIDER_TO_ENV
from openmw.openvault.vault.crypto import VaultSealedError
from openmw.openvault.vault.providers import ProviderSpec, get_provider
from openmw.openvault.vault.store import KeyRole, KeyVault, ProviderKind

log = structlog.get_logger()

_SAFE_PROVIDER_ID = re.compile(r"[a-z0-9_]{1,32}\Z")
_KEY_ROLES: tuple[KeyRole, ...] = ("primary", "backup", "cheap", "free")
_EXTRA_KEY_ENV = frozenset(
    {
        "OPENVAULT_KEY",
        "OPENVAULT_API_KEY",
        "OPENVAULT_SECRET",
        "OPENVAULT_ADD_KEY",
        "GEMINI_API_KEY",
        "NVIDIA_NIM_API_KEY",
        "FREENVIDIA_API_KEY",
        "GH_TOKEN",
        "HUGGINGFACE_API_KEY",
        "HUGGING_FACE_HUB_TOKEN",
        "CURSOR_API_KEY",
        "CLOUDFLARE_API_TOKEN",
        "CF_API_TOKEN",
        "OMNIROUTE_API_KEY",
    }
)
ARGV_KEY_ERROR = (
    "refusing a key passed on the command line; paste it at the hidden prompt or pipe it on stdin"
)
_USAGE = """\
usage: openvault add <provider>

<provider> is a catalog id.
The API key is read from a hidden prompt when stdin is a TTY, otherwise from stdin.
A key on the command line or in an environment variable is refused.
"""


@dataclass(frozen=True)
class ProbeResult:
    """Outcome of the 1-token chat. No request or response body."""

    ok: bool
    http_status: int | None
    error: str = ""


@dataclass(frozen=True)
class AddResult:
    """Safe to print. Never includes the provider key."""

    ok: bool
    label: str
    masked_id: str
    key_id: str = ""
    duplicate: bool = False
    error: str = ""


def mask_key_id(key_id: str) -> str:
    """Short public form of a vault key id."""
    if len(key_id) <= 8:
        return "*" * len(key_id)
    return f"{key_id[:4]}...{key_id[-4:]}"


def key_fingerprint(secret: str, vault_secret: bytes) -> str:
    """HMAC-SHA256 of the provider key under a vault-held secret.

    Not a bare sha256 of the key.
    """
    return hmac.new(vault_secret, secret.encode("utf-8"), hashlib.sha256).hexdigest()


def supplied_key_env_names(environ: Mapping[str, str]) -> list[str]:
    """Env var names that hold a key. Values are not returned."""
    blocked = set(PROVIDER_TO_ENV.values())
    blocked.update(_EXTRA_KEY_ENV)
    return [name for name in sorted(blocked) if (environ.get(name) or "").strip()]


def env_key_error(names: list[str]) -> str:
    shown = ", ".join(names)
    return (
        f"refusing a key from the environment ({shown}); "
        "paste it at the hidden prompt or pipe it on stdin"
    )


def probe_catalog_chat(
    spec: ProviderSpec,
    secret: str,
    *,
    client: httpx.Client | None = None,
    timeout_s: float = 20.0,
) -> ProbeResult:
    """1-token chat on the provider's first catalog model.

    GET /models is not a test. OpenRouter and NVIDIA answer that without a key.
    The request and response bodies are not logged.
    """
    if not spec.chat_models:
        return ProbeResult(False, None, "no catalog chat model to test")
    base = spec.base_url.strip().rstrip("/")
    if not base:
        return ProbeResult(False, None, "no catalog base url to test")
    url = f"{base}/chat/completions"
    payload = {
        "model": spec.chat_models[0],
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 1,
    }
    headers = {"Authorization": f"Bearer {secret}"}
    owns = client is None
    http = client or httpx.Client(
        timeout=timeout_s,
        trust_env=False,
        follow_redirects=False,
    )
    try:
        try:
            resp = http.post(url, headers=headers, json=payload)
        except httpx.TimeoutException:
            return ProbeResult(False, None, "test call failed (timeout)")
        except httpx.HTTPError:
            return ProbeResult(False, None, "test call failed (unreachable)")
        status = resp.status_code
        # Status only. Do not read resp.text, resp.content, or resp.json().
        resp.close()
    finally:
        if owns:
            http.close()
    if 200 <= status < 300:
        return ProbeResult(True, status, "")
    return ProbeResult(False, status, f"test call failed (HTTP {status})")


def _role_for(spec: ProviderSpec) -> KeyRole:
    raw = spec.default_role
    if raw in _KEY_ROLES:
        return cast(KeyRole, raw)
    return "backup"


def _unknown_provider_error(provider_id: str) -> str:
    if _SAFE_PROVIDER_ID.fullmatch(provider_id):
        return f"unknown catalog id: {provider_id}"
    return "unknown catalog id"


def _log_add(
    provider_id: str,
    *,
    model: str,
    http_status: int | None,
    result: AddResult,
) -> None:
    log.info(
        "openvault_key_add",
        provider=provider_id if get_provider(provider_id) is not None else "unknown",
        model=model,
        http_status=http_status,
        stored=result.ok,
        duplicate=result.duplicate,
    )


def add_tested_key(
    vault: KeyVault,
    provider_id: str,
    secret: str,
    *,
    client: httpx.Client | None = None,
    timeout_s: float = 20.0,
) -> AddResult:
    """Test, dedupe, then store in the existing vault. Store nothing on failure."""
    provider_id = (provider_id or "").strip()
    secret = (secret or "").strip()
    spec = get_provider(provider_id)
    model = ""
    http_status: int | None = None
    if spec is None:
        result = AddResult(False, "", "", error=_unknown_provider_error(provider_id))
    elif not secret:
        result = AddResult(False, "", "", error="no key entered")
    elif not spec.chat_models:
        result = AddResult(False, spec.name, "", error="no catalog chat model to test")
    elif not spec.base_url.strip():
        result = AddResult(False, spec.name, "", error="no catalog base url to test")
    else:
        result, model, http_status = _probe_and_store(
            vault,
            spec,
            secret,
            client=client,
            timeout_s=timeout_s,
        )
    _log_add(provider_id, model=model, http_status=http_status, result=result)
    return result


def _probe_and_store(
    vault: KeyVault,
    spec: ProviderSpec,
    secret: str,
    *,
    client: httpx.Client | None,
    timeout_s: float,
) -> tuple[AddResult, str, int | None]:
    model = spec.chat_models[0]
    try:
        material = vault.get_or_create_fingerprint_secret()
    except VaultSealedError:
        return AddResult(False, spec.name, "", error="vault is sealed"), model, None
    existing = vault.find_by_fingerprint(key_fingerprint(secret, material))
    if existing is not None:
        return (
            AddResult(
                False,
                existing.label,
                mask_key_id(existing.id),
                key_id=existing.id,
                duplicate=True,
                error="duplicate key refused",
            ),
            model,
            None,
        )
    probe = probe_catalog_chat(spec, secret, client=client, timeout_s=timeout_s)
    if not probe.ok:
        return (
            AddResult(False, spec.name, "", error=probe.error),
            model,
            probe.http_status,
        )
    try:
        record = vault.create(
            label=spec.name[:80],
            provider=cast(ProviderKind, spec.id),
            secret=secret,
            role=_role_for(spec),
            base_url=spec.base_url,
            key_fp=key_fingerprint(secret, material),
        )
    except VaultSealedError:
        return AddResult(False, spec.name, "", error="vault is sealed"), model, probe.http_status
    vault.set_precheck(record.id, status="ok", latency_ms=None, error=None)
    return (
        AddResult(True, record.label, mask_key_id(record.id), key_id=record.id),
        model,
        probe.http_status,
    )


def _is_tty(stream: TextIO) -> bool:
    isatty = getattr(stream, "isatty", None)
    if not callable(isatty):
        return False
    try:
        return bool(isatty())
    except (OSError, ValueError):
        return False


def read_key(*, stdin: TextIO, stderr: TextIO) -> str:
    """Hidden prompt on a TTY, otherwise one stdin payload. Nothing is echoed."""
    if _is_tty(stdin):
        return getpass.getpass("API key (input hidden): ", stream=stderr).strip()
    return stdin.read().strip()


def split_add_argv(argv: list[str]) -> tuple[str | None, list[str], bool]:
    """Return (provider, extras, help). A leading ``add`` is optional."""
    args = list(argv)
    if args and args[0] == "add":
        args = args[1:]
    if any(item in ("-h", "--help") for item in args):
        return None, [], True
    if not args:
        return None, [], False
    provider = args[0]
    extras = args[1:]
    if provider.startswith("-"):
        return None, [provider, *extras], False
    return provider, extras, False


def _emit(result: AddResult, stdout: TextIO, stderr: TextIO) -> int:
    if result.ok:
        print(f"{result.label} {result.masked_id}", file=stdout)
        return 0
    if result.duplicate:
        print(f"{result.label} {result.masked_id}", file=stdout)
        print("duplicate key refused", file=stderr)
        return 1
    print(result.error or "key add failed", file=stderr)
    return 1


def cli_main(
    argv: list[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    vault: KeyVault | None = None,
    client: httpx.Client | None = None,
) -> int:
    """``openvault add <provider>``. Prints the label and masked id, or an error."""
    args = list(sys.argv[1:] if argv is None else argv)
    env = os.environ if environ is None else environ
    in_stream = sys.stdin if stdin is None else stdin
    out_stream = sys.stdout if stdout is None else stdout
    err_stream = sys.stderr if stderr is None else stderr

    provider, extras, help_requested = split_add_argv(args)
    if help_requested:
        print(_USAGE, file=out_stream, end="")
        return 0
    if provider is None and not extras:
        print("usage: openvault add <provider>", file=err_stream)
        return 2
    if extras or provider is None or _SAFE_PROVIDER_ID.fullmatch(provider) is None:
        print(ARGV_KEY_ERROR, file=err_stream)
        log.info("openvault_key_add_refused", reason="argv")
        return 2
    names = supplied_key_env_names(env)
    if names:
        print(env_key_error(names), file=err_stream)
        log.info("openvault_key_add_refused", reason="env")
        return 2

    secret = read_key(stdin=in_stream, stderr=err_stream)
    store = vault if vault is not None else KeyVault()
    result = add_tested_key(store, provider, secret, client=client)
    return _emit(result, out_stream, err_stream)


def main() -> int:
    return cli_main()


if __name__ == "__main__":
    raise SystemExit(main())
