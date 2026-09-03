"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { FolderOpen, Eye, Rocket, Upload } from "lucide-react";
import {
  LONG_TIMEOUT_MS,
  apiFetch,
  apiGet,
  apiPost,
  isApiError,
  ovUrl,
} from "@/lib/api/client";
import { listKeys, type KeyRow } from "@/lib/api/keys";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PageContainer } from "@/components/ui/PageContainer";
import { PageHeader } from "@/components/ui/PageHeader";
import { cn } from "@/lib/utils";

type Stack = {
  primary?: string;
  framework?: string;
  category?: string;
  package_manager?: string;
  install_command?: string;
  build_command?: string;
  start_command?: string;
  root_directory?: string;
  output_directory?: string;
  confidence?: number;
};

type Recommend = {
  target?: string;
  reason?: string;
  real_publish?: boolean;
};

type Preflight = {
  ready?: boolean;
  blocker?: string;
  facts?: Record<string, string>;
  real_publish?: boolean;
};

type TargetCard = {
  id: string;
  title: string;
  blurb: string;
  instant_host?: boolean;
  estimated_min_usd?: number;
  sponsored?: boolean;
};

type GitHubStatus = {
  connected?: boolean;
  mode?: string;
  login?: string | null;
  scopes?: string[];
  detail?: string;
};

const TARGETS_DEFAULT: TargetCard[] = [];

