"""Encrypted store for non-API-key secrets: passwords and payment cards.

Why a second table rather than a ``kind`` column on ``keys``: the ``keys`` table
is load-bearing for routing (provider, role, priority, precheck, fallback
breakers). A card has none of those and would carry a dozen NULL columns through
every routing query, and every consumer of ``/api/keys`` — AirGPT, FreeIDE,
Netie — would start receiving rows it must learn to skip. Same database file and
the *same* ``Seal``, so there is still exactly one master key and one thing to
back up; only the schema is separate. PRODUCT_ROLES' "no second key vault" is
about custody, not about table count.

PCI posture, stated plainly because it constrains the whole module:

* The PAN is the only card field that is sealed. ``brand``/``last4``/expiry are
  stored in the clear on purpose — they are what a chooser UI needs, and they
  are not usable to charge a card.
* CVV/CVC is **never** accepted or stored, in any form, encrypted or not. PCI
  DSS 3.2 forbids retaining it after authorization, and OpenVault does not
  authorize anything, so there is no window in which holding it is legitimate.
  ``create_card`` raises rather than silently dropping it — a caller that
  believes it stored a CVV would build a checkout flow on a field that is not
  there.
* No public record type carries plaintext. ``SecretRecord`` holds a mask only,
  so ``asdict()`` into a JSON response, a log line, or a traceback cannot leak a
  PAN or a password by accident. Plaintext leaves this module through exactly
  one function, ``reveal``.
"""

from __future__ import annotations

import contextlib
import json
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from openmw.openvault.paths import keys_db_path
from openmw.openvault.vault.crypto import Seal

SecretKind = Literal["password", "payment_card", "recovery_codes", "identity"]
SecretLifecycle = Literal["active", "revoked", "rotated", "compromised"]

# Government and institutional identifiers. Deliberately not "PII" as a blanket:
# a name and a home address are ordinary profile fields and belong in the
# profile store, where a form-fill can read them. These are the subset where
# disclosure is not recoverable - you cannot rotate an IC number after it leaks
# - and that irreversibility, not sensitivity in general, is what makes them
# vault material.
IdentityDocType = Literal[
    "nric",
    "passport",
    "driving_licence",
    "tax_id",
    "national_id",
    "other",
]

CardBrand = Literal[
    "visa",
    "mastercard",
    "amex",
    "discover",
    "jcb",
    "diners",
    "unionpay",
    "unknown",
]

# Ordered longest-prefix-first; the first match wins, so mastercard's 2221-2720
# range cannot be shadowed by a shorter pattern.
_BRAND_PATTERNS: tuple[tuple[str, CardBrand], ...] = (
    (r"^4", "visa"),
    (r"^(5[1-5]|22[2-9]\d|2[3-6]\d\d|27[01]\d|2720)", "mastercard"),
    (r"^3[47]", "amex"),
    (r"^(6011|65|64[4-9])", "discover"),
    (r"^35(2[89]|[3-8]\d)", "jcb"),
    (r"^(30[0-5]|3095|36|3[89])", "diners"),
    (r"^62", "unionpay"),
)


class SecretValidationError(ValueError):
    """Raised when a caller's secret payload is malformed or PCI-forbidden."""


def luhn_valid(pan: str) -> bool:
    """Standard mod-10 check. Catches typos, not fraud."""
    digits = [int(c) for c in pan if c.isdigit()]
    if len(digits) < 12:
        return False
    total = 0
    for index, digit in enumerate(reversed(digits)):
        if index % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def detect_brand(pan: str) -> CardBrand:
    for pattern, brand in _BRAND_PATTERNS:
        if re.match(pattern, pan):
            return brand
    return "unknown"


def normalize_pan(raw: str) -> str:
    """Strip the spaces and dashes humans type; reject anything else."""
    pan = re.sub(r"[\s-]", "", raw or "")
    if not pan.isdigit():
        raise SecretValidationError("card number must contain only digits, spaces, or dashes")
    if not 12 <= len(pan) <= 19:
        raise SecretValidationError("card number must be 12-19 digits")
    if not luhn_valid(pan):
        raise SecretValidationError("card number failed the Luhn check")
    return pan


