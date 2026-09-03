"""Official password-manager CSV dump-import into sealed secrets.

Google Password Manager, Apple Passwords, and Chrome exports only. Dry-run
defaults True, matching ``env_ingest``. CVV / security-code columns are never
stored — they are stripped or the row is refused with an explicit reason, the
same honesty class as ``create_card`` refusing ``cvv``.

This module does not scrape vendors and does not take vendor credentials.
Synthetic fixtures in tests; real dumps stay on the operator's machine.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from openmw.openvault.paths import openvault_home
from openmw.openvault.vault.crypto import Seal, VaultSealedError
from openmw.openvault.vault.secrets import SecretStore, SecretValidationError

Dialect = Literal["google", "apple", "chrome", "unknown"]

_CVV_HEADERS = frozenset(
    {
        "cvv",
        "cvc",
        "cid",
        "security code",
        "security_code",
        "securitycode",
        "csc",
        "cvv2",
        "cvc2",
    }
)
_PASSWORD_HEADERS = frozenset({"password", "passwd", "pass"})
_PAN_HEADERS = frozenset({"pan", "card number", "card_number", "cardnumber", "number", "cc number"})
_LABEL_HEADERS = frozenset({"title", "name", "label", "site", "card name"})
_URL_HEADERS = frozenset({"url", "origin_url", "website", "login_uri"})
_USER_HEADERS = frozenset({"username", "user", "login", "email"})
_HOLDER_HEADERS = frozenset({"cardholder", "cardholder name", "name on card", "cardholder_name"})
_EXP_HEADERS = frozenset({"expiration date", "expiry", "exp", "expires", "expiration"})
_SKIP_EMPTY = "empty password skipped"


def import_dir(home: Path | None = None) -> Path:
    """Operator staging for PM CSVs. Under OPENVAULT_HOME so git ignores it."""
    base = home if home is not None else openvault_home()
    path = base / "import"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _norm_header(raw: str) -> str:
    return re.sub(r"\s+", " ", (raw or "").replace("\ufeff", "").strip().lower())


def detect_dialect(headers: list[str]) -> Dialect:
    names = {_norm_header(h) for h in headers}
    if "title" in names and "url" in names:
        return "apple"
    if "name" in names and "url" in names and "note" in names:
        return "google"
    if "name" in names and "url" in names:
        return "chrome"
    return "unknown"


def _pick(row: Mapping[str, str], candidates: frozenset[str]) -> str:
    for key, value in row.items():
        if _norm_header(key) in candidates:
            return (value or "").strip()
    return ""


def _parse_expiry(raw: str) -> tuple[int | None, int | None]:
    text = (raw or "").strip()
    if not text:
        return None, None
    match = re.match(r"^(\d{1,2})\s*[/\-]\s*(\d{2}|\d{4})$", text)
    if match:
        month = int(match.group(1))
        year = int(match.group(2))
        if year < 100:
            year += 2000
        return month, year
    match = re.match(r"^(\d{4})\s*[\-]\s*(\d{1,2})$", text)
    if match:
        return int(match.group(2)), int(match.group(1))
    return None, None


@dataclass
class ImportRowResult:
    action: str
    kind: str | None
    label: str
    reason: str = ""
    ok: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "kind": self.kind,
            "label": self.label,
            "reason": self.reason,
            "ok": self.ok,
        }


def _row_has_cvv(row: Mapping[str, str]) -> str:
    return _pick(row, _CVV_HEADERS)


def parse_csv_text(text: str) -> tuple[Dialect, list[dict[str, str]]]:
    sample = text.lstrip("\ufeff")
    reader = csv.DictReader(io.StringIO(sample))
    if reader.fieldnames is None:
        return "unknown", []
    dialect = detect_dialect(list(reader.fieldnames))
    rows = [{k: (v or "") for k, v in row.items() if k is not None} for row in reader]
    return dialect, rows


def ingest_pm_csv(
    store: SecretStore,
    text: str,
    *,
    dry_run: bool = True,
    seal: Seal | None = None,
    source: str = "csv",
) -> dict[str, Any]:
    """Import one CSV. Never echoes passwords, PANs, or CVVs in the report."""
    if not dry_run and seal is not None and seal.is_sealed:
        raise VaultSealedError("vault is sealed; unseal before password-manager ingest")

    dialect, rows = parse_csv_text(text)
    results: list[ImportRowResult] = []
    imported = 0
    skipped = 0
    cvv_stripped = 0

    for row in rows:
        label = _pick(row, _LABEL_HEADERS) or _pick(row, _URL_HEADERS) or "(untitled)"
        cvv = _row_has_cvv(row)
        password = _pick(row, _PASSWORD_HEADERS)
        pan = _pick(row, _PAN_HEADERS)
        username = _pick(row, _USER_HEADERS)
        url = _pick(row, _URL_HEADERS)
        holder = _pick(row, _HOLDER_HEADERS)
        exp_month, exp_year = _parse_expiry(_pick(row, _EXP_HEADERS))

        if cvv:
            cvv_stripped += 1

        if pan:
            if cvv and not dry_run:
                # Same refusal class as create_card: never store CVV. Strip it
                # and continue with the PAN-only row rather than aborting the
                # rest of a 300-row dump because one export included CVC.
                pass
            if dry_run:
                results.append(
                    ImportRowResult(
                        action="would_import",
                        kind="payment_card",
                        label=label,
                        reason="CVV/security-code stripped: never stored" if cvv else "",
                    )
                )
                continue
            try:
                if exp_month is None or exp_year is None:
                    raise SecretValidationError("card row needs an expiration date")
                store.create_card(
                    label=label,
                    pan=pan,
                    exp_month=exp_month,
                    exp_year=exp_year,
                    cardholder=holder,
                )
            except SecretValidationError as exc:
                skipped += 1
                results.append(
                    ImportRowResult(
                        action="refused",
                        kind="payment_card",
                        label=label,
                        reason=str(exc),
                        ok=False,
                    )
                )
                continue
            imported += 1
            results.append(
                ImportRowResult(
                    action="imported",
                    kind="payment_card",
                    label=label,
                    reason="CVV/security-code stripped: never stored" if cvv else "",
                )
            )
            continue

        if not password:
            skipped += 1
            reason = "CVV/security-code stripped: never stored" if cvv else _SKIP_EMPTY
            results.append(
                ImportRowResult(
                    action="skipped",
                    kind="password",
                    label=label,
                    reason=reason,
                    ok=False,
                )
            )
            continue

        if dry_run:
            results.append(ImportRowResult(action="would_import", kind="password", label=label))
            continue

        try:
            store.create_password(
                label=label,
                password=password,
                username=username,
                url=url,
            )
        except SecretValidationError as exc:
            skipped += 1
            results.append(
                ImportRowResult(
                    action="refused",
                    kind="password",
                    label=label,
                    reason=str(exc),
                    ok=False,
                )
            )
            continue
        imported += 1
        results.append(ImportRowResult(action="imported", kind="password", label=label))

    report: dict[str, Any] = {
        "ok": True,
        "dry_run": dry_run,
        "source": source,
        "dialect": dialect,
        "scanned": len(rows),
        "imported": imported,
        "skipped": skipped,
        "cvv_stripped": cvv_stripped,
        "results": [r.to_dict() for r in results],
    }
    return report


def ingest_import_dir(
    store: SecretStore,
    *,
    dry_run: bool = True,
    seal: Seal | None = None,
    home: Path | None = None,
) -> dict[str, Any]:
    """Scan ``OPENVAULT_HOME/import/*.csv``. Does not recurse."""
    folder = import_dir(home)
    files = sorted(p for p in folder.glob("*.csv") if p.is_file())
    combined: dict[str, Any] = {
        "ok": True,
        "dry_run": dry_run,
        "source": str(folder),
        "files": [],
        "imported": 0,
        "skipped": 0,
        "cvv_stripped": 0,
        "scanned": 0,
        "results": [],
    }
    for path in files:
        text = path.read_text(encoding="utf-8")
        one = ingest_pm_csv(store, text, dry_run=dry_run, seal=seal, source=path.name)
        combined["files"].append({"name": path.name, "dialect": one["dialect"]})
        combined["imported"] += int(one["imported"])
        combined["skipped"] += int(one["skipped"])
        combined["cvv_stripped"] += int(one["cvv_stripped"])
        combined["scanned"] += int(one["scanned"])
        combined["results"].extend(one["results"])
    return combined