export default function ShipPage() {
  const router = useRouter();
  const [path, setPath] = useState("");
  const [hostname, setHostname] = useState("");
  // Only the vps_ssh target uses this — the box we manage on the user's behalf.
  const [vpsHost, setVpsHost] = useState("");
  const [stack, setStack] = useState<Stack | null>(null);
  const [recommend, setRecommend] = useState<Recommend | null>(null);
  const [target, setTarget] = useState("spaceship_ftp");
  const [targets, setTargets] = useState<TargetCard[]>(TARGETS_DEFAULT);
  const [preflight, setPreflight] = useState<Preflight | null>(null);
  const [deployOut, setDeployOut] = useState<Record<string, unknown> | null>(null);
  const [previewUrl, setPreviewUrl] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [phase, setPhase] = useState("");
  const [gh, setGh] = useState<GitHubStatus | null>(null);
  const [ghBusy, setGhBusy] = useState(false);
  const [ghNotice, setGhNotice] = useState("");
  const [pat, setPat] = useState("");
  const [vaultKeys, setVaultKeys] = useState<KeyRow[]>([]);
  const [injectIds, setInjectIds] = useState<Record<string, boolean>>({});
  const [ht1, setHt1] = useState<{
    live_public_url: string | null;
    human_test_gate?: string;
    host_adapters?: string[];
    vercel_job?: { note?: string; preview?: string; git_push?: string; env?: string; dotenv_upload?: string };
  } | null>(null);

  const loadTargets = useCallback(async () => {
    try {
      const data = await apiGet<{
        targets?: TargetCard[];
        live_public_url?: string | null;
        human_test_gate?: string;
        host_adapters?: string[];
        vercel_job?: { note?: string; preview?: string; git_push?: string; env?: string; dotenv_upload?: string };
      }>("/api/ship/targets");
      setTargets(data.targets || []);
      setHt1({
        live_public_url: data.live_public_url ?? null,
        human_test_gate: data.human_test_gate,
        host_adapters: data.host_adapters,
        vercel_job: data.vercel_job,
      });
    } catch {
      /* targets optional at paint */
    }
  }, []);

  const loadGitHub = useCallback(async () => {
    try {
      const data = await apiGet<GitHubStatus>("/api/ship/github/status");
      setGh(data);
    } catch {
      setGh(null);
    }
  }, []);

  useEffect(() => {
    void loadTargets();
    void loadGitHub();
    void listKeys()
      .then(setVaultKeys)
      .catch(() => setVaultKeys([]));
  }, [loadTargets, loadGitHub]);

  async function connectGhCli() {
    setGhBusy(true);
    setGhNotice("");
    try {
      const out = await apiPost<GitHubStatus & { ok?: boolean; detail?: string }>(
        "/api/ship/github/connect",
        {},
        { timeoutMs: LONG_TIMEOUT_MS },
      );
      setGh(out);
      setGhNotice(out.detail || (out.connected ? "GitHub connected via gh CLI" : "Connect finished"));
      await loadGitHub();
    } catch (e) {
      setGhNotice(isApiError(e) ? e.message : String(e));
    } finally {
      setGhBusy(false);
    }
  }

  async function saveGhPat() {
    if (!pat.trim()) {
      setGhNotice("Paste a GitHub PAT first");
      return;
    }
    setGhBusy(true);
    setGhNotice("");
    try {
      const out = await apiPost<GitHubStatus>("/api/ship/github/pat", {
        token: pat.trim(),
        note: "openvault-ui",
      });
      setPat("");
      setGh(out);
      setGhNotice(out.connected ? `Connected as ${out.login || "user"}` : out.detail || "PAT saved");
      await loadGitHub();
    } catch (e) {
      setGhNotice(isApiError(e) ? e.message : String(e));
    } finally {
      setGhBusy(false);
    }
  }

  async function clearGhPat() {
    setGhBusy(true);
    setGhNotice("");
    try {
      const out = await apiFetch<GitHubStatus>("/api/ship/github/pat", {
        method: "DELETE",
      });
      setGh(out);
      setGhNotice("GitHub PAT cleared");
    } catch (e) {
      setGhNotice(isApiError(e) ? e.message : String(e));
    } finally {
      setGhBusy(false);
    }
  }

  async function writeWorkflow() {
    if (!path.trim()) {
      setErr("Project path required to write the GitHub Actions workflow");
      return;
    }
    setBusy(true);
    setErr("");
    setGhNotice("");
    try {
      const out = await apiPost<{ detail?: string; public_url?: string | null }>(
        "/api/ship/cicd/write",
        { project_path: path.trim() },
      );
      if (out.public_url) {
        setErr("Refuse: workflow write must not return a live URL");
        return;
      }
      setGhNotice(out.detail || "Workflow written. No live URL.");
    } catch (e) {
      setErr(isApiError(e) ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function previewLocal() {
    if (!path.trim()) {
      setErr("Choose a folder or drop a zip first.");
      return;
    }
    setBusy(true);
    setErr("");
    setPreviewUrl("");
    setPhase("Previewing…");
    try {
      const out = await apiPost<{
        ok?: boolean;
        local_preview_url?: string;
        public_url?: string | null;
        live?: boolean;
        blocker?: string;
      }>("/api/ship/preview", { project_path: path.trim() });
      if (out.public_url || out.live) {
        setErr("Refuse: local preview must not return a live URL");
        return;
      }
      if (!out.ok) {
        setErr(out.blocker || "No previewable artifact. Not a live URL.");
        return;
      }
      setPreviewUrl(out.local_preview_url || "");
    } catch (e) {
      setErr(isApiError(e) ? e.message : String(e));
    } finally {
      setBusy(false);
      setPhase("");
    }
  }

  async function afterPath(projectPath: string, detected?: Stack) {
    setPath(projectPath);
    setErr("");
    setDeployOut(null);
    setBusy(true);
    setPhase("Detecting…");
    try {
      const st =
        detected ||
        (await apiPost<Stack>(
          "/api/detect",
          { project_path: projectPath },
          { timeoutMs: 60_000 },
        ));
      setStack(st);
      setPhase("Picking target…");
      const rec = await apiPost<Recommend>("/api/ship/recommend", {
        project_path: projectPath,
        stack: st,
      });
      setRecommend(rec);
      const nextTarget = rec.target || "spaceship_ftp";
      setTarget(nextTarget);
      setPhase("Preflight…");
      const pre = await apiPost<Preflight>("/api/ship/preflight", {
        target: nextTarget,
      });
      setPreflight(pre);
      setPhase("");
    } catch (e) {
      setStack(null);
      setRecommend(null);
      setPreflight(null);
      setErr(isApiError(e) ? e.message : String(e));
      setPhase("");
    } finally {
      setBusy(false);
    }
  }

  async function pickFolder() {
    setBusy(true);
    setErr("");
    setPhase("Opening folder…");
    try {
      const data = await apiPost<{ ok?: boolean; path?: string }>(
        "/api/ship/pick-folder",
        undefined,
        { timeoutMs: LONG_TIMEOUT_MS },
      );
      if (data.ok && data.path) {
        await afterPath(data.path);
        return;
      }
      setErr("Folder picker cancelled — paste a path or drop a zip.");
    } catch (e) {
      setErr(isApiError(e) ? e.message : String(e));
    } finally {
      setBusy(false);
      setPhase("");
    }
  }

  async function onZip(file: File) {
    setBusy(true);
    setErr("");
    setPhase("Uploading…");
    try {
      const session = await apiPost<{ session_id: string; staging_dir?: string }>(
        "/api/ship/library/upload-session",
      );
      const form = new FormData();
      form.append("file", file, file.name);
      const res = await fetch(
        ovUrl(`/api/ship/library/upload-session/${session.session_id}/files`),
        { method: "POST", body: form, cache: "no-store" },
      );
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(
          typeof body?.detail === "string" ? body.detail : `Upload HTTP ${res.status}`,
        );
      }
      const scan = await apiPost<{
        ok?: boolean;
        staging_dir?: string;
        stack?: Stack;
        error?: string;
      }>(`/api/ship/library/upload-session/${session.session_id}/scan`);
      if (!scan.ok || !scan.staging_dir) {
        throw new Error(scan.error || "Scan failed");
      }
      await afterPath(scan.staging_dir, scan.stack);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setPhase("");
    } finally {
      setBusy(false);
    }
  }

  async function changeTarget(next: string) {
    setTarget(next);
    setDeployOut(null);
    setBusy(true);
    setPhase("Preflight…");
    try {
      const pre = await apiPost<Preflight>("/api/ship/preflight", {
        target: next,
        vps_host: vpsHost.trim(),
      });
      setPreflight(pre);
    } catch (e) {
      setPreflight(null);
      setErr(isApiError(e) ? e.message : String(e));
    } finally {
      setBusy(false);
      setPhase("");
    }
  }

  async function deploy() {
    if (!path.trim()) {
      setErr("Choose a folder or drop a zip first.");
      return;
    }
    if (preflight && preflight.ready === false) {
      setErr(preflight.blocker || "Preflight failed — fix the blocker before Deploy.");
      return;
    }
    setBusy(true);
    setErr("");
    setPhase("Deploying…");
    try {
      const out = await apiPost<Record<string, unknown>>(
        "/api/ship/engine",
        {
          target,
          project_path: path.trim(),
          hostname: hostname.trim(),
          vps_host: vpsHost.trim(),
          // The engine asks the adapter whether this machine has to build.
          // This used to be `target === "cloudflare_pages"`, which left Netlify
          // -- which also uploads a built directory -- permanently unable to
          // publish from here.
          run_build: false,
          secrets: vaultKeys
            .filter((k) => injectIds[k.id])
            .map((k) => ({
              env_name: `${k.provider.toUpperCase()}_API_KEY`,
              key_id: k.id,
            })),
        },
        { timeoutMs: LONG_TIMEOUT_MS },
      );
      setDeployOut(out);
      if (out.ok === false) {
        setErr(String(out.error || "Deploy refused"));
      }
      const dep = out.deployment as { deployment_id?: string } | undefined;
      const depId =
        (typeof dep?.deployment_id === "string" && dep.deployment_id) ||
        (typeof out.deployment_id === "string" ? out.deployment_id : "");
      if (depId) {
        router.push(`/ship/deploy/${encodeURIComponent(depId)}`);
      }
    } catch (e) {
      setErr(isApiError(e) ? e.message : String(e));
    } finally {
      setBusy(false);
      setPhase("");
    }
  }

  const ready = Boolean(preflight?.ready);
  const publicUrl =
    deployOut && typeof deployOut === "object"
      ? String(
          (deployOut as { public_url?: string; url?: string }).public_url ||
            (deployOut as { url?: string }).url ||
            "",
        )
      : "";

  return (
    <PageContainer>
      <PageHeader
        title="Ship"
        description="Your machine builds. Your cloud account hosts. Your domain points at it. One Deploy — we detect, pick the target, and preflight first."
      />

      {ht1 ? (
        <div
          data-glass
          className="mb-5 rounded-2xl border border-border bg-card p-4 text-sm"
        >
          <p className="font-medium text-foreground">
            {ht1.host_adapters?.length ?? 0} host adapters. Zero live URL (HT1).
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            {ht1.host_adapters?.join(", ") || "spaceship_ftp, cloudflare_pages, coolify, netlify, vps_ssh"}.
            Spaceship FTP reuses the host you already pay for. Simulate never invents a production URL. Loopback preview of a built artifact is not live. .env files stay off
            the public upload; vault env needs a non-public FTP dir. A real openable host URL is HT1 and
            is not agent-closeable. This is not Vercel
            {ht1.vercel_job?.git_push ? ` (git-push: ${ht1.vercel_job.git_push})` : ""}.
          </p>
        </div>
      ) : null}

      <div
        data-glass
        className="mb-5 rounded-2xl border border-border bg-card p-5"
      >
        <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="text-sm font-semibold text-foreground">GitHub</p>
            <p className="mt-0.5 text-xs text-muted-foreground">
              Connect via <code className="text-foreground">gh</code> CLI or a PAT so Ship can list repos and push workflows.
            </p>
          </div>
          <Button
            variant="outline"
            size="sm"
            disabled={ghBusy}
            onClick={() => void loadGitHub()}
          >
            Refresh
          </Button>
        </div>
        <p className="mb-3 text-sm text-foreground">
          {gh?.connected
            ? `Connected (${gh.mode || "unknown"})${gh.login ? ` as ${gh.login}` : ""}`
            : "Not connected"}
          {gh?.detail ? (
            <span className="text-muted-foreground"> — {gh.detail}</span>
          ) : null}
        </p>
        <div className="flex flex-wrap items-end gap-2">
          <Button disabled={ghBusy} onClick={() => void connectGhCli()}>
            {ghBusy ? "Working…" : "Connect with gh CLI"}
          </Button>
          <div className="flex min-w-[220px] flex-1 flex-col gap-1">
            <Label htmlFor="gh-pat">Or paste PAT</Label>
            <Input
              id="gh-pat"
              type="password"
              value={pat}
              onChange={(e) => setPat(e.target.value)}
              placeholder="ghp_…"
              disabled={ghBusy}
            />
          </div>
          <Button variant="outline" disabled={ghBusy} onClick={() => void saveGhPat()}>
            Save PAT
          </Button>
          {gh?.connected ? (
            <Button variant="ghost" disabled={ghBusy} onClick={() => void clearGhPat()}>
              Disconnect
            </Button>
          ) : null}
        </div>
        {ghNotice ? (
          <p className="mt-3 text-sm text-muted-foreground">{ghNotice}</p>
        ) : null}
      </div>

      <div data-glass className="rounded-2xl border border-border bg-card p-5">
        <div className="mb-4 flex flex-wrap gap-2">
          <Button onClick={() => void pickFolder()} disabled={busy}>
            <FolderOpen className="size-4" />
            Choose folder
          </Button>
          <label
            className={cn(
              "inline-flex h-9 cursor-pointer items-center gap-2 rounded-md border border-input bg-background px-3 text-sm",
              busy && "pointer-events-none opacity-50",
            )}
          >
            <Upload className="size-4" />
            Drop zip
            <input
              type="file"
              accept=".zip,application/zip"
              className="hidden"
              disabled={busy}
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) void onZip(f);
                e.target.value = "";
              }}
            />
          </label>
          <Button
            disabled={busy || !path.trim() || preflight?.ready === false}
            onClick={() => void deploy()}
          >
            <Rocket className="size-4" />
            {phase || "Deploy"}
          </Button>
          <Button
            variant="outline"
            disabled={busy || !path.trim()}
            onClick={() => void writeWorkflow()}
          >
            Write git-push workflow
          </Button>
          <Button
            variant="outline"
            disabled={busy || !path.trim()}
            onClick={() => void previewLocal()}
          >
            <Eye className="size-4" />
            Preview locally
          </Button>
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-2">
            <Label htmlFor="ship-path">Project path</Label>
            <Input
              id="ship-path"
              value={path}
              onChange={(e) => setPath(e.target.value)}
              onBlur={() => {
                if (path.trim()) void afterPath(path.trim());
              }}
              placeholder="D:\path\to\app"
              disabled={busy}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="ship-host">Custom domain (optional)</Label>
            <Input
              id="ship-host"
              value={hostname}
              onChange={(e) => setHostname(e.target.value)}
              placeholder="app.example.com"
              disabled={busy}
            />
          </div>
        </div>

        {target === "vps_ssh" ? (
          <div className="mt-4 space-y-2">
            <Label htmlFor="ship-vps">Your server (IP or hostname)</Label>
            <Input
              id="ship-vps"
              value={vpsHost}
              onChange={(e) => setVpsHost(e.target.value)}
              onBlur={() => {
                if (vpsHost.trim()) void changeTarget("vps_ssh");
              }}
              placeholder="203.0.113.10"
              disabled={busy}
            />
            <p className="text-xs text-muted-foreground">
              SSH key auth as root (or a sudo user). We install Docker and Caddy, build
              your app there, run replicas behind a load balancer, and get the
              certificate once your domain points at this address.
            </p>
          </div>
        ) : null}

        {recommend ? (
          <div className="mt-5 rounded-xl border border-border bg-background/50 p-4">
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Auto-picked target
            </p>
            <p className="mt-1 text-sm font-medium text-foreground">
              {recommend.target}
              {recommend.real_publish ? " · real publish" : " · plan / simulate"}
            </p>
            <p className="mt-1 text-sm text-muted-foreground">{recommend.reason}</p>
            <div className="mt-3 flex flex-wrap gap-2">
              {(targets.length ? targets : [{ id: "spaceship_ftp", title: "Spaceship FTP (existing host)", blurb: "" }]).map(
                (t) => (
                  <button
                    key={t.id}
                    type="button"
                    disabled={busy}
                    onClick={() => void changeTarget(t.id)}
                    className={cn(
                      "rounded-full border px-3 py-1 text-xs",
                      target === t.id
                        ? "border-primary bg-primary/10 text-foreground"
                        : "border-border text-muted-foreground hover:bg-muted",
                    )}
                    title={t.blurb}
                  >
                    {t.title || t.id}
                    {t.sponsored ? " · Sponsored" : ""}
                  </button>
                ),
              )}
            </div>
          </div>
        ) : null}

        {preflight ? (
          <div
            className={cn(
              "mt-4 rounded-xl border p-4 text-sm",
              ready
                ? "border-success-border bg-success-bg/40 text-foreground"
                : "border-warning-border bg-warning-bg/40 text-foreground",
            )}
          >
            <p className="font-medium">{ready ? "Preflight OK" : "Preflight blocked"}</p>
            {!ready && preflight.blocker ? (
              <p className="mt-1 text-muted-foreground">{preflight.blocker}</p>
            ) : null}
            {preflight.facts && Object.keys(preflight.facts).length > 0 ? (
              <p className="mt-1 font-mono text-xs text-muted-foreground">
                {Object.entries(preflight.facts)
                  .map(([k, v]) => `${k}=${v}`)
                  .join(" · ")}
              </p>
            ) : null}
          </div>
        ) : null}

        {vaultKeys.length > 0 ? (
          <div className="mt-4 rounded-xl border border-border p-4 text-sm">
            <p className="font-medium">Inject vaulted keys</p>
            <p className="mt-1 text-xs text-muted-foreground">
              Names only in the UI. Values go to the host, never into logs.
              Payment cards cannot be injected.
            </p>
            <ul className="mt-3 space-y-2">
              {vaultKeys
                .filter((k) => k.enabled !== false)
                .slice(0, 12)
                .map((k) => (
                  <li key={k.id} className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      id={`inject-${k.id}`}
                      checked={Boolean(injectIds[k.id])}
                      onChange={(e) =>
                        setInjectIds((prev) => ({ ...prev, [k.id]: e.target.checked }))
                      }
                    />
                    <label htmlFor={`inject-${k.id}`} className="text-xs">
                      {k.label || k.provider} → {k.provider.toUpperCase()}_API_KEY
                    </label>
                  </li>
                ))}
            </ul>
          </div>
        ) : null}

        {err ? <p className="mt-4 text-sm text-destructive">{err}</p> : null}

        {stack ? (
          <dl className="mt-5 grid gap-2 text-sm sm:grid-cols-2">
            <div>
              <dt className="text-muted-foreground">Stack</dt>
              <dd className="font-medium">{stack.framework || stack.primary || "—"}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Category</dt>
              <dd className="font-medium">{stack.category || "—"}</dd>
            </div>
            <div className="sm:col-span-2">
              <dt className="text-muted-foreground">Build</dt>
              <dd className="font-mono text-xs">{stack.build_command || "—"}</dd>
            </div>
          </dl>
        ) : (
          <p className="mt-4 text-sm text-muted-foreground">
            Choose a folder or drop a zip — we detect the stack, prefer Spaceship
            FTP when you already pay for that host, and refuse Deploy until
            preflight passes. No invented preview URL.
          </p>
        )}

        {previewUrl ? (
          <p className="mt-4 text-sm">
            Local preview (not live, not HT1):{" "}
            <a className="text-primary underline" href={previewUrl} target="_blank" rel="noreferrer">
              {previewUrl}
            </a>
          </p>
        ) : null}

        {publicUrl ? (
          <p className="mt-4 text-sm">
            Live:{" "}
            <a className="text-primary underline" href={publicUrl} target="_blank" rel="noreferrer">
              {publicUrl}
            </a>
          </p>
        ) : null}

        {deployOut ? (
          <pre className="mt-4 max-h-48 overflow-auto rounded-lg bg-background/80 p-3 font-mono text-[11px] text-muted-foreground">
            {JSON.stringify(deployOut, null, 2)}
          </pre>
        ) : null}
      </div>
    </PageContainer>
  );
}
