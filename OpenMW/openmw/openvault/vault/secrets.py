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

import re
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from openmw.openvault.paths import keys_db_path
from openmw.openvault.vault.crypto import Seal

SecretKind = Literal["password", "payment_card"]
SecretLifecycle = Literal["active", "revoked", "rotated", "compromised"]

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
                  created_at REAL NOT NULL,
                  updated_at REAL NOT NULL,
                  last_revealed_at REAL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_secrets_kind ON secrets(kind)")
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
                "SELECT secret_blob FROM secrets WHERE id = ?", (secret_id,)
            ).fetchone()
            if row is None:
                return None
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
