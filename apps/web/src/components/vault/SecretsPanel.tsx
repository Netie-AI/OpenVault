"use client";

/**
 * Passwords and payment cards over `/api/secrets*`.
 *
 * Masks in the list; reveal only on click (cleared after a short window).
 * No CVV field — backend refuses CVV with 400 and that refusal must stay visible.
 * Sealed vault: honest lock banner + unseal, not a blank hang.
 */

import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Modal } from "@/components/ui/Modal";
import { isApiError } from "@/lib/api/client";
import {
  createCard,
  createPassword,
  deleteSecret,
  fetchVaultStatus,
  listSecrets,
  revealSecretValue,
  retirePlaintextBackup,
  revokeSecret,
  rotateSecret,
  unsealVault,
  lockVault,
  setVaultPassphrase,
  type SecretRow,
  type VaultStatus,
} from "@/lib/api/secrets";

const REVEAL_TTL_MS = 15_000;

type CreateMode = "password" | "card" | null;
type RotateTarget = { row: SecretRow } | null;

export function SecretsPanel() {
  const [secrets, setSecrets] = useState<SecretRow[]>([]);
  const [status, setStatus] = useState<VaultStatus | null>(null);
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [revealed, setRevealed] = useState<Record<string, string>>({});
  const [passphrase, setPassphrase] = useState("");
  const [createMode, setCreateMode] = useState<CreateMode>(null);
  const [rotateTarget, setRotateTarget] = useState<RotateTarget>(null);

  // Password form
  const [pwLabel, setPwLabel] = useState("");
  const [pwUser, setPwUser] = useState("");
  const [pwUrl, setPwUrl] = useState("");
  const [pwSecret, setPwSecret] = useState("");

  // Card form — no CVV
  const [cardLabel, setCardLabel] = useState("");
  const [cardPan, setCardPan] = useState("");
  const [cardHolder, setCardHolder] = useState("");
  const [cardMonth, setCardMonth] = useState("");
  const [cardYear, setCardYear] = useState("");

  // Rotate form
  const [rotateValue, setRotateValue] = useState("");
  const [rotateMonth, setRotateMonth] = useState("");
  const [rotateYear, setRotateYear] = useState("");

  const refresh = useCallback(async (signal?: AbortSignal) => {
    const [rows, st] = await Promise.all([
      listSecrets({ signal }),
      fetchVaultStatus(signal),
    ]);
    setSecrets(rows);
    setStatus(st);
  }, []);

  useEffect(() => {
    const ac = new AbortController();
    void (async () => {
      try {
        await refresh(ac.signal);
      } catch (err) {
        if (!ac.signal.aborted) {
          setNotice(isApiError(err) ? err.message : "Could not load passwords/cards");
        }
      }
    })();
    return () => ac.abort();
  }, [refresh]);

  // Clear revealed plaintext on a timer so it does not linger in React state.
  useEffect(() => {
    const ids = Object.keys(revealed);
    if (ids.length === 0) return;
    const timers = ids.map((id) =>
      window.setTimeout(() => {
        setRevealed((r) => {
          if (!(id in r)) return r;
          const next = { ...r };
          delete next[id];
          return next;
        });
      }, REVEAL_TTL_MS),
    );
    return () => {
      for (const t of timers) window.clearTimeout(t);
    };
  }, [revealed]);

  async function run(id: string, action: () => Promise<unknown>, done: string) {
    setBusy(id);
    setNotice("");
    try {
      await action();
      await refresh();
      setNotice(done);
    } catch (err) {
      setNotice(isApiError(err) ? err.message : "That did not work");
    } finally {
      setBusy(null);
    }
  }

  async function onUnseal() {
    setBusy("unseal");
    setNotice("");
    try {
      const st = await unsealVault(passphrase);
      setStatus(st);
      setPassphrase("");
      await refresh();
      setNotice(st.sealed ? "Still sealed" : "Vault unsealed");
    } catch (err) {
      setNotice(isApiError(err) ? err.message : "Unseal failed");
    } finally {
      setBusy(null);
    }
  }

  async function onLock() {
    setBusy("lock");
    setNotice("");
    try {
      const st = await lockVault();
      setStatus(st);
      setNotice(st.sealed ? "Vault locked" : "Lock did not seal");
    } catch (err) {
      setNotice(isApiError(err) ? err.message : "Lock failed");
    } finally {
      setBusy(null);
    }
  }

  async function onSetPassphrase() {
    setBusy("set-passphrase");
    setNotice("");
    try {
      const st = await setVaultPassphrase(passphrase);
      setStatus(st);
      setPassphrase("");
      await refresh();
      setNotice(
        st.passphrase_configured
          ? "Passphrase configured. Lock, then Unseal, then retire the bak."
          : "Passphrase was not stored",
      );
    } catch (err) {
      setNotice(isApiError(err) ? err.message : "Set passphrase failed");
    } finally {
      setBusy(null);
    }
  }

  async function onRetireBackup() {
    setBusy("retire-bak");
    setNotice("");
    try {
      const st = await retirePlaintextBackup(passphrase);
      setStatus(st);
      setNotice(
        st.plaintext_backup_present
          ? "Plaintext backup still present"
          : "Plaintext master-key backup retired",
      );
    } catch (err) {
      setNotice(isApiError(err) ? err.message : "Retire failed");
    } finally {
      setBusy(null);
    }
  }

  async function toggleReveal(row: SecretRow) {
    if (revealed[row.id]) {
      setRevealed((r) => {
        const next = { ...r };
        delete next[row.id];
        return next;
      });
      return;
    }
    setBusy(`reveal-${row.id}`);
    setNotice("");
    try {
      const { secret } = await revealSecretValue(row.id);
      setRevealed((r) => ({ ...r, [row.id]: secret }));
    } catch (err) {
      setNotice(isApiError(err) ? err.message : "Could not reveal");
    } finally {
      setBusy(null);
    }
  }

  function resetCreateForms() {
    setPwLabel("");
    setPwUser("");
    setPwUrl("");
    setPwSecret("");
    setCardLabel("");
    setCardPan("");
    setCardHolder("");
    setCardMonth("");
    setCardYear("");
  }

  async function submitPassword() {
    setBusy("create-pw");
    setNotice("");
    try {
      await createPassword({
        label: pwLabel.trim(),
        password: pwSecret,
        username: pwUser.trim() || undefined,
        url: pwUrl.trim() || undefined,
      });
      resetCreateForms();
      setCreateMode(null);
      await refresh();
      setNotice("Password stored");
    } catch (err) {
      setNotice(isApiError(err) ? err.message : "Could not store password");
    } finally {
      setBusy(null);
    }
  }

  async function submitCard() {
    setBusy("create-card");
    setNotice("");
    try {
      await createCard({
        label: cardLabel.trim(),
        pan: cardPan.trim(),
        exp_month: Number(cardMonth),
        exp_year: Number(cardYear),
        cardholder: cardHolder.trim() || undefined,
      });
      resetCreateForms();
      setCreateMode(null);
      await refresh();
      setNotice("Card stored");
    } catch (err) {
      setNotice(isApiError(err) ? err.message : "Could not store card");
    } finally {
      setBusy(null);
    }
  }

  async function submitRotate() {
    if (!rotateTarget) return;
    const row = rotateTarget.row;
    setBusy(`rotate-${row.id}`);
    setNotice("");
    try {
      if (row.kind === "password") {
        await rotateSecret(row.id, { new_password: rotateValue });
      } else {
        await rotateSecret(row.id, {
          new_pan: rotateValue.trim(),
          exp_month: rotateMonth ? Number(rotateMonth) : undefined,
          exp_year: rotateYear ? Number(rotateYear) : undefined,
        });
      }
      setRotateTarget(null);
      setRotateValue("");
      setRotateMonth("");
      setRotateYear("");
      await refresh();
      setNotice("Rotated (old row kept as rotated)");
    } catch (err) {
      setNotice(isApiError(err) ? err.message : "Rotate failed");
    } finally {
      setBusy(null);
    }
  }

  const sealed = status?.sealed === true;
  const bakPresent = status?.plaintext_backup_present === true;
  const passwords = secrets.filter((s) => s.kind === "password");
  const cards = secrets.filter((s) => s.kind === "payment_card");

  return (
    <section className="mt-8 space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold text-foreground">Passwords & cards</h2>
          <p className="text-sm text-muted-foreground">
            Same custody as keys — masks in the list, reveal only on purpose.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={sealed}
            onClick={() => setCreateMode("password")}
          >
            Add password
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={sealed}
            onClick={() => setCreateMode("card")}
          >
            Add card
          </Button>
        </div>
      </div>

      {status ? (
        <div
          data-glass
          className={`rounded-2xl border px-4 py-3 text-sm ${
            sealed
              ? "border-warning-border bg-warning-bg text-foreground"
              : "border-border bg-card text-muted-foreground"
          }`}
        >
          {sealed ? (
            <div className="space-y-3">
              <p className="font-medium text-foreground">
                Vault is sealed
                {status.passphrase_configured
                  ? " — enter the passphrase to create, reveal, or change secrets."
                  : " — unlock before mutating secrets."}
              </p>
              <div className="flex flex-wrap items-end gap-2">
                <div className="min-w-[12rem] flex-1">
                  <Label htmlFor="vault-passphrase">Passphrase</Label>
                  <Input
                    id="vault-passphrase"
                    type="password"
                    autoComplete="current-password"
                    value={passphrase}
                    onChange={(e) => setPassphrase(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") void onUnseal();
                    }}
                  />
                </div>
                <Button
                  size="sm"
                  disabled={busy === "unseal"}
                  onClick={() => void onUnseal()}
                >
                  {busy === "unseal" ? "Unsealing…" : "Unseal"}
                </Button>
              </div>
            </div>
          ) : (
            <div className="space-y-3">
              <p>
                Vault open
                {status.wrap_method ? ` · wrap=${status.wrap_method}` : ""}
                {status.passphrase_configured ? " · passphrase configured" : ""}
              </p>
              <div className="flex flex-wrap items-end gap-2">
                {!status.passphrase_configured ? (
                  <>
                    <div className="min-w-[12rem] flex-1">
                      <Label htmlFor="vault-set-passphrase">New passphrase</Label>
                      <Input
                        id="vault-set-passphrase"
                        type="password"
                        autoComplete="new-password"
                        value={passphrase}
                        onChange={(e) => setPassphrase(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") void onSetPassphrase();
                        }}
                      />
                    </div>
                    <Button
                      size="sm"
                      disabled={busy === "set-passphrase" || !passphrase}
                      onClick={() => void onSetPassphrase()}
                    >
                      {busy === "set-passphrase" ? "Saving..." : "Set passphrase"}
                    </Button>
                  </>
                ) : null}
                <Button
                  variant="outline"
                  size="sm"
                  disabled={busy === "lock"}
                  onClick={() => void onLock()}
                >
                  {busy === "lock" ? "Locking..." : "Lock"}
                </Button>
              </div>
            </div>
          )}
        </div>
      ) : null}

      {bakPresent ? (
        <div
          data-glass
          className="rounded-2xl border border-warning-border bg-warning-bg px-4 py-3 text-sm text-foreground"
        >
          <p className="font-medium">
            Plaintext master-key backup is on disk (master.key.v0.bak).
          </p>
          <p className="mt-1 text-muted-foreground">
            Copying this vault folder can open sealed rows without the passphrase.
            Retire it after the live wrapped key verifies.
          </p>
          <Button
            size="sm"
            className="mt-3"
            disabled={sealed || busy === "retire-bak"}
            onClick={() => void onRetireBackup()}
          >
            {busy === "retire-bak" ? "Retiring..." : "Retire plaintext backup"}
          </Button>
        </div>
      ) : null}

      {notice ? <p className="text-sm text-muted-foreground">{notice}</p> : null}

      <SecretGroup
        title="Passwords"
        empty="No passwords yet."
        rows={passwords}
        revealed={revealed}
        busy={busy}
        sealed={sealed}
        onReveal={(row) => void toggleReveal(row)}
        onRevoke={(row) =>
          void run(row.id, () => revokeSecret(row.id), "Revoked")
        }
        onDelete={(row) =>
          void run(row.id, () => deleteSecret(row.id), "Deleted")
        }
        onRotate={(row) => {
          setRotateValue("");
          setRotateMonth("");
          setRotateYear("");
          setRotateTarget({ row });
        }}
      />

      <SecretGroup
        title="Payment cards"
        empty="No cards yet."
        rows={cards}
        revealed={revealed}
        busy={busy}
        sealed={sealed}
        onReveal={(row) => void toggleReveal(row)}
        onRevoke={(row) =>
          void run(row.id, () => revokeSecret(row.id), "Revoked")
        }
        onDelete={(row) =>
          void run(row.id, () => deleteSecret(row.id), "Deleted")
        }
        onRotate={(row) => {
          setRotateValue("");
          setRotateMonth(row.exp_month != null ? String(row.exp_month) : "");
          setRotateYear(row.exp_year != null ? String(row.exp_year) : "");
          setRotateTarget({ row });
        }}
      />

      <Modal
        isOpen={createMode === "password"}
        onClose={() => {
          setCreateMode(null);
          resetCreateForms();
        }}
        maxWidth="28rem"
        minWidth="20rem"
      >
        <div className="space-y-4 p-1">
          <h3 className="text-sm font-semibold text-foreground">Add password</h3>
          <Field label="Label" value={pwLabel} onChange={setPwLabel} required />
          <Field label="Username" value={pwUser} onChange={setPwUser} />
          <Field label="URL" value={pwUrl} onChange={setPwUrl} />
          <Field
            label="Password"
            value={pwSecret}
            onChange={setPwSecret}
            type="password"
            required
          />
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={() => setCreateMode(null)}>
              Cancel
            </Button>
            <Button
              disabled={busy === "create-pw" || !pwLabel.trim() || !pwSecret}
              onClick={() => void submitPassword()}
            >
              {busy === "create-pw" ? "Saving…" : "Save"}
            </Button>
          </div>
        </div>
      </Modal>

      <Modal
        isOpen={createMode === "card"}
        onClose={() => {
          setCreateMode(null);
          resetCreateForms();
        }}
        maxWidth="28rem"
        minWidth="20rem"
      >
        <div className="space-y-4 p-1">
          <h3 className="text-sm font-semibold text-foreground">Add payment card</h3>
          <p className="text-xs text-muted-foreground">
            CVV is never stored — do not enter it here.
          </p>
          <Field label="Label" value={cardLabel} onChange={setCardLabel} required />
          <Field label="Card number" value={cardPan} onChange={setCardPan} required />
          <Field label="Cardholder" value={cardHolder} onChange={setCardHolder} />
          <div className="grid grid-cols-2 gap-3">
            <Field
              label="Exp month"
              value={cardMonth}
              onChange={setCardMonth}
              placeholder="1-12"
              required
            />
            <Field
              label="Exp year"
              value={cardYear}
              onChange={setCardYear}
              placeholder="2029"
              required
            />
          </div>
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={() => setCreateMode(null)}>
              Cancel
            </Button>
            <Button
              disabled={
                busy === "create-card" ||
                !cardLabel.trim() ||
                !cardPan.trim() ||
                !cardMonth ||
                !cardYear
              }
              onClick={() => void submitCard()}
            >
              {busy === "create-card" ? "Saving…" : "Save"}
            </Button>
          </div>
        </div>
      </Modal>

      <Modal
        isOpen={rotateTarget != null}
        onClose={() => setRotateTarget(null)}
        maxWidth="28rem"
        minWidth="20rem"
      >
        {rotateTarget ? (
          <div className="space-y-4 p-1">
            <h3 className="text-sm font-semibold text-foreground">
              Rotate {rotateTarget.row.label}
            </h3>
            <p className="text-xs text-muted-foreground">
              Creates a replacement row and marks the old one rotated.
            </p>
            {rotateTarget.row.kind === "password" ? (
              <Field
                label="New password"
                value={rotateValue}
                onChange={setRotateValue}
                type="password"
                required
              />
            ) : (
              <>
                <Field
                  label="New card number"
                  value={rotateValue}
                  onChange={setRotateValue}
                  required
                />
                <div className="grid grid-cols-2 gap-3">
                  <Field label="Exp month" value={rotateMonth} onChange={setRotateMonth} />
                  <Field label="Exp year" value={rotateYear} onChange={setRotateYear} />
                </div>
              </>
            )}
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={() => setRotateTarget(null)}>
                Cancel
              </Button>
              <Button
                disabled={
                  busy === `rotate-${rotateTarget.row.id}` || !rotateValue.trim()
                }
                onClick={() => void submitRotate()}
              >
                {busy === `rotate-${rotateTarget.row.id}` ? "Rotating…" : "Rotate"}
              </Button>
            </div>
          </div>
        ) : null}
      </Modal>
    </section>
  );
}

