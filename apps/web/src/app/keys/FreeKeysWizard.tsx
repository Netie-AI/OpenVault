"use client";

/**
 * FreeRoute Get free keys onboard wizard (#60).
 *
 * Reuses POST /api/keys, /api/vault/ingest-env, /api/vault/seed-essentials,
 * and /api/freeroute/onboard. Site passwords stay on /api/secrets*.
 * Retired inference APIs are not listed. Save never waits on CF /models 405.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { apiGet, isApiError } from "@/lib/api/client";
import {
  createKey,
  ingestEnv,
  listKeys,
  precheckKey,
  seedEssentials,
  type IngestEnvResult,
  type KeyRow,
} from "@/lib/api/keys";
import { createPassword } from "@/lib/api/secrets";
import { VaultSealBar } from "@/components/vault/VaultSealBar";
import { rememberRegisterIntent } from "@/lib/vault/registerIntent";
import {
  FREE_KEYS_ONBOARD,
  composeCloudflareWorkersAiBase,
  isProbeMismatchWarn,
  type FreeKeyOnboardRow,
} from "@/lib/vault/freeKeysOnboard";

const CARD = "rounded-2xl border border-border bg-card p-6";
const H2 = "text-lg font-semibold tracking-tight text-foreground";
const LEAD = "mt-1 text-sm text-muted-foreground";
const STEP_N =
  "flex size-7 shrink-0 items-center justify-center rounded-full bg-primary/10 text-sm font-semibold text-primary";

type OnboardResponse = {
  providers?: FreeKeyOnboardRow[];
  github_models?: string;
};

type Drafts = Record<string, { secret: string; accountId: string; msg: string; warn: string }>;

function emptyDraft(): { secret: string; accountId: string; msg: string; warn: string } {
  return { secret: "", accountId: "", msg: "", warn: "" };
}

export function FreeKeysWizard({ focusProvider = "" }: { focusProvider?: string }) {
  const [rows, setRows] = useState<FreeKeyOnboardRow[]>([...FREE_KEYS_ONBOARD]);
  const [keys, setKeys] = useState<KeyRow[]>([]);
  const [drafts, setDrafts] = useState<Drafts>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [sealed, setSealed] = useState(true);
  const [envText, setEnvText] = useState("");
  const [envPreview, setEnvPreview] = useState<IngestEnvResult | null>(null);
  const [envMsg, setEnvMsg] = useState("");
  const [seedMsg, setSeedMsg] = useState("");
  const [pwLabel, setPwLabel] = useState("");
  const [pwValue, setPwValue] = useState("");
  const [pwMsg, setPwMsg] = useState("");

  const refreshKeys = useCallback(async () => {
    try {
      setKeys(await listKeys());
    } catch {
      /* sealed or unreachable — checklist still works */
    }
  }, []);

  useEffect(() => {
    const ac = new AbortController();
    apiGet<OnboardResponse>("/api/freeroute/onboard", { signal: ac.signal })
      .then((data) => {
        if (data.providers?.length) setRows(data.providers);
      })
      .catch(() => undefined);
    void refreshKeys();
    return () => ac.abort();
  }, [refreshKeys]);

  const installed = useMemo(() => {
    const have = new Set<string>();
    for (const key of keys) {
      if (!key.enabled) continue;
      if (key.provider !== "custom") have.add(key.provider);
      if (
        (key.base_url || "").includes("api.cloudflare.com/client/v4/accounts/") &&
        (key.base_url || "").includes("/ai")
      ) {
        have.add("cloudflare");
      }
    }
    for (const row of rows) {
      if (row.installed) have.add(row.id);
    }
    return have;
  }, [keys, rows]);

  function patchDraft(id: string, patch: Partial<Drafts[string]>) {
    setDrafts((d) => ({ ...d, [id]: { ...emptyDraft(), ...d[id], ...patch } }));
  }

  async function installRow(row: FreeKeyOnboardRow) {
    const draft = drafts[row.id] ?? emptyDraft();
    const secret = draft.secret.trim();
    if (!secret) {
      patchDraft(row.id, { msg: "Paste the key you copied after register" });
      return;
    }
    let baseUrl = row.default_base_url;
    if (row.needs_account_id) {
      try {
        baseUrl = composeCloudflareWorkersAiBase(draft.accountId);
      } catch (err) {
        patchDraft(row.id, { msg: err instanceof Error ? err.message : "Need a Cloudflare Account ID" });
        return;
      }
    }
    setBusy(row.id);
    try {
      const created = await createKey({
        label: row.label,
        provider: row.add_key_provider,
        secret,
        role: "free",
        base_url: baseUrl,
      });
      patchDraft(row.id, { secret: "", msg: `Installed ${row.label} into the vault.` });
      await refreshKeys();
      // Fire-and-forget: 405 on CF /models is a warn, never un-saves the row.
      try {
        const probe = await precheckKey(created.id);
        const errText = probe.error || probe.detail || "";
        if (isProbeMismatchWarn(errText) || (probe.status === "ok" && errText.includes("405"))) {
          patchDraft(row.id, {
            warn: "Saved. GET /models returned 405 — probe mismatch, not a dead key.",
          });
        } else if (probe.status && probe.status !== "ok" && probe.status !== "unknown") {
          patchDraft(row.id, { warn: `Saved. Precheck: ${probe.status}${errText ? ` (${errText})` : ""}` });
        }
      } catch {
        patchDraft(row.id, { warn: "Saved. Precheck skipped (offline or sealed)." });
      }
    } catch (err) {
      patchDraft(row.id, { msg: isApiError(err) ? err.message : "Could not install that key" });
    } finally {
      setBusy(null);
    }
  }

  async function previewEnv() {
    setBusy("env-preview");
    setEnvMsg("");
    try {
      const preview = await ingestEnv(true, { envText });
      setEnvPreview(preview);
      setEnvMsg(
        preview.scanned
          ? `Dry run: ${preview.scanned} candidate(s). Nothing written.`
          : "Dry run: no importable keys in that paste.",
      );
    } catch (err) {
      setEnvMsg(isApiError(err) ? err.message : "Dry-run ingest failed");
    } finally {
      setBusy(null);
    }
  }

  async function importEnv() {
    setBusy("env-import");
    setEnvMsg("");
    try {
      const result = await ingestEnv(false, { envText });
      setEnvPreview(result);
      setEnvText("");
      await refreshKeys();
      setEnvMsg(
        `Imported ${result.imported ?? 0} key(s)` +
          (result.passwords_imported ? `, ${result.passwords_imported} password(s) to /api/secrets` : "") +
          ". Testing is separate and cannot un-save a Cloudflare 405.",
      );
    } catch (err) {
      setEnvMsg(isApiError(err) ? err.message : "Import failed");
    } finally {
      setBusy(null);
    }
  }

  async function seedLocal() {
    setBusy("seed");
    try {
      await seedEssentials();
      setSeedMsg("Local Ollama/Cortex placeholders seeded. No cloud keys invented.");
    } catch (err) {
      setSeedMsg(isApiError(err) ? err.message : "Seed failed");
    } finally {
      setBusy(null);
    }
  }

  async function saveSitePassword() {
    if (!pwLabel.trim() || !pwValue.trim()) {
      setPwMsg("Label and password required — this uses /api/secrets, not /api/keys.");
      return;
    }
    setBusy("password");
    try {
      await createPassword({ label: pwLabel.trim(), password: pwValue });
      setPwLabel("");
      setPwValue("");
      setPwMsg("Stored as a site password in /api/secrets. Not an API key.");
    } catch (err) {
      setPwMsg(isApiError(err) ? err.message : "Could not store that password");
    } finally {
      setBusy(null);
    }
  }

  return (
    <section id="keypath-free" data-testid="free-screen" className="grid gap-5 lg:grid-cols-2">
      <div className="space-y-5 lg:col-span-2">
        <VaultSealBar onStatus={(st) => setSealed(st.sealed)} />
      </div>

      <div className={CARD}>
        <h2 className={H2}>Get free keys</h2>
        <p className={LEAD}>Two steps. Register, then install. Groq first. One OpenVault.</p>
        <ol className="my-4 space-y-3">
          <li data-testid="free-step-1" className="flex gap-3">
            <span className={STEP_N}>1</span>
            <div>
              <p className="font-medium text-foreground">Register</p>
              <p className="text-sm text-muted-foreground">
                Create a free account and copy the key it shows you.
              </p>
            </div>
          </li>
          <li data-testid="free-step-2" className="flex gap-3">
            <span className={STEP_N}>2</span>
            <div>
              <p className="font-medium text-foreground">Install</p>
              <p className="text-sm text-muted-foreground">
                Paste that key here. OpenVault encrypts it in the vault.
              </p>
            </div>
          </li>
        </ol>
        <p className="text-xs text-muted-foreground">
          Deep-link signup is Groq-first. GitHub Models is retired and not listed. Site
          passwords never go to <code className="text-foreground">/api/keys</code> with an
          empty base_url — use <code className="text-foreground">/api/secrets*</code>.
        </p>
        {sealed ? (
          <p className="mt-3 text-sm text-warning">Unseal the vault to install keys.</p>
        ) : null}
      </div>

      <div className={CARD}>
        <h2 className={H2}>Checklist</h2>
        <p className={LEAD}>Paste-to-save. Auto base_url from the catalog. Role free.</p>
        <ol className="mt-4 space-y-4" data-testid="free-keys-checklist">
          {rows.map((row, index) => {
            const draft = drafts[row.id] ?? emptyDraft();
            const done = installed.has(row.id);
            const focused = focusProvider === row.id || (index === 0 && !focusProvider);
            return (
              <li
                key={row.id}
                data-onboard-id={row.id}
                className={
                  "rounded-xl border p-3 " +
                  (focused ? "border-primary bg-primary/5" : "border-border")
                }
              >
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div>
                    <p className="text-sm font-semibold text-foreground">
                      {index + 1}. {row.label}
                      {row.required_first ? (
                        <span className="ml-2 text-[11px] uppercase tracking-wide text-primary">
                          first
                        </span>
                      ) : null}
                    </p>
                    <p className="text-xs text-muted-foreground">{row.notes}</p>
                    {done ? (
                      <p className="mt-1 text-xs text-success">Installed in this vault</p>
                    ) : null}
                  </div>
                  <Button asChild variant="outline" size="sm">
                    <Link
                      href={`/tool/register?provider=${encodeURIComponent(row.id)}`}
                      onClick={() =>
                        rememberRegisterIntent({
                          providerId: row.add_key_provider,
                          providerName: row.label,
                          registerUrl: row.register_url,
                        })
                      }
                    >
                      Register
                    </Link>
                  </Button>
                </div>
                {row.needs_account_id ? (
                  <div className="mt-3 space-y-1.5">
                    <Label htmlFor={`cf-account-${row.id}`}>Cloudflare Account ID (not a secret)</Label>
                    <Input
                      id={`cf-account-${row.id}`}
                      autoComplete="off"
                      placeholder="32-character account id from the dashboard"
                      value={draft.accountId}
                      onChange={(e) => patchDraft(row.id, { accountId: e.target.value })}
                    />
                    <p className="font-mono text-[11px] text-muted-foreground">
                      {row.default_base_url}
                    </p>
                  </div>
                ) : null}
                <div className="mt-3 space-y-2">
                  <Label htmlFor={`free-secret-${row.id}`}>Paste key</Label>
                  <Input
                    id={`free-secret-${row.id}`}
                    type="password"
                    autoComplete="off"
                    placeholder="paste the key from signup"
                    value={draft.secret}
                    onChange={(e) => patchDraft(row.id, { secret: e.target.value })}
                  />
                  <Button
                    onClick={() => void installRow(row)}
                    disabled={busy === row.id || sealed}
                  >
                    {busy === row.id ? "Saving…" : "Install into vault"}
                  </Button>
                  {draft.msg ? <p className="text-xs text-muted-foreground">{draft.msg}</p> : null}
                  {draft.warn ? <p className="text-xs text-warning">{draft.warn}</p> : null}
                </div>
              </li>
            );
          })}
        </ol>
      </div>

      <div className={CARD}>
        <h2 className={H2}>Batch .env ingest</h2>
        <p className={LEAD}>
          Dry-run default. Uses existing <code className="text-foreground">/api/vault/ingest-env</code>.
          Known API keys go to the vault; SITE_* / passwords go to secrets.
        </p>
        <div className="mt-3 space-y-2">
          <Label htmlFor="env-ingest-text">Paste a .env or several KEY=value lines</Label>
          <Textarea
            id="env-ingest-text"
            rows={8}
            placeholder={"GROQ_API_KEY=\nGOOGLE_API_KEY=\nCLOUDFLARE_API_TOKEN=\nCLOUDFLARE_ACCOUNT_ID="}
            value={envText}
            onChange={(e) => setEnvText(e.target.value)}
          />
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" onClick={() => void previewEnv()} disabled={busy === "env-preview"}>
              Dry-run scan
            </Button>
            <Button onClick={() => void importEnv()} disabled={busy === "env-import" || sealed}>
              {busy === "env-import" ? "Importing…" : "Import into vault"}
            </Button>
          </div>
          {envMsg ? <p className="text-xs text-muted-foreground">{envMsg}</p> : null}
          {envPreview?.results?.length ? (
            <ul className="mt-2 space-y-1 text-xs text-muted-foreground">
              {envPreview.results.map((row) => (
                <li key={`${row.env_key}-${row.action}`}>
                  {row.env_key} → {row.store || "keys"} · {row.action}
                  {row.masked ? ` · ${row.masked}` : ""}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
        <div className="mt-5 border-t border-border pt-4">
          <p className="text-sm font-medium text-foreground">Local slots</p>
          <p className="mt-1 text-xs text-muted-foreground">
            seed-essentials only creates Ollama/Cortex placeholders. It does not invent
            cloud keys or ov_ tokens.
          </p>
          <Button
            className="mt-2"
            variant="outline"
            size="sm"
            onClick={() => void seedLocal()}
            disabled={busy === "seed" || sealed}
          >
            Seed local placeholders
          </Button>
          {seedMsg ? <p className="mt-2 text-xs text-muted-foreground">{seedMsg}</p> : null}
        </div>
      </div>

      <div className={CARD}>
        <h2 className={H2}>Site password</h2>
        <p className={LEAD}>
          Not an API key. Stored via <code className="text-foreground">POST /api/secrets/passwords</code>.
          Never as a custom key with an empty base_url.
        </p>
        <div className="mt-3 space-y-2">
          <Label htmlFor="site-pw-label">Label</Label>
          <Input
            id="site-pw-label"
            value={pwLabel}
            onChange={(e) => setPwLabel(e.target.value)}
            placeholder="console.groq.com login"
          />
          <Label htmlFor="site-pw-value">Password</Label>
          <Input
            id="site-pw-value"
            type="password"
            autoComplete="off"
            value={pwValue}
            onChange={(e) => setPwValue(e.target.value)}
          />
          <Button variant="outline" onClick={() => void saveSitePassword()} disabled={sealed}>
            Save password
          </Button>
          {pwMsg ? <p className="text-xs text-muted-foreground">{pwMsg}</p> : null}
          <Button asChild variant="ghost" size="sm">
            <Link href="/vault">Open operator vault / secrets</Link>
          </Button>
        </div>
      </div>
    </section>
  );
}
