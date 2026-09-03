"use client";

/**
 * Keys — the friendly key UI. Ported from the retired OpenMW/webui Keys tab.
 *
 * Product lock:
 *  1. Subscribe shows a Cortex API key only. The issued `ov_` token is framed
 *     as a Cortex key; no hop vendor or fake vendor string on that screen.
 *  2. Bring your key shows the provider name the user pasted (honest labels).
 *  3. Free keys are two short steps: Register, then Install.
 *  4. Operator hop status stays hop-honest (R-0011) — that is /vault, not here.
 *
 * The subscribe section is literal copy on purpose. It is kept in lockstep with
 * src/keys/copy.ts and OpenMW/openmw/openvault/vault/key_ui_copy.py, and
 * OpenMW/tests/test_key_ui.py reads this file's source to lock it.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PageContainer } from "@/components/ui/PageContainer";
import { PageHeader } from "@/components/ui/PageHeader";
import { Tabs, type TabDef } from "@/components/ui/Tabs";
import { apiPost, isApiError } from "@/lib/api/client";
import { createKey, listFreeProviders, type ProviderSpec } from "@/lib/api/keys";
import { guessByokProvider, honestByokLabel } from "@/keys/byok";

type KeyPath = "subscribe" | "byok" | "free" | "operator";

const PATHS: readonly KeyPath[] = ["subscribe", "byok", "free", "operator"];

const TABS: TabDef<KeyPath>[] = [
  { key: "subscribe", label: "Subscribe" },
  { key: "byok", label: "Bring your key" },
  { key: "free", label: "Free keys" },
  { key: "operator", label: "Operator" },
];

/** `#keys` and `#vault` are the old webui deep links; both land on Subscribe. */
function pathFromHash(hash: string): KeyPath {
  const name = hash.replace(/^#/, "");
  return (PATHS as readonly string[]).includes(name) ? (name as KeyPath) : "subscribe";
}

/** Catalog id for a provider name the user typed. Unknown names stay "custom". */
const PROVIDER_ID_BY_NAME: Record<string, string> = {
  "cortex api key": "cortex",
  cortex: "cortex",
  anthropic: "anthropic",
  openrouter: "openrouter",
  groq: "groq",
  "hugging face": "huggingface",
  huggingface: "huggingface",
  "google ai studio": "google",
  google: "google",
  cerebras: "cerebras",
  fireworks: "fireworks",
  "together ai": "together",
  together: "together",
  "github models": "github_models",
  openai: "openai",
  ollama: "ollama",
};

const FALLBACK_PROVIDER = "custom";

function providerIdFromName(name: string): string {
  return PROVIDER_ID_BY_NAME[name.trim().toLowerCase()] ?? FALLBACK_PROVIDER;
}

const CARD = "rounded-2xl border border-border bg-card p-6";
const H2 = "text-lg font-semibold tracking-tight text-foreground";
const LEAD = "mt-1 text-sm text-muted-foreground";
const STEP_N =
  "flex size-7 shrink-0 items-center justify-center rounded-full bg-primary/10 text-sm font-semibold text-primary";

export default function KeysPage() {
  const [path, setPath] = useState<KeyPath>("subscribe");
  const [busy, setBusy] = useState<"issue" | "byok" | null>(null);

  const [issued, setIssued] = useState("");
  const [subscribeMsg, setSubscribeMsg] = useState("");

  const [byokSecret, setByokSecret] = useState("");
  const [byokName, setByokName] = useState("");
  const [byokMsg, setByokMsg] = useState("");

  const [catalog, setCatalog] = useState<ProviderSpec[]>([]);

  useEffect(() => {
    setPath(pathFromHash(window.location.hash));
  }, []);

  useEffect(() => {
    const ac = new AbortController();
    listFreeProviders(ac.signal)
      .then(setCatalog)
      .catch(() => undefined);
    return () => ac.abort();
  }, []);

  const activate = useCallback((next: KeyPath) => {
    setPath(next);
    window.history.replaceState(null, "", `#${next}`);
  }, []);

  async function issueCortex() {
    setBusy("issue");
    try {
      const res = await apiPost<{ token?: string }>("/api/keys/cortex");
      setIssued(res.token ?? "");
      setSubscribeMsg("Issued a Cortex API key. Copy it now.");
    } catch (err) {
      setSubscribeMsg(isApiError(err) ? err.message : "Could not issue a Cortex API key");
    } finally {
      setBusy(null);
    }
  }

  async function copyCortex() {
    if (!issued) return;
    try {
      await navigator.clipboard.writeText(issued);
      setSubscribeMsg("Copied Cortex API key");
    } catch {
      setSubscribeMsg("Could not copy. Select the key and copy it by hand.");
    }
  }

  function onSecretChange(value: string) {
    setByokSecret(value);
    const guess = guessByokProvider(value);
    if (guess.displayName && !byokName) setByokName(guess.displayName);
  }

  const honestLabel = useMemo(
    () => honestByokLabel(byokName, byokSecret),
    [byokName, byokSecret],
  );

  async function storeByok() {
    const secret = byokSecret.trim();
    if (!secret) {
      setByokMsg("Paste a key first");
      return;
    }
    const typed = byokName.trim();
    const guess = guessByokProvider(secret);
    const label = typed || guess.displayName || "Brought key";
    const provider = typed ? providerIdFromName(typed) : (guess.providerId ?? FALLBACK_PROVIDER);
    setBusy("byok");
    try {
      await createKey({ label, provider, secret, role: "backup" });
      setByokMsg(`Stored as ${label}`);
      setByokSecret("");
    } catch (err) {
      setByokMsg(isApiError(err) ? err.message : "Could not store that key");
    } finally {
      setBusy(null);
    }
  }

  return (
    <PageContainer>
      <PageHeader
        title="Keys"
        description="Cortex API key · bring your own · free register + install · one vault"
      />

      <Tabs tabs={TABS} value={path} onChange={activate} className="mb-6" />

      {path === "subscribe" && (
        <section id="keypath-subscribe" data-testid="subscribe-screen" className="max-w-2xl">
          <div className={CARD}>
            <h2 className={H2}>Your Cortex API key</h2>
            <p className={LEAD}>
              Get a Cortex API key. Use it with Cortex. OpenVault stores it -- one vault, one key.
            </p>
            {issued ? (
              <div className="my-4 rounded-xl border border-dashed border-border p-4">
                <p className="text-sm font-medium text-foreground">Cortex API key</p>
                <code className="mt-1.5 block break-all text-xs text-foreground">{issued}</code>
                <p className="mt-2 text-xs text-muted-foreground">
                  Copy this Cortex API key now. OpenVault will not show the full value again.
                </p>
              </div>
            ) : (
              <p className="my-4 text-sm text-muted-foreground">
                No Cortex API key yet. Tap the button to issue one.
              </p>
            )}
            <div className="flex flex-wrap gap-2">
              <Button onClick={issueCortex} disabled={busy === "issue"}>
                Get Cortex API key
              </Button>
              <Button variant="outline" onClick={copyCortex} disabled={!issued}>
                Copy
              </Button>
            </div>
            {subscribeMsg && <p className="mt-3 text-xs text-muted-foreground">{subscribeMsg}</p>}
            <p className="mt-4 text-sm text-muted-foreground" data-testid="subscribe-disclosure">
              Safety: Cortex uses this key. OpenVault keeps it in one vault. Do not share it. Ship
              and leave-machine still go through the gate. Powered by top-tier AI.
            </p>
          </div>
        </section>
      )}

      {path === "byok" && (
        <section id="keypath-byok" data-testid="byok-screen" className="max-w-2xl">
          <div className={`${CARD} space-y-4`}>
            <div>
              <h2 className={H2}>Bring your own key</h2>
              <p className={LEAD}>
                Paste a key you already have. We show the provider name you brought -- honest
                labels, not a guessed brand.
              </p>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="byokSecret">Paste your key</Label>
              <Input
                id="byokSecret"
                type="password"
                autoComplete="off"
                placeholder="the key you copied"
                value={byokSecret}
                onChange={(e) => onSecretChange(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="byokProviderName">Provider name you brought</Label>
              <Input
                id="byokProviderName"
                placeholder="shown as you typed or from the key prefix"
                value={byokName}
                onChange={(e) => setByokName(e.target.value)}
              />
            </div>
            {byokSecret.trim() && (
              <p className="text-sm text-muted-foreground" data-testid="byok-honest-label">
                Stored as{" "}
                <strong className="text-foreground">
                  {honestLabel || "Unknown provider -- type the name you got this key from"}
                </strong>
              </p>
            )}
            <div className="flex flex-wrap gap-2">
              <Button onClick={storeByok} disabled={busy === "byok"}>
                Store this key
              </Button>
            </div>
            {byokMsg && <p className="text-xs text-muted-foreground">{byokMsg}</p>}
          </div>
        </section>
      )}

      {path === "free" && (
        <section id="keypath-free" data-testid="free-screen" className="grid gap-5 lg:grid-cols-2">
          <div className={CARD}>
            <h2 className={H2}>Free keys</h2>
            <p className={LEAD}>Two steps. Register, then install.</p>
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
            <p className="text-sm text-muted-foreground">
              Want the easy path? Issue a Cortex API key on Subscribe -- no extra signup.
            </p>
            <div className="mt-4 flex flex-wrap gap-2">
              <Button onClick={() => activate("subscribe")}>Get Cortex API key</Button>
              <Button variant="outline" onClick={() => activate("byok")}>
                I have a key to paste
              </Button>
            </div>
          </div>
          <div className={CARD}>
            <h2 className={H2}>Register catalog</h2>
            {catalog.length === 0 ? (
              <p className={LEAD}>No free providers listed by the vault yet.</p>
            ) : (
              <ul className="mt-3 space-y-2">
                {catalog.map((p) => (
                  <li
                    key={p.id}
                    className="flex items-center justify-between gap-3 rounded-xl border border-border px-3 py-2"
                  >
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium text-foreground">{p.name}</p>
                      {p.free_notes && (
                        <p className="truncate text-xs text-muted-foreground">{p.free_notes}</p>
                      )}
                    </div>
                    {p.register_url && (
                      <Button asChild variant="outline" size="sm">
                        <a href={p.register_url} target="_blank" rel="noreferrer noopener">
                          Register
                        </a>
                      </Button>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </section>
      )}

      {path === "operator" && (
        <section id="keypath-operator" data-testid="operator-screen" className="max-w-2xl">
          <div className={CARD}>
            <h2 className={H2}>Operator vault</h2>
            <p className={LEAD}>
              Operator vault -- hop-honest status may name upstreams (R-0011). Not the subscribe
              screen.
            </p>
            <p className="mt-3 text-sm text-muted-foreground">
              Stored keys, the fallback chain, precheck and reveal live on the Vault page.
            </p>
            <div className="mt-4">
              <Button asChild>
                <Link href="/vault">Open the operator vault</Link>
              </Button>
            </div>
          </div>
        </section>
      )}
    </PageContainer>
  );
}