def mask_pan(last4: str, brand: CardBrand = "unknown") -> str:
    """A display mask, never derived from the sealed PAN at read time."""
    groups = "•••• •••• ••••" if brand != "amex" else "•••• ••••••"
    return f"{groups} {last4}" if last4 else groups


def mask_password(password: str) -> str:
    """Reveal nothing but a rough length.

    Deliberately unlike ``crypto.mask_secret``, which shows the first four
    characters. For an API key ``sk-a…`` is a useful non-secret discriminator
    between two rows; for a password the first four characters are a meaningful
    head start on guessing the rest, and the user already knows which password
    it is from the label and username.
    """
    if not password:
        return ""
    return "•" * min(len(password), 12)


def normalize_recovery_codes(raw: str | list[str]) -> list[str]:
    """Accept how a human actually pastes backup codes, reject an empty set.

    Providers hand these over as a block from a screen or a downloaded .txt, so
    the input is a blob of newline- or space-separated tokens as often as it is
    a list. Codes are kept EXACTLY as issued apart from surrounding whitespace:
    they have to be typed back character-for-character, and helpfully stripping
    a dash out of ``abcd-efgh`` produces a code that no longer works.

    Order is preserved and duplicates are dropped. A duplicate is a paste
    accident, and silently keeping it would overstate how many logins you have
    left - which is the one number this record exists to tell you.
    """
    # ONE PER LINE, and nothing smarter. Splitting on whitespace looks more
    # forgiving and is actively dangerous: providers print codes as
    # "1234 5678", so a space-split turns every code into two codes that
    # will never work AND doubles the count of logins you think you have.
    # That failure is silent until you are locked out, which is the exact
    # moment this record is for. A single-line paste instead stores one
    # obviously-wrong code, which is visible immediately and recoverable.
    tokens = raw.splitlines() if isinstance(raw, str) else [str(t) for t in (raw or [])]

    codes: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        # Providers number the list on screen ("1. abcd-efgh", "2) ..."), and
        # that prefix is not part of the code.
        code = re.sub(r"^\s*(?:\d{1,2}\s*[.)]|[-*•])\s*", "", token).strip()
        if not code:
            continue
        if len(code) > 128:
            raise SecretValidationError("a recovery code longer than 128 characters is not a code")
        if code in seen:
            continue
        seen.add(code)
        codes.append(code)

    if not codes:
        raise SecretValidationError("no recovery codes found in the input")
    if len(codes) > 64:
        raise SecretValidationError("more than 64 recovery codes - is this the right paste?")
    return codes


def mask_recovery_codes(unused: int, total: int) -> str:
    """The only thing worth showing about a code set is how much is left."""
    return f"{unused} of {total} unused"


def normalize_identity_number(raw: str) -> str:
    """Trim, but do not reformat.

    An IC is written ``900101-01-1234`` and a passport ``A12345678``; both are
    typed back into forms in the shape the document uses. Normalising the
    punctuation away here would mean every consumer has to guess it back.
    """
    number = " ".join((raw or "").split())
    if not number:
        raise SecretValidationError("identity number must not be empty")
    if not 4 <= len(number) <= 64:
        raise SecretValidationError("identity number must be 4-64 characters")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 \-/]*", number):
        raise SecretValidationError(
            "identity number may contain only letters, digits, spaces, dashes and slashes"
        )
    return number


def mask_identity(number: str) -> str:
    """Last four only, derived at write time like the card mask.

    Four is enough for the owner to tell two documents apart and not enough to
    reconstruct a checksummed national id from the mask alone.
    """
    tail = re.sub(r"[^A-Za-z0-9]", "", number or "")[-4:]
    return f"•••• {tail.upper()}" if tail else "••••"


def validate_expiry(month: int, year: int) -> tuple[int, int]:
    if not 1 <= month <= 12:
        raise SecretValidationError("expiry month must be 1-12")
    if not 2000 <= year <= 2099:
        raise SecretValidationError("expiry year must be a 4-digit year in 2000-2099")
    return month, year