function Field({
  label,
  value,
  onChange,
  type = "text",
  placeholder,
  required,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  type?: string;
  placeholder?: string;
  required?: boolean;
}) {
  const id = `field-${label.replace(/\s+/g, "-").toLowerCase()}`;
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>
        {label}
        {required ? " *" : ""}
      </Label>
      <Input
        id={id}
        type={type}
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        autoComplete="off"
      />
    </div>
  );
}

function SecretGroup({
  title,
  empty,
  rows,
  revealed,
  busy,
  sealed,
  onReveal,
  onRevoke,
  onDelete,
  onRotate,
}: {
  title: string;
  empty: string;
  rows: SecretRow[];
  revealed: Record<string, string>;
  busy: string | null;
  sealed: boolean;
  onReveal: (row: SecretRow) => void;
  onRevoke: (row: SecretRow) => void;
  onDelete: (row: SecretRow) => void;
  onRotate: (row: SecretRow) => void;
}) {
  return (
    <div
      data-glass
      className="overflow-hidden rounded-2xl border border-border bg-card"
    >
      <div className="flex items-center gap-2 border-b border-border px-4 py-3">
        <span className="text-sm font-semibold text-foreground">{title}</span>
        <span className="ml-auto text-xs text-muted-foreground">{rows.length}</span>
      </div>
      {rows.length === 0 ? (
        <p className="px-4 py-6 text-sm text-muted-foreground">{empty}</p>
      ) : (
        <ul>
          {rows.map((row) => (
            <li
              key={row.id}
              className="flex flex-wrap items-center gap-x-3 gap-y-2 border-t border-border px-4 py-3"
            >
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium text-foreground">
                  {row.label}
                  {row.lifecycle !== "active" ? (
                    <span className="ml-2 text-xs font-normal text-muted-foreground">
                      ({row.lifecycle})
                    </span>
                  ) : null}
                </p>
                <p className="truncate text-xs text-muted-foreground">
                  {row.kind === "password" ? (
                    <>
                      {row.username || "—"}
                      {row.url ? ` · ${row.url}` : ""}
                    </>
                  ) : (
                    <>
                      {row.brand || "card"}
                      {row.last4 ? ` · •••• ${row.last4}` : ""}
                      {row.exp_month != null && row.exp_year != null
                        ? ` · ${row.exp_month}/${row.exp_year}`
                        : ""}
                      {row.cardholder ? ` · ${row.cardholder}` : ""}
                    </>
                  )}
                  {" · "}
                  <span className="font-mono">
                    {revealed[row.id] ?? row.masked ?? "••••"}
                  </span>
                </p>
              </div>
              <div className="flex flex-wrap gap-1">
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={sealed || busy === `reveal-${row.id}`}
                  onClick={() => onReveal(row)}
                >
                  {revealed[row.id] ? "Hide" : "Reveal"}
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={sealed || busy === row.id || row.lifecycle !== "active"}
                  onClick={() => onRotate(row)}
                >
                  Rotate
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={sealed || busy === row.id || row.lifecycle !== "active"}
                  onClick={() => onRevoke(row)}
                >
                  Revoke
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={sealed || busy === row.id}
                  onClick={() => onDelete(row)}
                >
                  Delete
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
