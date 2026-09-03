"""Import official password-manager CSV dumps into sealed secrets.

Google Password Manager, Chrome, and Apple Passwords exports are the same
class of source as ``OPENVAULT_HOME/import/*.keys.env``: a dump the operator
already exported, not a scrape. Dry-run defaults on. Raw passwords and PANs
never appear in the result dicts.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from openmw.openvault.paths import import_dir, master_key_path
from openmw.openvault.vault.crypto import plaintext_backup_present
from openmw.openvault.vault.secrets import (
    SecretStore,
    SecretValidationError,
    mask_password,
)

_CVV_HEADERS = frozenset(
    {"cvv", "cvc", "csc", "security code", "security_code", "card cvv", "card cvc"}
)
_PASSWORD_HEADERS = frozenset({"password", "passwd", "login password"})
_PAN_HEADERS = frozenset({"number", "card number", "card_number", "pan", "cc number"})
_LABEL_HEADERS = frozenset({"name", "title", "label", "card name", "account"})
_URL_HEADERS = frozenset({"url", "origin", "origin_url", "website", "link", "login uri"})
_USER_HEADERS = frozenset({"username", "user name", "user", "login", "email"})
_HOLDER_HEADERS = frozenset({"cardholder", "cardholder name", "name on card", "holder"})
_EXP_MONTH_HEADERS = frozenset({"exp_month", "expiry month", "expiration month", "exp month"})
_EXP_YEAR_HEADERS = frozenset({"exp_year", "expiry year", "expiration year", "exp year"})


class PmImportError(ValueError):
    """Raised when the dump cannot be read or must not be written."""


def _norm_header(raw: str) -> str:
    return " ".join(raw.strip().lower().replace("-", " ").replace("_", " ").split())


def _pick(row: dict[str, str], aliases: frozenset[str]) -> str:
    for key, value in row.items():
        if _norm_header(key) in aliases:
            return (value or "").strip()
    return ""


def _header_hit(headers: Iterable[str], aliases: frozenset[str]) -> bool:
    return any(_norm_header(h) in aliases for h in headers)


def _open_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(text.splitlines(), dialect=dialect)
    if reader.fieldnames is None:
        raise PmImportError(f"{path.name} has no header row")
    headers = [h or "" for h in reader.fieldnames]
    rows = [{k or "": (v or "") for k, v in row.items()} for row in reader]
    return headers, rows


def _password_dup_key(url: str, username: str, label: str) -> tuple[str, str, str]:
    return (url.strip().lower(), username.strip().lower(), label.strip().lower())


def ingest_pm_csv(
    store: SecretStore,
    path: Path,
    *,
    dry_run: bool = True,
    allow_plaintext_backup: bool = False,
) -> dict[str, Any]:
    """Import one official PM CSV. Never returns password or PAN plaintext."""
    path = path.expanduser()
    if path.suffix.lower() != ".csv":
        raise PmImportError("only .csv dumps are accepted")
    if not path.is_file():
        raise PmImportError(f"file not found: {path}")
    if not dry_run and not allow_plaintext_backup and plaintext_backup_present(master_key_path()):
        raise PmImportError(
            "plaintext master-key backup is present; retire it before importing secrets"
        )

    headers, rows = _open_rows(path)
    has_password = _header_hit(headers, _PASSWORD_HEADERS)
    has_pan = _header_hit(headers, _PAN_HEADERS)
    if not has_password and not has_pan:
        raise PmImportError(
            f"{path.name} is not a Google/Apple/Chrome password or card export "
            "(no password or card-number column)"
        )

    existing = {
        _password_dup_key(s.url, s.username, s.label)
        for s in store.list_secrets(kind="password")
        if s.lifecycle == "active"
    }
    results: list[dict[str, Any]] = []
    imported = 0
    cvv_stripped = 0

    for index, row in enumerate(rows, start=2):
        cvv = _pick(row, _CVV_HEADERS)
        label = _pick(row, _LABEL_HEADERS)
        url = _pick(row, _URL_HEADERS)
        username = _pick(row, _USER_HEADERS)
        password = _pick(row, _PASSWORD_HEADERS)
        pan = _pick(row, _PAN_HEADERS)
        report: dict[str, Any] = {"line": index, "label": label or url or "(untitled)"}

        if cvv:
            cvv_stripped += 1
            report["cvv"] = "stripped_not_stored"

        if has_pan and pan:
            month_raw = _pick(row, _EXP_MONTH_HEADERS)
            year_raw = _pick(row, _EXP_YEAR_HEADERS)
            try:
                exp_month = int(month_raw)
                exp_year = int(year_raw)
            except ValueError:
                report.update(kind="payment_card", ok=False, action="skipped", reason="bad_expiry")
                results.append(report)
                continue
            if dry_run:
                report.update(kind="payment_card", ok=True, action="would_import", last4=pan[-4:])
                results.append(report)
                continue
            try:
                record = store.create_card(
                    label=label or f"card-{pan[-4:]}",
                    pan=pan,
                    exp_month=exp_month,
                    exp_year=exp_year,
                    cardholder=_pick(row, _HOLDER_HEADERS),
                )
            except SecretValidationError as exc:
                report.update(
                    kind="payment_card", ok=False, action="skipped", reason=str(exc)
                )
                results.append(report)
                continue
            imported += 1
            report.update(
                kind="payment_card",
                ok=True,
                action="created",
                id=record.id,
                last4=record.last4,
                brand=record.brand,
            )
            results.append(report)
            continue

        if not has_password:
            report.update(kind="password", ok=False, action="skipped", reason="no_password_column")
            results.append(report)
            continue
        if not password:
            report.update(kind="password", ok=False, action="skipped", reason="empty_password")
            results.append(report)
            continue
        key = _password_dup_key(url, username, label)
        if key in existing:
            report.update(
                kind="password",
                ok=True,
                action="skipped_duplicate",
                username=username,
                url=url,
            )
            results.append(report)
            continue
        if dry_run:
            report.update(
                kind="password",
                ok=True,
                action="would_import",
                username=username,
                url=url,
                masked=mask_password(password),
            )
            results.append(report)
            continue
        record = store.create_password(
            label=label or url or "imported password",
            password=password,
            username=username,
            url=url,
        )
        imported += 1
        existing.add(key)
        report.update(
            kind="password",
            ok=True,
            action="created",
            id=record.id,
            username=username,
            url=url,
            masked=record.masked,
        )
        results.append(report)

    return {
        "ok": True,
        "dry_run": dry_run,
        "source": path.name,
        "scanned": len(rows),
        "imported": imported,
        "cvv_stripped": cvv_stripped,
        "results": results,
        "policy": (
            "Official export CSV only. CVV is never stored. Passwords are sealed "
            "and never echoed. Delete the CSV after a successful import."
        ),
    }


def ingest_pm_sources(
    store: SecretStore,
    *,
    path: Path | None = None,
    dry_run: bool = True,
    allow_plaintext_backup: bool = False,
) -> dict[str, Any]:
    """Ingest one explicit CSV, or every ``*.csv`` under the vault import dir."""
    if path is not None:
        files = [path]
    else:
        files = sorted(import_dir().glob("*.csv"))
        if not files:
            return {
                "ok": True,
                "dry_run": dry_run,
                "scanned": 0,
                "imported": 0,
                "files": [],
                "policy": "no CSV in OPENVAULT_HOME/import",
            }
    files_out: list[dict[str, Any]] = []
    imported = 0
    scanned = 0
    cvv_stripped = 0
    for file in files:
        one = ingest_pm_csv(
            store,
            file,
            dry_run=dry_run,
            allow_plaintext_backup=allow_plaintext_backup,
        )
        scanned += int(one["scanned"])
        imported += int(one["imported"])
        cvv_stripped += int(one["cvv_stripped"])
        files_out.append(one)
    return {
        "ok": True,
        "dry_run": dry_run,
        "scanned": scanned,
        "imported": imported,
        "cvv_stripped": cvv_stripped,
        "files": files_out,
        "policy": (
            "Official export CSV only. CVV is never stored. Passwords are sealed "
            "and never echoed. Delete the CSV after a successful import."
        ),
    }