@dataclass(frozen=True)
class SecretRecord:
    """Public view of a stored secret. Never contains plaintext.

    ``masked`` is the only value derived from the sealed payload, and it is
    derived at *write* time for cards (from ``last4``) so that listing does not
    require decrypting anything.
    """

    id: str
    kind: SecretKind
    label: str
    masked: str
    account_id: str | None
    lifecycle: SecretLifecycle
    replaced_by: str | None
    created_at: float
    updated_at: float
    last_revealed_at: float | None
    # password-only
    username: str = ""
    url: str = ""
    # payment_card-only
    brand: str = ""
    last4: str = ""
    exp_month: int | None = None
    exp_year: int | None = None
    cardholder: str = ""
    # recovery_codes-only. Counts are stored in the clear, like brand/last4:
    # "how many logins do I have left" is the question this row exists to
    # answer, and answering it must not require unsealing the vault.
    codes_total: int | None = None
    codes_unused: int | None = None
    # identity-only. The document TYPE is a chooser field, not a secret; the
    # number it describes is sealed.
    doc_type: str = ""


class SecretStore:
    """CRUD + gated decrypt for passwords and payment cards.

    Shares ``keys.db`` and the vault master key with :class:`KeyVault`.
    """

    def __init__(self, db_path: Path | None = None, seal: Seal | None = None) -> None:
        self._db_path = db_path if db_path is not None else keys_db_path()
        self._seal = seal if seal is not None else Seal()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS secrets (
                  id TEXT PRIMARY KEY,
                  kind TEXT NOT NULL,
                  label TEXT NOT NULL,
                  secret_blob BLOB NOT NULL,
                  masked TEXT NOT NULL DEFAULT '',
                  account_id TEXT,
                  lifecycle TEXT NOT NULL DEFAULT 'active',
                  replaced_by TEXT,
                  username TEXT NOT NULL DEFAULT '',
                  url TEXT NOT NULL DEFAULT '',
                  brand TEXT NOT NULL DEFAULT '',
                  last4 TEXT NOT NULL DEFAULT '',
                  exp_month INTEGER,
                  exp_year INTEGER,
                  cardholder TEXT NOT NULL DEFAULT '',
                  codes_total INTEGER,
                  codes_unused INTEGER,
                  doc_type TEXT NOT NULL DEFAULT '',
                  created_at REAL NOT NULL,
                  updated_at REAL NOT NULL,
                  last_revealed_at REAL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_secrets_kind ON secrets(kind)")
            # Databases created before recovery codes and identity documents
            # existed have the table already, so CREATE TABLE IF NOT EXISTS is a
            # no-op for them and the new columns would simply be missing. Add
            # what is absent rather than versioning the schema: these are
            # nullable additions with defaults, so the migration is total.
            have = {row["name"] for row in conn.execute("PRAGMA table_info(secrets)")}
            for column, ddl in (
                ("codes_total", "codes_total INTEGER"),
                ("codes_unused", "codes_unused INTEGER"),
                ("doc_type", "doc_type TEXT NOT NULL DEFAULT ''"),
            ):
                if column not in have:
                    conn.execute(f"ALTER TABLE secrets ADD COLUMN {ddl}")
            conn.commit()

    def _row_to_record(self, row: sqlite3.Row) -> SecretRecord:
        return SecretRecord(
            id=row["id"],
            kind=cast(SecretKind, row["kind"]),
            label=row["label"],
            masked=row["masked"],
            account_id=row["account_id"],
            lifecycle=cast(SecretLifecycle, row["lifecycle"]),
            replaced_by=row["replaced_by"],
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            last_revealed_at=row["last_revealed_at"],
            username=row["username"],
            url=row["url"],
            brand=row["brand"],
            last4=row["last4"],
            exp_month=row["exp_month"],
            exp_year=row["exp_year"],
            cardholder=row["cardholder"],
            codes_total=row["codes_total"],
            codes_unused=row["codes_unused"],
            doc_type=row["doc_type"],
        )

    # --- read ---

    def list_secrets(
        self,
        *,
        kind: SecretKind | None = None,
        account_id: str | None = None,
    ) -> list[SecretRecord]:
        clauses: list[str] = []
        values: list[object] = []
        if kind is not None:
            clauses.append("kind = ?")
            values.append(kind)
        if account_id is not None:
            clauses.append("account_id = ?")
            values.append(account_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM secrets {where} ORDER BY kind ASC, created_at ASC", values
            ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def get(self, secret_id: str) -> SecretRecord | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM secrets WHERE id = ?", (secret_id,)).fetchone()
        return None if row is None else self._row_to_record(row)

    def reveal(self, secret_id: str) -> str | None:
        """Decrypt one payload — the password, or the full PAN.

        The only path out of this module for plaintext. Callers must have
        already passed the loopback + intent gate and must audit the call;
        ``last_revealed_at`` is stamped here so a reveal is visible in the
        record itself even if the audit file is lost.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT secret_blob, kind FROM secrets WHERE id = ?", (secret_id,)
            ).fetchone()
            if row is None:
                return None
            if row["kind"] == "recovery_codes":
                # This path hands back one sealed payload, and for a code set
                # that payload is EVERY code at once. A backup code is spent by
                # being used, so anything that reads the set without marking a
                # code used desynchronises the count and quietly re-issues
                # codes that no longer work. Callers take one at a time.
                raise SecretValidationError(
                    "recovery codes are consumed one at a time - use consume_recovery_code"
                )
            conn.execute(
                "UPDATE secrets SET last_revealed_at = ? WHERE id = ?", (time.time(), secret_id)
            )
            conn.commit()
        return self._seal.decrypt(row["secret_blob"])

    # --- write ---

    def create_password(
        self,
        *,
        label: str,
        password: str,
        username: str = "",
        url: str = "",
        account_id: str | None = None,
    ) -> SecretRecord:
        if not password:
            raise SecretValidationError("password must not be empty")
        secret_id = uuid.uuid4().hex
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO secrets (
                  id, kind, label, secret_blob, masked, account_id, lifecycle,
                  username, url, created_at, updated_at
                ) VALUES (?, 'password', ?, ?, ?, ?, 'active', ?, ?, ?, ?)
                """,
                (
                    secret_id,
                    label,
                    self._seal.encrypt(password),
                    mask_password(password),
                    account_id,
                    username,
                    url,
                    now,
                    now,
                ),
            )
            conn.commit()
        record = self.get(secret_id)
        assert record is not None
        return record

    def create_recovery_codes(
        self,
        *,
        label: str,
        codes: str | list[str],
        username: str = "",
        url: str = "",
        account_id: str | None = None,
    ) -> SecretRecord:
        """Store a set of single-use backup codes as one sealed blob.

        One blob rather than a row per code, deliberately. A row per code would
        make the remaining count a cheap ``COUNT(*)``, but it would also mean
        that leaking one row leaks one working code with a label attached to it.
        Sealed as a set, the codes are atomic: you cannot read any of them
        without unsealing all of them, which is the same bar as the password
        next to them. The count lives in its own clear column instead, so
        listing still never decrypts.
        """
        normalized = normalize_recovery_codes(codes)
        payload = json.dumps([{"code": code, "used_at": None} for code in normalized])
        total = len(normalized)
        secret_id = uuid.uuid4().hex
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO secrets (
                  id, kind, label, secret_blob, masked, account_id, lifecycle,
                  username, url, codes_total, codes_unused, created_at, updated_at
                ) VALUES (?, 'recovery_codes', ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?)
                """,
                (
                    secret_id,
                    label,
                    self._seal.encrypt(payload),
                    mask_recovery_codes(total, total),
                    account_id,
                    username,
                    url,
                    total,
                    total,
                    now,
                    now,
                ),
            )
            conn.commit()
        record = self.get(secret_id)
        assert record is not None
        return record

    def consume_recovery_code(self, secret_id: str) -> str | None:
        """Hand out the next unused code and mark it spent, in one transaction.

        A backup code works once. Returning one without marking it is how the
        same code gets typed into two prompts, the second is rejected, and the
        user concludes their codes are broken. Marking one without returning it
        silently burns a login. So both happen together or neither does, and an
        exhausted set returns ``None`` rather than a stale code.

        ``BEGIN IMMEDIATE`` on a connection with autocommit off is what makes
        two concurrent callers take two different codes instead of the same one.
        """
        conn = self._connect()
        try:
            conn.isolation_level = None
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT secret_blob, kind, codes_total FROM secrets WHERE id = ?", (secret_id,)
            ).fetchone()
            if row is None or row["kind"] != "recovery_codes":
                conn.execute("ROLLBACK")
                return None

            entries = json.loads(self._seal.decrypt(row["secret_blob"]))
            nxt = next((e for e in entries if e.get("used_at") is None), None)
            if nxt is None:
                conn.execute("ROLLBACK")
                return None

            now = time.time()
            nxt["used_at"] = now
            unused = sum(1 for e in entries if e.get("used_at") is None)
            total = int(row["codes_total"] or len(entries))
            conn.execute(
                """
                UPDATE secrets
                   SET secret_blob = ?, codes_unused = ?, masked = ?,
                       updated_at = ?, last_revealed_at = ?
                 WHERE id = ?
                """,
                (
                    self._seal.encrypt(json.dumps(entries)),
                    unused,
                    mask_recovery_codes(unused, total),
                    now,
                    now,
                    secret_id,
                ),
            )
            conn.execute("COMMIT")
            return cast(str, nxt["code"])
        except BaseException:
            with contextlib.suppress(sqlite3.Error):
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def create_identity(
        self,
        *,
        label: str,
        doc_type: IdentityDocType,
        number: str,
        account_id: str | None = None,
    ) -> SecretRecord:
        """Store a government or institutional identifier.

        Only the number is sealed, and only the document type and a last-four
        mask are kept in the clear - the same split the card path uses, for the
        same reason: a chooser UI needs to tell two documents apart without the
        vault being unsealed.

        Name, address and date of birth are NOT stored here. They are ordinary
        profile fields that a form-fill legitimately reads, and putting them
        behind the seal would mean unsealing the vault to type an address.
        """
        normalized = normalize_identity_number(number)
        secret_id = uuid.uuid4().hex
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO secrets (
                  id, kind, label, secret_blob, masked, account_id, lifecycle,
                  doc_type, created_at, updated_at
                ) VALUES (?, 'identity', ?, ?, ?, ?, 'active', ?, ?, ?)
                """,
                (
                    secret_id,
                    label,
                    self._seal.encrypt(normalized),
                    mask_identity(normalized),
                    account_id,
                    doc_type,
                    now,
                    now,
                ),
            )
            conn.commit()
        record = self.get(secret_id)
        assert record is not None
        return record

    def create_card(
        self,
        *,
        label: str,
        pan: str,
        exp_month: int,
        exp_year: int,
        cardholder: str = "",
        account_id: str | None = None,
        cvv: str | None = None,
    ) -> SecretRecord:
        """Store a card. ``cvv`` exists only to be refused loudly — see module docstring."""
        if cvv:
            raise SecretValidationError(
                "CVV/CVC is never stored: PCI DSS forbids retaining it after authorization, "
                "and OpenVault does not authorize payments. Drop the field."
            )
        pan = normalize_pan(pan)
        exp_month, exp_year = validate_expiry(exp_month, exp_year)
        brand = detect_brand(pan)
        last4 = pan[-4:]
        secret_id = uuid.uuid4().hex
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO secrets (
                  id, kind, label, secret_blob, masked, account_id, lifecycle,
                  brand, last4, exp_month, exp_year, cardholder, created_at, updated_at
                ) VALUES (?, 'payment_card', ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    secret_id,
                    label,
                    self._seal.encrypt(pan),
                    mask_pan(last4, brand),
                    account_id,
                    brand,
                    last4,
                    exp_month,
                    exp_year,
                    cardholder,
                    now,
                    now,
                ),
            )
            conn.commit()
        record = self.get(secret_id)
        assert record is not None
        return record

    def update(
        self,
        secret_id: str,
        *,
        label: str | None = None,
        username: str | None = None,
        url: str | None = None,
        cardholder: str | None = None,
        exp_month: int | None = None,
        exp_year: int | None = None,
        account_id: str | None = None,
    ) -> SecretRecord | None:
        """Metadata only.

        Changing the payload is ``rotate``, not ``update``: replacing a password
        or a PAN in place would erase the fact that the old one ever existed,
        and "which card was on file in March" is exactly the question an audit
        has to answer.
        """
        current = self.get(secret_id)
        if current is None:
            return None
        fields: list[str] = []
        values: list[object] = []
        for column, value in (
            ("label", label),
            ("username", username),
            ("url", url),
            ("cardholder", cardholder),
            ("account_id", account_id),
        ):
            if value is not None:
                fields.append(f"{column} = ?")
                values.append(value)
        if exp_month is not None or exp_year is not None:
            month = exp_month if exp_month is not None else (current.exp_month or 0)
            year = exp_year if exp_year is not None else (current.exp_year or 0)
            month, year = validate_expiry(month, year)
            fields.append("exp_month = ?")
            values.append(month)
            fields.append("exp_year = ?")
            values.append(year)
        fields.append("updated_at = ?")
        values.append(time.time())
        values.append(secret_id)
        with self._connect() as conn:
            conn.execute(f"UPDATE secrets SET {', '.join(fields)} WHERE id = ?", values)
            conn.commit()
        return self.get(secret_id)

    def delete(self, secret_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM secrets WHERE id = ?", (secret_id,))
            conn.commit()
            return cur.rowcount > 0

    def revoke(self, secret_id: str, *, reason: str = "operator_revoke") -> SecretRecord | None:
        """Mark unusable, keep the ciphertext until an explicit delete.

        Mirrors ``KeyVault.revoke``: a revoked card is still the answer to "what
        was charged", so dropping the row on revoke would destroy the audit
        trail at the exact moment it becomes interesting.
        """
        if self.get(secret_id) is None:
            return None
        with self._connect() as conn:
            conn.execute(
                "UPDATE secrets SET lifecycle = 'revoked', updated_at = ? WHERE id = ?",
                (time.time(), secret_id),
            )
            conn.commit()
        return self.get(secret_id)

    def rotate(
        self,
        secret_id: str,
        *,
        new_password: str | None = None,
        new_pan: str | None = None,
        exp_month: int | None = None,
        exp_year: int | None = None,
        label_suffix: str = "rotated",
    ) -> SecretRecord | None:
        """Mint a replacement and chain the old record to it via ``replaced_by``."""
        current = self.get(secret_id)
        if current is None:
            return None

        if current.kind == "password":
            if not new_password:
                raise SecretValidationError("rotating a password requires new_password")
            replacement = self.create_password(
                label=f"{current.label} ({label_suffix})",
                password=new_password,
                username=current.username,
                url=current.url,
                account_id=current.account_id,
            )
        else:
            if not new_pan:
                raise SecretValidationError("rotating a card requires new_pan")
            month = exp_month if exp_month is not None else current.exp_month
            year = exp_year if exp_year is not None else current.exp_year
            if month is None or year is None:
                raise SecretValidationError("rotating a card requires exp_month and exp_year")
            replacement = self.create_card(
                label=f"{current.label} ({label_suffix})",
                pan=new_pan,
                exp_month=month,
                exp_year=year,
                cardholder=current.cardholder,
                account_id=current.account_id,
            )

        with self._connect() as conn:
            conn.execute(
                """
                UPDATE secrets SET lifecycle = 'rotated', replaced_by = ?, updated_at = ?
                WHERE id = ?
                """,
                (replacement.id, time.time(), secret_id),
            )
            conn.commit()
        return replacement
