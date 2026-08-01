#!/usr/bin/env python3
"""Add one provider key to the local vault, without it touching your shell history.

The console is the nicer way in. This exists for when it is not available -- a
dev server that has not picked up its rewrites, a headless box, or a key you
would rather not paste into a browser at all.

Why a prompt and not an argument: a secret passed as ``--secret sk-...`` is
recorded in your shell history, is visible to every other process in ``ps``
output for the lifetime of the command, and tends to end up pasted into a chat
window when something goes wrong. ``getpass`` reads it without echoing and it
never leaves this process except over loopback to the vault.

The vault does the encrypting; this script only carries the value there. It
talks to 127.0.0.1 only -- the same loopback rule every custody mutation is
subject to.

Usage:
    python scripts/add_key.py                       # prompts for everything
    python scripts/add_key.py --provider google     # skip the provider question
    python scripts/add_key.py --rotate <key-id>     # replace an existing secret
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
import urllib.error
import urllib.request

DEFAULT_API = "http://127.0.0.1:5000"
TIMEOUT_S = 20

# Enough to name the provider from a pasted key. Mirrors the console's
# inferProvider rules; keep the two in step when either gains a vendor.
PREFIXES: list[tuple[str, str]] = [
    ("sk-ant-", "anthropic"),
    ("sk-or-v1-", "openrouter"),
    ("gsk_", "groq"),
    ("hf_", "huggingface"),
    ("AIza", "google"),
    ("AQ.", "google"),  # Google AI Studio's newer shape
    ("xai-", "xai"),
    ("pplx-", "perplexity"),
    ("nvapi-", "nvidia"),
    ("csk-", "cerebras"),
    ("sk-", "openai"),  # generic, must stay last
]


def guess_provider(secret: str) -> str | None:
    for prefix, provider in PREFIXES:
        if secret.startswith(prefix):
            return provider
    return None


def call(api: str, path: str, method: str = "GET", payload: dict | None = None) -> dict:
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        f"{api}{path}",
        data=body,
        method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as res:
            raw = res.read().decode() or "{}"
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:400]
        raise SystemExit(f"vault refused ({exc.code}): {detail}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(
            f"cannot reach the vault at {api} -- is it running?\n  {exc.reason}"
        ) from exc


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--api", default=DEFAULT_API, help=f"vault base URL (default {DEFAULT_API})")
    ap.add_argument("--provider", help="provider id, e.g. google. Inferred from the key if omitted.")
    ap.add_argument("--label", help="human name for this key")
    ap.add_argument("--role", default="free", help="key role (default: free)")
    ap.add_argument("--rotate", metavar="KEY_ID", help="replace the secret on an existing key")
    ap.add_argument("--list", action="store_true", help="list existing keys and exit")
    args = ap.parse_args()

    if args.list:
        rows = call(args.api, "/api/keys")
        rows = rows if isinstance(rows, list) else rows.get("keys", [])
        if not rows:
            print("no keys stored")
            return 0
        for row in rows:
            print(f"  {row.get('id','')[:8]}  {row.get('provider','?'):<12} {row.get('label','')}")
        return 0

    # Never echoed, never in argv, never in history.
    secret = getpass.getpass("Paste the API key (input hidden): ").strip()
    if not secret:
        print("nothing entered -- aborted", file=sys.stderr)
        return 1

    if args.rotate:
        call(args.api, f"/api/keys/{args.rotate}/rotate", "POST", {"new_secret": secret})
        print(f"rotated {args.rotate}. The old secret is gone.")
        return 0

    provider = args.provider or guess_provider(secret)
    if not provider:
        provider = input("Provider id (e.g. google, anthropic, openai): ").strip()
    if not provider:
        print("no provider -- aborted", file=sys.stderr)
        return 1

    label = args.label or f"{provider} ({secret[:4]}...{secret[-4:]})"
    created = call(
        args.api,
        "/api/keys",
        "POST",
        {"label": label, "provider": provider, "secret": secret, "role": args.role},
    )

    # `masked_secret` is what the vault echoes back; the plaintext is not
    # returned by design, so there is nothing here worth redacting further.
    print(f"stored {created.get('id','?')[:8]}  {provider}  {created.get('masked_secret','')}")
    print("Encrypted at rest by the vault. Run a precheck in the console to confirm it works.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
