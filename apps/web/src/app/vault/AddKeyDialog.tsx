"use client";

/**
 * Add a key by pasting it. That is the whole interaction.
 *
 * Copy rules (CLIPDROP_CONTRACT §4): name the provider, not the action;
 * never claim a state we have not observed; register-return is a statement,
 * not a question.
 */

import { useEffect, useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Modal } from "@/components/ui/Modal";
import { isApiError } from "@/lib/api/client";
import {
  createKey,
  precheckKey,
  KEY_ROLES,
  type KeyRole,
  type KeyRow,
  type ProviderSpec,
} from "@/lib/api/keys";
import { inferProvider, suggestLabel } from "@/lib/vault/inferProvider";
import {
  clearRegisterIntent,
  formatRegisterAgo,
  type RegisterIntent,
} from "@/lib/vault/registerIntent";

interface Props {
  isOpen: boolean;
  onClose: () => void;
  providers: ProviderSpec[];
  initialSecret?: string | null;
  pendingRegister?: RegisterIntent | null;
  onStored: (created: KeyRow) => void | Promise<void>;
  wireToProxy?: boolean;
  onWireToProxy?: (created: KeyRow) => void;
}

export function AddKeyDialog({
  isOpen,
  onClose,
  providers,
  initialSecret = null,
  pendingRegister = null,
  onStored,
  wireToProxy = true,
  onWireToProxy,
}: Props) {
  const [secret, setSecret] = useState("");
  const [manualProvider, setManualProvider] = useState<string | null>(null);
  const [manualRole, setManualRole] = useState<KeyRole | null>(null);
  const [manualLabel, setManualLabel] = useState<string | null>(null);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const knownIds = useMemo(() => new Set(providers.map((p) => p.id)), [providers]);
  const guess = useMemo(() => inferProvider(secret, knownIds), [secret, knownIds]);

  const providerId =
    manualProvider ??
    guess.providerId ??
    (pendingRegister && (!guess.providerId || guess.confidence === "weak")
      ? pendingRegister.providerId
      : null);

  const spec = useMemo(
    () => providers.find((p) => p.id === providerId) ?? null,
    [providers, providerId],
  );

  const role: KeyRole = manualRole ?? spec?.default_role ?? "backup";
  const label = manualLabel ?? (spec ? suggestLabel(spec.name, secret) : "");

  const fromRegister =
    Boolean(pendingRegister) &&
    providerId === pendingRegister?.providerId &&
    (!guess.providerId || guess.providerId === pendingRegister.providerId);

  useEffect(() => {
    if (!isOpen) {
      setSecret("");
      setManualProvider(null);
      setManualRole(null);
      setManualLabel(null);
      setShowAdvanced(false);
      setError("");
      return;
    }
    if (initialSecret) setSecret(initialSecret);
  }, [isOpen, initialSecret]);

  useEffect(() => {
    if (secret.trim() && !guess.providerId && !manualProvider && !pendingRegister) {
      setShowAdvanced(true);
    }
  }, [secret, guess.providerId, manualProvider, pendingRegister]);

  const canSubmit = Boolean(secret.trim() && providerId && label.trim()) && !busy;

  const title = spec
    ? `${spec.name} key detected`
    : initialSecret
      ? "API key detected"
      : "Add a key";

  const subtitle = fromRegister && pendingRegister
    ? `You registered ${formatRegisterAgo(pendingRegister.clickedAt)}.`
    : "Paste it. We work out the provider, store it encrypted, and test it.";

  async function store(andWire: boolean) {
    if (!canSubmit || !providerId) return;
    setBusy(true);
    setError("");
    try {
      const created = await createKey({
        label: label.trim(),
        provider: providerId,
        secret: secret.trim(),
        role,
        base_url: spec?.base_url,
      });
      // Fire-and-forget probe — no green tick until it returns (page refresh).
      void precheckKey(created.id).catch(() => undefined);
      if (fromRegister) clearRegisterIntent();
      await onStored(created);
      onClose();
      if (andWire && wireToProxy) onWireToProxy?.(created);
    } catch (err) {
      setError(isApiError(err) ? err.message : "Could not store the key");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal isOpen={isOpen} onClose={onClose} maxWidth="34rem" minWidth="min(30rem, 92vw)">
      <div className="space-y-4 p-1">
        <div>
          <h2 className="text-base font-semibold text-foreground">{title}</h2>
          <p className="mt-1 text-sm text-muted-foreground">{subtitle}</p>
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="vault-secret">API key</Label>
          <Input
            id="vault-secret"
            type="password"
            autoFocus
            autoComplete="off"
            spellCheck={false}
            placeholder="sk-…"
            value={secret}
            onChange={(e) => setSecret(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && canSubmit) void store(true);
            }}
          />
          {secret.trim() && spec ? (
            <p className="text-xs text-muted-foreground">
              {spec.name}
              {guess.reason ? ` · ${guess.reason}` : ""}
            </p>
          ) : secret.trim() ? (
            <p className="text-xs text-warning">
              {guess.reason || "Pick the provider below"}
            </p>
          ) : null}
        </div>

        {spec && !showAdvanced ? (
          <dl className="rounded-xl border border-border bg-card/50 p-3 text-xs">
            <div className="flex justify-between gap-4 py-0.5">
              <dt className="text-muted-foreground">Name</dt>
              <dd className="truncate text-foreground">{label}</dd>
            </div>
            <div className="flex justify-between gap-4 py-0.5">
              <dt className="text-muted-foreground">Role</dt>
              <dd className="capitalize text-foreground">{role}</dd>
            </div>
            <div className="flex justify-between gap-4 py-0.5">
              <dt className="text-muted-foreground">Endpoint</dt>
              <dd className="truncate text-foreground">{spec.base_url}</dd>
            </div>
          </dl>
        ) : null}

        {showAdvanced ? (
          <div className="space-y-3 rounded-xl border border-border p-3">
            <div className="space-y-1.5">
              <Label htmlFor="vault-provider">Provider</Label>
              <select
                id="vault-provider"
                className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm text-foreground"
                value={providerId ?? ""}
                onChange={(e) => setManualProvider(e.target.value || null)}
              >
                <option value="">Select a provider…</option>
                {providers.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="vault-label">Name</Label>
              <Input
                id="vault-label"
                value={label}
                onChange={(e) => setManualLabel(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="vault-role">Role</Label>
              <select
                id="vault-role"
                className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm capitalize text-foreground"
                value={role}
                onChange={(e) => setManualRole(e.target.value as KeyRole)}
              >
                {KEY_ROLES.map((r) => (
                  <option key={r} value={r}>
                    {r}
                  </option>
                ))}
              </select>
            </div>
          </div>
        ) : null}

        {error ? <p className="text-sm text-destructive">{error}</p> : null}

        <div className="flex items-center justify-between gap-2 pt-1">
          <Button variant="ghost" size="sm" onClick={() => setShowAdvanced((v) => !v)}>
            {showAdvanced ? "Hide details" : "Change details"}
          </Button>
          <div className="flex gap-2">
            <Button variant="ghost" onClick={onClose} disabled={busy}>
              Not now
            </Button>
            {wireToProxy ? (
              <Button onClick={() => void store(true)} disabled={!canSubmit}>
                {busy ? "Stored · testing…" : "Add & open Proxy"}
              </Button>
            ) : (
              <Button onClick={() => void store(false)} disabled={!canSubmit}>
                {busy ? "Stored · testing…" : "Add key"}
              </Button>
            )}
          </div>
        </div>
      </div>
    </Modal>
  );
}
