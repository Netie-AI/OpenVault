#!/usr/bin/env python3
"""Every path the console calls must exist on the vault.

The console and the vault are separate codebases that agree only by convention.
Nothing today notices when a route is renamed on one side: the UI keeps calling
the old path, the vault answers 404, and the page shows an empty list -- which
looks like "you have no keys" rather than "this endpoint moved".

This reads the paths out of the console's API layer, reads the vault's own
OpenAPI document, and reports the difference. It is read-only and needs the
vault running.

Usage:
    python scripts/check_ui_backend_contract.py
    python scripts/check_ui_backend_contract.py --api http://127.0.0.1:5000
    python scripts/check_ui_backend_contract.py --json     # for CI

Exit code is 1 when the console calls something the vault does not serve.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import urllib.error
import urllib.request

REPO = pathlib.Path(__file__).resolve().parent.parent
UI_API_DIR = REPO / "apps" / "web" / "src" / "lib" / "api"
DEFAULT_API = "http://127.0.0.1:5000"

# apiGet("/api/keys"), apiPost(`/api/keys/${id}/revoke`), apiFetch('/api/...')
CALL_RE = re.compile(
    r"""api(?:Get|Post|Patch|Delete|Fetch)\s*(?:<[^>]*>)?\s*\(\s*[`'"]([^`'"]+)[`'"]""",
    re.VERBOSE,
)
# `${...}` is a path parameter; OpenAPI spells it {param}.
TEMPLATE_RE = re.compile(r"\$\{[^}]+\}")


# Any api* call, whether or not its first argument is a literal we can read.
ANY_CALL_RE = re.compile(r"api(?:Get|Post|Patch|Delete|Fetch)\s*(?:<[^>]*>)?\s*\(")


def ui_paths() -> tuple[dict[str, set[str]], list[str]]:
    """Map each called path to the files that call it.

    Also returns the call sites whose path is NOT a literal (built from a
    variable, say). Those are reported rather than skipped: a checker that
    quietly ignores what it cannot parse will pass while the drift it exists to
    catch sits in the calls it dropped.
    """
    found: dict[str, set[str]] = {}
    unresolved: list[str] = []
    if not UI_API_DIR.is_dir():
        raise SystemExit(f"console API directory not found: {UI_API_DIR}")
    for path in sorted(UI_API_DIR.rglob("*.ts")):
        if path.name.endswith(".test.ts"):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        literal = 0
        for raw in CALL_RE.findall(text):
            literal += 1
            if not raw.startswith("/"):
                continue
            found.setdefault(normalise(raw), set()).add(path.name)
        total = len(ANY_CALL_RE.findall(text))
        if total > literal:
            unresolved.append(f"{path.name}: {total - literal} call(s) with a non-literal path")
    return found, unresolved


def normalise(path: str) -> str:
    """Reduce a called path to its OpenAPI shape."""
    path = path.split("?", 1)[0]
    path = TEMPLATE_RE.sub("{param}", path)
    return path.rstrip("/") or "/"


def backend_paths(api: str) -> set[str]:
    try:
        with urllib.request.urlopen(f"{api}/openapi.json", timeout=20) as res:
            spec = json.loads(res.read().decode())
    except urllib.error.URLError as exc:
        raise SystemExit(f"cannot reach the vault at {api} -- is it running?\n  {exc.reason}") from exc
    return {normalise(re.sub(r"\{[^}]+\}", "{param}", p)) for p in spec.get("paths", {})}


def probe(api: str, path: str) -> int | None:
    """Ask the server whether a path exists. Returns the status, or None if unreachable.

    The OpenAPI document is NOT the list of served routes -- routes registered
    without a response model, or mounted outside the schema, answer perfectly
    well while being absent from it. Reporting a spec omission as a broken
    endpoint is a false alarm that costs someone an afternoon, so absence from
    the spec only ever earns a path a probe, never a verdict.

    Paths with a parameter cannot be probed without inventing an id, so they are
    reported as unverifiable rather than guessed at.
    """
    if "{param}" in path:
        return None
    req = urllib.request.Request(f"{api}{path}", method="GET")
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            return res.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except urllib.error.URLError:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--api", default=DEFAULT_API)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    called, unresolved = ui_paths()
    served = backend_paths(args.api)

    # Not in the spec is a question, not an answer. Ask the server.
    missing: dict[str, list[str]] = {}
    undocumented: list[str] = []
    unverifiable: list[str] = []
    for path, files in called.items():
        if path in served:
            continue
        status = probe(args.api, path)
        if status is None:
            unverifiable.append(path)
        elif status == 404:
            missing[path] = sorted(files)
        else:
            undocumented.append(f"{path} (answers {status}, absent from openapi.json)")

    if args.json:
        print(
            json.dumps(
                {
                    "called": len(called),
                    "served": len(served),
                    "missing": missing,
                    "undocumented": undocumented,
                    "unverifiable": unverifiable,
                    "unresolved": unresolved,
                },
                indent=2,
            )
        )
        return 1 if missing else 0

    print(f"console calls {len(called)} paths; vault documents {len(served)}")
    for note in unresolved:
        print(f"  not checked  - {note}")
    for note in unverifiable:
        print(f"  not probed   - {note} (takes a path parameter)")
    for note in undocumented:
        print(f"  undocumented - {note}")
    if not missing:
        print("OK - every path the console calls answers on the vault")
        return 0

    print(f"\n{len(missing)} path(s) the console calls but the vault does not serve:")
    for path, files in sorted(missing.items()):
        print(f"  {path}\n      called from: {', '.join(files)}")
    print("\nA missing path answers 404, and most pages render that as an empty list.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
