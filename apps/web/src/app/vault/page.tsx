"use client";

/**
 * The vault.
 *
 * Two principles drive the layout:
 *
 * 1. The user should never do work we can do for them. Keys sitting in the
 *    environment are offered as a single import, not a scan-review-select-
 *    confirm wizard. Adding a key is ClipDrop → one tap → Proxy.
 * 2. Nothing claims more than it knows. A key's status is whatever the last
 *    real provider probe returned — "unknown" is displayed as unknown rather
 *    than optimistically as fine. Empty vaults show a catch zone, not role dots.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { PageContainer } from "@/components/ui/PageContainer";
import { PageHeader } from "@/components/ui/PageHeader";
import { ClipDropZone } from "@/components/vault/ClipDropZone";
import { isApiError } from "@/lib/api/client";
import {
  deleteKey,
  ingestEnv,
  listKeys,
  listProviders,
  precheckAll,
  precheckKey,
  revealSecret,
  revokeKey,
  scanEnv,
  updateKey,
  fetchCoverage,
  fetchOpenFreeBudget,
  KEY_ROLES,
  type EnvCandidate,
  type KeyRole,
  type KeyRow,
  type PrecheckStatus,
  type ProviderSpec,
  type CoverageReport,
  type OpenFreeBudget,
} from "@/lib/api/keys";
import {
  readRegisterIntent,
  rememberRegisterIntent,
  type RegisterIntent,
} from "@/lib/vault/registerIntent";
import { AddKeyDialog } from "./AddKeyDialog";

const ROLE_BLURB: Record<KeyRole, string> = {
  primary: "Tried first",
  backup: "Used when primary fails",
  cheap: "Preferred for bulk work",
  free: "Free tiers, tried last",
};

const STATUS_STYLE: Record<PrecheckStatus, { label: string; className: string }> = {
  ok: { label: "Working", className: "border-success-border bg-success-bg text-success" },
  auth_fail: { label: "Rejected", className: "border-destructive/40 bg-destructive/10 text-destructive" },
  rate_limit: { label: "Rate limited", className: "border-warning-border bg-warning-bg text-warning" },
  error: { label: "Unreachable", className: "border-warning-border bg-warning-bg text-warning" },
  unknown: { label: "Not tested", className: "border-border text-muted-foreground" },
};

function statusOf(key: KeyRow) {
  return STATUS_STYLE[key.precheck_status] ?? STATUS_STYLE.unknown;
}

export default function VaultPage() {
  const router = useRouter();
  const [keys, setKeys] = useState<KeyRow[]>([]);
  const [providers, setProviders] = useState<ProviderSpec[]>([]);
  const [envCandidates, setEnvCandidates] = useState<EnvCandidate[]>([]);
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const [revealed, setRevealed] = useState<Record<string, string>>({});
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [clipSecret, setClipSecret] = useState<string | null>(null);
  const [pendingRegister, setPendingRegister] = useState<RegisterIntent | null>(null);
  const [coverage, setCoverage] = useState<CoverageReport | null>(null);
  const [budget, setBudget] = useState<OpenFreeBudget | null>(null);

  const refresh = useCallback(async () => {
    setKeys(await listKeys());
  }, []);

  useEffect(() => {
    const ac = new AbortController();
    void (async () => {
      try {
        const [keyRows, catalog] = await Promise.all([
          listKeys(ac.signal),
          listProviders(ac.signal),
        ]);
        setKeys(keyRows);
        setProviders(catalog);
      } catch (err) {
        if (!ac.signal.aborted) {
          setNotice(isApiError(err) ? err.message : "Could not reach the vault");
        }
      }
      // Environment scanning is read-only and returns masked values only, so
      // it is safe to run unprompted. Nothing is imported without a click.
      try {
        setEnvCandidates((await scanEnv(ac.signal)).filter((c) => c.known));
      } catch {
        // A failed scan just means no banner; never block the page on it.
      }
      try {
        const [cov, bud] = await Promise.all([
          fetchCoverage(ac.signal),
          fetchOpenFreeBudget(ac.signal),
        ]);
        if (!ac.signal.aborted) {
          setCoverage(cov);
          setBudget(bud);
        }
      } catch {
        /* optional strips */
      }
    })();
    return () => ac.abort();
  }, []);

  useEffect(() => {
    setPendingRegister(readRegisterIntent());
  }, [addOpen]);

  function openAdd(secret?: string) {
    setClipSecret(secret ?? null);
    setPendingRegister(readRegisterIntent());
    setAddOpen(true);
  }

  function onClipSecret(secret: string) {
    openAdd(secret);
  }

  function startRegister(providerId: string, providerName: string, registerUrl: string) {
    rememberRegisterIntent({ providerId, providerName, registerUrl });
    setPendingRegister(readRegisterIntent());
    window.open(registerUrl, "_blank", "noopener");
  }

  const knownProviderIds = useMemo(
    () => new Set(providers.map((p) => p.id)),
    [providers],
  );
  const providerNames = useMemo(() => {
    const m = new Map<string, string>();
    for (const p of providers) m.set(p.id, p.name);
    return m;
  }, [providers]);

  const grouped = useMemo(() => {
    const groups: Record<string, KeyRow[]> = {};
    for (const key of keys) {
      const role = (KEY_ROLES as readonly string[]).includes(key.role) ? key.role : "other";
      (groups[role] ||= []).push(key);
    }
    for (const rows of Object.values(groups)) {
      rows.sort((a, b) => a.priority - b.priority || a.label.localeCompare(b.label));
    }
    return groups;
  }, [keys]);

  const working = keys.filter((k) => k.precheck_status === "ok").length;
  const failing = keys.filter(
    (k) => k.precheck_status === "auth_fail" || k.precheck_status === "error",
  ).length;
  const hasFreeFallback = keys.some(
    (k) =>
      k.enabled &&
      (k.role === "free" ||
        k.role === "cheap" ||
        ["groq", "google", "openrouter", "ollama"].includes(k.provider)),
  );
  const freeSuggest =
    coverage?.free_or_local_catalog?.find((p) => p.tier === "free" || p.tier === "freemium") ??
    null;
  const remaining =
    budget?.remaining_tokens ?? budget?.remaining ?? null;

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

  async function importEnv() {
    setBusy("env");
    try {
      await ingestEnv(false);
      setEnvCandidates([]);
      await refresh();
      setNotice("Imported. Testing them now…");
      await precheckAll();
      await refresh();
    } catch (err) {
      setNotice(isApiError(err) ? err.message : "Import failed");
    } finally {
      setBusy(null);
    }
  }

  async function toggleReveal(key: KeyRow) {
    if (revealed[key.id]) {
      setRevealed((r) => {
        const next = { ...r };
        delete next[key.id];
        return next;
      });
      return;
    }
    try {
      const { secret } = await revealSecret(key.id);
      setRevealed((r) => ({ ...r, [key.id]: secret }));
    } catch (err) {
      setNotice(isApiError(err) ? err.message : "Could not reveal that key");
    }
  }

  return (
    <PageContainer>
      <PageHeader
        title="Vault"
        description="Every key, encrypted at rest and tested against its provider."
      />

      {budget ? (
        <div
          data-glass
          className="mb-4 rounded-2xl border border-border bg-card px-4 py-3 text-sm text-muted-foreground"
        >
          OpenFree budget ({budget.tier || "free"}):{" "}
          <span className="font-medium text-foreground">
            {remaining != null ? `${Number(remaining).toLocaleString()} tokens left` : "—"}
          </span>
          {budget.tokens_per_min != null
            ? ` · ${Number(budget.tokens_per_min).toLocaleString()} tok/min`
            : ""}
        </div>
      ) : null}

      {!hasFreeFallback && freeSuggest ? (
        <div
          data-glass
          className="mb-5 flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-warning-border bg-warning-bg p-4"
        >
          <div className="min-w-0">
            <p className="text-sm font-medium text-foreground">
              No free fallback key yet
            </p>
            <p className="mt-0.5 text-sm text-muted-foreground">
              Add {freeSuggest.name} so chat still works when paid providers fail.
            </p>
          </div>
          {freeSuggest.register_url ? (
            <Button
              variant="outline"
              size="sm"
              onClick={() =>
                startRegister(
                  freeSuggest.id,
                  freeSuggest.name,
                  freeSuggest.register_url as string,
                )
              }
            >
              Get free key
            </Button>
          ) : null}
        </div>
      ) : null}

      {/* The single highest-value action on the page: keys the user already
          has, imported without them hunting for them. */}
      {envCandidates.length > 0 ? (
        <div
          data-glass
          className="mb-5 flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-info-border bg-info-bg p-4"
        >
          <div className="min-w-0">
            <p className="text-sm font-medium text-foreground">
              {envCandidates.length} provider key
              {envCandidates.length === 1 ? "" : "s"} found on this machine
            </p>
            <p className="truncate text-xs text-muted-foreground">
              {envCandidates.map((c) => c.env_key).join(" · ")}
            </p>
          </div>
          <Button onClick={() => void importEnv()} disabled={busy === "env"}>
            {busy === "env" ? "Importing…" : "Import them"}
          </Button>
        </div>
      ) : null}

      {keys.length === 0 ? (
        <div className="mb-5 space-y-4">
          <ClipDropZone
            hero
            onSecret={onClipSecret}
            ignoreSecret={clipSecret}
            knownProviderIds={knownProviderIds}
            providerNames={providerNames}
          />
          <div className="flex flex-wrap items-center justify-center gap-2">
            <Button variant="outline" onClick={() => openAdd()}>
              Type a key instead
            </Button>
          </div>
          {pendingRegister ? (
            <p className="text-center text-xs text-muted-foreground">
              {pendingRegister.providerName} — copy the key from the signup tab and return.
            </p>
          ) : null}
        </div>
      ) : (
        <div className="mb-5 space-y-3">
          <ClipDropZone
            onSecret={onClipSecret}
            ignoreSecret={clipSecret}
            knownProviderIds={knownProviderIds}
            providerNames={providerNames}
          />
          <div className="flex flex-wrap items-center gap-2">
            <Button onClick={() => openAdd()}>Add key</Button>
            <Button
              variant="outline"
              onClick={() => void run("all", precheckAll, "All keys tested")}
              disabled={busy === "all"}
            >
              {busy === "all" ? "Testing…" : "Test all"}
            </Button>
            <div className="ml-auto flex flex-wrap gap-2 text-xs">
              <span className="rounded-full border border-border px-2.5 py-1 text-muted-foreground">
                {keys.length} total
              </span>
              {working > 0 ? (
                <span className="rounded-full border border-success-border bg-success-bg px-2.5 py-1 text-success">
                  {working} working
                </span>
              ) : null}
              {failing > 0 ? (
                <span className="rounded-full border border-destructive/40 bg-destructive/10 px-2.5 py-1 text-destructive">
                  {failing} need attention
                </span>
              ) : null}
            </div>
          </div>
        </div>
      )}

      {notice ? <p className="mb-4 text-sm text-muted-foreground">{notice}</p> : null}

      <div className="space-y-3">
        {([...KEY_ROLES, "other"] as const)
          .filter((role) => (grouped[role] ?? []).length > 0)
          .map((role) => {
            const rows = grouped[role] ?? [];
            const isCollapsed = collapsed[role] ?? false;
            const groupOk = rows.filter((r) => r.precheck_status === "ok").length;
            return (
              <section
                key={role}
                data-glass
                className="overflow-hidden rounded-2xl border border-border bg-card"
              >
                <button
                  type="button"
                  onClick={() => setCollapsed((c) => ({ ...c, [role]: !isCollapsed }))}
                  className="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-accent/40"
                  aria-expanded={!isCollapsed}
                >
                  <span
                    aria-hidden
                    className={`text-muted-foreground transition-transform ${isCollapsed ? "" : "rotate-90"}`}
                  >
                    ›
                  </span>
                  <span className="text-sm font-semibold capitalize text-foreground">{role}</span>
                  <span className="text-xs text-muted-foreground">
                    {ROLE_BLURB[role as KeyRole] ?? "Uncategorised"}
                  </span>
                  <span className="ml-auto text-xs text-muted-foreground">
                    {groupOk}/{rows.length} working
                  </span>
                </button>

                {!isCollapsed ? (
                  <ul>
                    {rows.map((key) => {
                      const status = statusOf(key);
                      return (
                        <li
                          key={key.id}
                          className="flex flex-wrap items-center gap-x-3 gap-y-2 border-t border-border px-4 py-3"
                        >
                          <div className="min-w-0 flex-1">
                            <p className="truncate text-sm font-medium text-foreground">
                              {key.label}
                            </p>
                            <p className="truncate text-xs text-muted-foreground">
                              {key.provider}
                              {key.last_latency_ms != null
                                ? ` · ${Math.round(key.last_latency_ms)} ms`
                                : ""}
                              {" · "}
                              <span className="font-mono">
                                {revealed[key.id] ?? key.masked_secret ?? "••••"}
                              </span>
                            </p>
                          </div>

                          <span
                            className={`rounded-full border px-2 py-0.5 text-xs ${status.className}`}
                          >
                            {status.label}
                          </span>

                          {/* Changing a key's role is a one-step choice. Drag
                              between groups is deliberately not used: without a
                              bulk reorder endpoint it would be optimistic-only
                              and silently revert on reload. */}
                          <select
                            aria-label={`Role for ${key.label}`}
                            className="h-8 rounded-md border border-input bg-background px-2 text-xs capitalize text-foreground"
                            value={KEY_ROLES.includes(key.role as KeyRole) ? key.role : "backup"}
                            onChange={(e) =>
                              void run(
                                key.id,
                                () => updateKey(key.id, { role: e.target.value as KeyRole }),
                                `Moved to ${e.target.value}`,
                              )
                            }
                            disabled={busy === key.id}
                          >
                            {KEY_ROLES.map((r) => (
                              <option key={r} value={r}>
                                {r}
                              </option>
                            ))}
                          </select>

                          <div className="flex gap-1">
                            <Button
                              variant="ghost"
                              size="sm"
                              disabled={busy === key.id}
                              onClick={() =>
                                void run(key.id, () => precheckKey(key.id), "Tested")
                              }
                            >
                              Test
                            </Button>
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => void toggleReveal(key)}
                            >
                              {revealed[key.id] ? "Hide" : "Reveal"}
                            </Button>
                            <Button
                              variant="ghost"
                              size="sm"
                              disabled={busy === key.id}
                              onClick={() =>
                                void run(key.id, () => revokeKey(key.id), "Revoked")
                              }
                            >
                              Revoke
                            </Button>
                            <Button
                              variant="ghost"
                              size="sm"
                              disabled={busy === key.id}
                              onClick={() =>
                                void run(key.id, () => deleteKey(key.id), "Deleted")
                              }
                            >
                              Delete
                            </Button>
                          </div>
                        </li>
                      );
                    })}
                  </ul>
                ) : null}
              </section>
            );
          })}
      </div>

      <AddKeyDialog
        isOpen={addOpen}
        onClose={() => {
          setAddOpen(false);
          setClipSecret(null);
        }}
        providers={providers}
        initialSecret={clipSecret}
        pendingRegister={pendingRegister}
        onStored={async () => {
          await refresh();
          setClipSecret(null);
        }}
        onWireToProxy={(created) => {
          try {
            sessionStorage.setItem(
              "openvault.just_wired",
              JSON.stringify({ keyId: created.id, label: created.label, at: Date.now() }),
            );
          } catch {
            /* ignore */
          }
          router.push("/proxy");
        }}
      />
    </PageContainer>
  );
}
