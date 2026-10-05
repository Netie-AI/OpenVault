"use client";

/**
 * Lock / unseal / passkey controls for the local vault.
 *
 * Passkeys are Windows Hello, Touch ID, or optional iPhone -- not autofill.
 */

import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { isApiError } from "@/lib/api/client";
import {
  fetchVaultStatus,
  lockVault,
  retirePlaintextBackup,
  setVaultPassphrase,
  unsealVault,
  type VaultStatus,
} from "@/lib/api/secrets";
import { readAdminSession, redactShown, writeAdminSession } from "@/lib/api/adminSession";
import {
  clearVaultPasskey,
  registerVaultPasskey,
  unsealVaultWithPasskey,
  webauthnAvailable,
} from "@/lib/api/webauthn";
import {
  SEALED_GATE_BODY,
  SEALED_GATE_TITLE,
  sealedGateOpen,
} from "@/lib/vault/sealedGate";

export function VaultSealBar({
  onStatus,
}: {
  onStatus?: (status: VaultStatus) => void;
}) {
  const [status, setStatus] = useState<VaultStatus | null>(null);
  const [passphrase, setPassphrase] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const [passkeyOk, setPasskeyOk] = useState(false);
  const [admin, setAdmin] = useState("");
  const [gateDismissed, setGateDismissed] = useState(false);
  const [unauthorized, setUnauthorized] = useState(false);
  const onStatusRef = useRef(onStatus);
  onStatusRef.current = onStatus;

  function apply(st: VaultStatus) {
    setStatus(st);
    onStatusRef.current?.(st);
  }

  useEffect(() => {
    setPasskeyOk(webauthnAvailable());
    setAdmin(readAdminSession());
  }, []);

  function note(text: string) {
    setNotice(redactShown(text, [passphrase, admin, readAdminSession()]));
  }

  useEffect(() => {
    const ac = new AbortController();
    void (async () => {
      try {
        const st = await fetchVaultStatus(ac.signal);
        if (ac.signal.aborted) return;
        setUnauthorized(false);
        apply(st);
      } catch (err) {
        if (ac.signal.aborted) return;
        if (isApiError(err) && err.status === 401) {
          setUnauthorized(true);
          setNotice("Could not read vault lock state. Enter the admin token, then check again.");
          return;
        }
        setNotice(isApiError(err) ? err.message : "Could not read vault lock state");
      }
    })();
    return () => ac.abort();
  }, []);

  async function onCheck() {
    writeAdminSession(admin);
    setBusy("status");
    setNotice("");
    try {
      const st = await fetchVaultStatus();
      setUnauthorized(false);
      apply(st);
      note(st.sealed ? "Vault is sealed" : "Vault open");
    } catch (err) {
      if (isApiError(err) && err.status === 401) {
        setUnauthorized(true);
        note("Could not read vault lock state. Enter the admin token, then check again.");
        return;
      }
      note(isApiError(err) ? err.message : "Could not read vault lock state");
    } finally {
      setBusy(null);
    }
  }

  async function onUnseal() {
    setBusy("unseal");
    setNotice("");
    writeAdminSession(admin);
    try {
      const st = await unsealVault(passphrase);
      apply(st);
      setPassphrase("");
      if (!st.sealed) setGateDismissed(false);
      note(st.sealed ? "Still sealed" : "Vault unsealed");
    } catch (err) {
      note(isApiError(err) ? err.message : "Unseal failed");
    } finally {
      setBusy(null);
    }
  }

  async function onLock() {
    setBusy("lock");
    setNotice("");
    try {
      const st = await lockVault();
      apply(st);
      note(st.sealed ? "Vault locked" : "Lock did not seal");
    } catch (err) {
      note(isApiError(err) ? err.message : "Lock failed");
    } finally {
      setBusy(null);
    }
  }

  async function onSetPassphrase() {
    setBusy("set-passphrase");
    setNotice("");
    try {
      const st = await setVaultPassphrase(passphrase);
      apply(st);
      setPassphrase("");
      note(
        st.passphrase_configured
          ? "Passphrase configured. Lock, then Unseal, then retire the bak."
          : "Passphrase was not stored",
      );
    } catch (err) {
      note(isApiError(err) ? err.message : "Set passphrase failed");
    } finally {
      setBusy(null);
    }
  }

  async function onPasskeyUnseal() {
    setBusy("passkey-unseal");
    setNotice("");
    try {
      const st = await unsealVaultWithPasskey();
      apply(st);
      note(st.sealed ? "Still sealed" : "Vault unsealed with passkey");
    } catch (err) {
      note(
        isApiError(err) ? err.message : err instanceof Error ? err.message : "Passkey unseal failed",
      );
    } finally {
      setBusy(null);
    }
  }

  async function onRegisterPasskey(hybrid: boolean) {
    setBusy(hybrid ? "passkey-iphone" : "passkey-platform");
    setNotice("");
    try {
      const st = await registerVaultPasskey(hybrid);
      apply(st);
      note(
        st.webauthn_registered
          ? hybrid
            ? "iPhone passkey registered. Passphrase still unlocks this vault."
            : "Passkey registered. Windows Hello / Face ID / fingerprint will unseal this vault."
          : "Passkey was not stored",
      );
    } catch (err) {
      note(
        isApiError(err)
          ? err.message
          : err instanceof Error
            ? err.message
            : "Passkey register failed",
      );
    } finally {
      setBusy(null);
    }
  }

  async function onClearPasskey() {
    setBusy("passkey-clear");
    setNotice("");
    try {
      const st = await clearVaultPasskey();
      apply(st);
      note(st.webauthn_registered ? "Passkey still registered" : "Passkey removed");
    } catch (err) {
      note(isApiError(err) ? err.message : "Could not remove passkey");
    } finally {
      setBusy(null);
    }
  }

  async function onRetireBackup() {
    setBusy("retire-bak");
    setNotice("");
    try {
      const st = await retirePlaintextBackup(passphrase);
      apply(st);
      note(
        st.plaintext_backup_present
          ? "Plaintext backup still present"
          : "Plaintext master-key backup retired",
      );
    } catch (err) {
      note(isApiError(err) ? err.message : "Retire failed");
    } finally {
      setBusy(null);
    }
  }

  const sealed = status?.sealed === true;
  const bakPresent = status?.plaintext_backup_present === true;
  const showGate = sealedGateOpen(status?.sealed, gateDismissed, unauthorized);

  return (
    <div className="mb-5 space-y-3">
      {!status && unauthorized ? (
        <div
          data-glass
          className="rounded-2xl border border-warning-border bg-warning-bg px-4 py-3 text-sm text-foreground"
        >
          <p className="font-medium" data-testid="vault-seal-state">
            {SEALED_GATE_TITLE}
          </p>
          <Button className="mt-2" size="sm" onClick={() => setGateDismissed(false)}>
            Enter passphrase
          </Button>
        </div>
      ) : null}

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
              <p className="font-medium text-foreground" data-testid="vault-seal-state">
                {SEALED_GATE_TITLE}
                {status.passphrase_configured
                  ? " - enter the passphrase, or use Face ID / fingerprint."
                  : " - unlock before mutating keys or secrets."}
              </p>
              <div className="flex flex-wrap items-center gap-2">
                <Button size="sm" onClick={() => setGateDismissed(false)}>
                  Enter passphrase
                </Button>
                {passkeyOk && status.webauthn_registered ? (
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={busy === "passkey-unseal"}
                    onClick={() => void onPasskeyUnseal()}
                  >
                    {busy === "passkey-unseal"
                      ? "Waiting for device..."
                      : "Unseal with Face ID / fingerprint"}
                  </Button>
                ) : null}
              </div>
            </div>
          ) : (
            <div className="space-y-3">
              <p data-testid="vault-seal-state">
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
              {passkeyOk ? (
                <div className="flex flex-wrap items-center gap-2">
                  {status.webauthn_registered ? (
                    <>
                      <p className="text-xs text-muted-foreground">
                        Passkey registered
                        {status.webauthn_hybrid ? " (iPhone / nearby)" : " (this PC)"}. Not autofill.
                      </p>
                      <Button
                        variant="ghost"
                        size="sm"
                        disabled={busy === "passkey-clear"}
                        onClick={() => void onClearPasskey()}
                      >
                        {busy === "passkey-clear" ? "Removing..." : "Remove passkey"}
                      </Button>
                    </>
                  ) : (
                    <>
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={busy === "passkey-platform"}
                        onClick={() => void onRegisterPasskey(false)}
                      >
                        {busy === "passkey-platform"
                          ? "Waiting for device..."
                          : "Add Windows Hello / Face ID / fingerprint"}
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={busy === "passkey-iphone"}
                        onClick={() => void onRegisterPasskey(true)}
                      >
                        {busy === "passkey-iphone" ? "Waiting for iPhone..." : "Add iPhone passkey"}
                      </Button>
                    </>
                  )}
                </div>
              ) : null}
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

      {showGate ? (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
          role="dialog"
          aria-modal="true"
          aria-labelledby="sealed-gate-title"
          data-testid="sealed-gate"
        >
          <div className="w-full max-w-md rounded-2xl border border-border bg-card p-6 text-foreground">
            <h2 id="sealed-gate-title" className="text-lg font-semibold">
              {SEALED_GATE_TITLE}
            </h2>
            <p className="mt-2 text-sm text-muted-foreground">{SEALED_GATE_BODY}</p>
            <div className="mt-4 space-y-3">
              <div>
                <Label htmlFor="sealed-gate-admin">Admin token</Label>
                <Input
                  id="sealed-gate-admin"
                  type="password"
                  autoComplete="off"
                  spellCheck={false}
                  value={admin}
                  onChange={(e) => {
                    setAdmin(e.target.value);
                    writeAdminSession(e.target.value);
                  }}
                />
                <p className="mt-1 text-xs text-muted-foreground">
                  Same session the Providers page uses.
                </p>
              </div>
              <div>
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
              <div className="flex flex-wrap gap-2">
                <Button size="sm" disabled={busy === "status"} onClick={() => void onCheck()}>
                  {busy === "status" ? "Checking..." : "Check again"}
                </Button>
                <Button size="sm" disabled={busy === "unseal"} onClick={() => void onUnseal()}>
                  {busy === "unseal" ? "Unsealing..." : "Unseal"}
                </Button>
                <Button variant="outline" size="sm" onClick={() => setGateDismissed(true)}>
                  Not now
                </Button>
              </div>
            </div>
          </div>
        </div>
      ) : null}

      {notice ? <p className="text-sm text-muted-foreground">{notice}</p> : null}
    </div>
  );
}
