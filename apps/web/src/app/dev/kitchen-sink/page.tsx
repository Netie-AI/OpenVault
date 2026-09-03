"use client";

/**
 * Lane B exit gate: every primitive this lane ships, in every state that can
 * break independently (default / hover / disabled / error / open).
 *
 * If a copy is broken — a missing token, a dead import, an unstyled control —
 * it is visible here in one scroll instead of two weeks later in a page nobody
 * thought to check. Dev-only surface; not linked from the app nav.
 */

import { useCallback, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import type { Terminal } from "@xterm/xterm";
import {
  Copy,
  Download,
  Ellipsis,
  KeyRound,
  Rocket,
  Settings,
  ShieldCheck,
  Trash2,
  Zap,
} from "lucide-react";

import { ThemeProvider, ThemeScript, THEMES, useTheme } from "@/components/theme-provider";
import { PageContainer } from "@/components/ui/PageContainer";
import { PageHeader } from "@/components/ui/PageHeader";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { Checkbox } from "@/components/ui/Checkbox";
import { Switch } from "@/components/ui/Switch";
import { Tabs } from "@/components/ui/Tabs";
import { SlidingToggle } from "@/components/ui/SlidingToggle";
import { CustomSelect } from "@/components/ui/CustomSelect";
import DropdownMenu from "@/components/ui/DropdownMenu";
import { DismissiblePopover } from "@/components/ui/Popover";
import { Modal } from "@/components/ui/Modal";
import { WarningCallout } from "@/components/ui/WarningCallout";
import OTPInput from "@/components/ui/OTPInput";
import { ToastProvider, useToast } from "@/components/ui/toast";

import PageHero from "@/components/shared/PageHero";
import StatCard from "@/components/shared/StatCard";
import ValueCard from "@/components/shared/ValueCard";
import FeatureCard from "@/components/shared/FeatureCard";
import AlertBox from "@/components/shared/AlertBox";
import TitledModal from "@/components/shared/Modal";
import ErrorState from "@/components/shared/ErrorState";

import TerminalSurface from "@/components/terminal/TerminalSurface";
import BuildLogPane from "@/components/terminal/BuildLogPane";

import { useSSEStream } from "@/lib/sse/useSSEStream";
import { createBuildMessageProcessor, type BuildMessage } from "@/lib/sse/messages";
import { STEP_INDEX, progressForStep } from "@/lib/sse/steps";
import { frameworks, stackCategories, getFrameworkConfig } from "@/lib/frameworks";
import { decodeSlug, encodeLocalSlug, encodeRepoSlug } from "@/lib/repoSlug";
import { randomUUID } from "@/lib/random-uuid";

/* ── Layout helpers (local to this page) ───────────────────────────── */

function Section({ title, note, children }: { title: string; note?: string; children: ReactNode }) {
  return (
    <section className="mb-14">
      <div className="mb-4 pb-2 border-b border-border/60">
        <h2 className="text-sm font-semibold uppercase tracking-[0.14em] text-muted-foreground">
          {title}
        </h2>
        {note && <p className="mt-1 text-xs text-muted-foreground/70">{note}</p>}
      </div>
      {children}
    </section>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center gap-3 py-2">
      <span className="w-32 shrink-0 text-xs text-muted-foreground">{label}</span>
      {children}
    </div>
  );
}

/* ── Skin picker ───────────────────────────────────────────────────── */

function SkinPicker() {
  const { theme, resolvedTheme, setTheme } = useTheme();

  return (
    <div className="flex flex-wrap items-center gap-2">
      {THEMES.map((t) => (
        <button
          key={t.id}
          type="button"
          onClick={() => setTheme(t.id)}
          title={t.hint}
          className={`px-3 py-1.5 rounded-xl text-xs font-medium border transition-colors ${
            theme === t.id
              ? "bg-primary text-primary-foreground border-primary"
              : "bg-card text-muted-foreground border-border hover:text-foreground"
          }`}
        >
          {t.label}
        </button>
      ))}
      <span className="text-xs text-muted-foreground ms-2">
        chosen <code className="text-foreground">{theme}</code> · painted{" "}
        <code className="text-foreground">{resolvedTheme}</code>
      </span>
    </div>
  );
}

/* ── SSE contract proof ────────────────────────────────────────────── */

/**
 * Feeds hand-built frames straight into the chunk parser. No backend needed —
 * this is what proves base64 log decoding, monotonic `eventId` dedup and the
 * step/progress table are all wired the way the API will emit them.
 */
function SseContractDemo() {
  const { resolvedTheme } = useTheme();
  const terminalRef = useRef<Terminal | null>(null);
  const [step, setStep] = useState(0);
  const [progress, setProgress] = useState(0);
  const [dropped, setDropped] = useState(0);
  const lastEventIdRef = useRef<number | undefined>(undefined);

  const processor = useMemo(
    () =>
      createBuildMessageProcessor({
        onLog: (message, _rawText, rawBytes) => {
          if (message.eventId !== undefined) {
            if (
              lastEventIdRef.current !== undefined &&
              message.eventId <= lastEventIdRef.current
            ) {
              setDropped((n) => n + 1);
              return;
            }
            lastEventIdRef.current = message.eventId;
          }
          if (rawBytes) terminalRef.current?.write(rawBytes);
        },
        onProgress: (currentStep, pct) => {
          setStep(currentStep);
          setProgress(pct);
        },
        onSuccess: () => {
          terminalRef.current?.write("\r\n\u001b[32m✓ deploy complete\u001b[0m\r\n");
          setProgress(100);
        },
      }),
    [],
  );

  const { processSSEChunk } = useSSEStream<BuildMessage>({
    terminalRef,
    autoWriteToTerminal: false,
    messageProcessor: processor,
  });

  const frame = useCallback(
    (payload: Record<string, unknown>) => `data: ${JSON.stringify(payload)}\n\n`,
    [],
  );

  const replay = useCallback(() => {
    lastEventIdRef.current = undefined;
    setDropped(0);
    terminalRef.current?.clear();

    let seq = 0;
    const log = (text: string) =>
      frame({ type: "log", data: btoa(text + "\r\n"), eventId: seq++ });

    const script = [
      frame({ type: "started" }),
      log("\u001b[36m→ openvault ship engine\u001b[0m"),
      frame({
        type: "progress",
        currentStep: STEP_INDEX.clone,
        progress: progressForStep("clone", "running"),
      }),
      log("cloning repository…"),
      frame({
        type: "progress",
        currentStep: STEP_INDEX.install,
        progress: progressForStep("install", "running"),
      }),
      log("installing dependencies (uv sync)"),
      log("\u001b[33mwarn\u001b[0m  lockfile is 3 days old"),
      frame({
        type: "progress",
        currentStep: STEP_INDEX.build,
        progress: progressForStep("build", "running"),
      }),
      log("next build"),
      frame({
        type: "progress",
        currentStep: STEP_INDEX.deploy,
        progress: progressForStep("deploy", "running"),
      }),
      log("starting service on :3000"),
      frame({ type: "complete", success: true }),
    ].join("");

    processSSEChunk(script);
  }, [frame, processSSEChunk]);

  const replayDuplicate = useCallback(() => {
    // Same eventId as a line already rendered: must be dropped, not painted.
    const id = lastEventIdRef.current ?? 0;
    processSSEChunk(
      frame({ type: "log", data: btoa("DUPLICATE — should never appear\r\n"), eventId: id }),
    );
  }, [frame, processSSEChunk]);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" onClick={replay}>
          Replay a build
        </Button>
        <Button size="sm" variant="outline" onClick={replayDuplicate}>
          Send a duplicate eventId
        </Button>
        <span className="text-xs text-muted-foreground">
          step <code className="text-foreground">{step}</code> · progress{" "}
          <code className="text-foreground">{progress}%</code> · dropped duplicates{" "}
          <code className="text-foreground">{dropped}</code>
        </span>
      </div>

      <div
        className="rounded-2xl border border-border overflow-hidden bg-background"
        style={{ height: "260px" }}
      >
        <TerminalSurface
          terminalRef={terminalRef}
          theme={resolvedTheme === "light" ? "light" : "dark"}
        />
      </div>
    </div>
  );
}

/* ── Toast trigger (needs to sit inside ToastProvider) ─────────────── */

function ToastRow() {
  const { toast } = useToast();
  return (
    <Row label="toast">
      <Button size="sm" variant="secondary" onClick={() => toast("info", "Nothing is on fire.")}>
        info
      </Button>
      <Button size="sm" variant="secondary" onClick={() => toast("success", "Key stored in the vault.", "Saved")}>
        success
      </Button>
      <Button size="sm" variant="secondary" onClick={() => toast("error", "Port 5000 is already in use.", "Cannot start")}>
        error
      </Button>
    </Row>
  );
}

/* ── The page ──────────────────────────────────────────────────────── */

function KitchenSink() {
  const [checkbox, setCheckbox] = useState<boolean | "indeterminate">(true);
  const [switchOn, setSwitchOn] = useState(true);
  const [tab, setTab] = useState<"smart" | "bench" | "errors">("smart");
  const [segment, setSegment] = useState<"sentinel" | "bottleneck">("sentinel");
  const [selected, setSelected] = useState<"priority" | "weighted" | "p2c">("weighted");
  const [otp, setOtp] = useState("214");
  const [modalOpen, setModalOpen] = useState(false);
  const [titledOpen, setTitledOpen] = useState(false);
  const [popoverOpen, setPopoverOpen] = useState(false);
  const [category, setCategory] = useState(stackCategories[0].id);

  const slug = encodeRepoSlug("anthropics", "openvault");
  const localSlug = encodeLocalSlug("D:\\OpenVault\\apps\\web");

  return (
    <PageContainer>
      <PageHeader
        title="Kitchen sink"
        description="Every Lane B primitive, in every state. Dev-only."
      />

      <Section title="Themes" note="All five skins plus system. Changing this rewrites data-theme on <html>.">
        <SkinPicker />
      </Section>

      <Section title="Button">
        <Row label="variants">
          <Button>Default</Button>
          <Button variant="secondary">Secondary</Button>
          <Button variant="outline">Outline</Button>
          <Button variant="ghost">Ghost</Button>
          <Button variant="destructive">Destructive</Button>
          <Button variant="link">Link</Button>
        </Row>
        <Row label="sizes">
          <Button size="sm">Small</Button>
          <Button size="default">Default</Button>
          <Button size="lg">Large</Button>
          <Button size="icon" aria-label="settings">
            <Settings />
          </Button>
        </Row>
        <Row label="with icon">
          <Button>
            <Rocket /> Deploy
          </Button>
          <Button variant="outline">
            <Download /> Export
          </Button>
        </Row>
        <Row label="disabled">
          <Button disabled>Default</Button>
          <Button variant="secondary" disabled>
            Secondary
          </Button>
          <Button variant="outline" disabled>
            Outline
          </Button>
          <Button variant="destructive" disabled>
            Destructive
          </Button>
        </Row>
        <Row label="asChild">
          <Button asChild variant="outline">
            <a href="#top">Anchor rendered as a button</a>
          </Button>
        </Row>
      </Section>

      <Section title="Form controls">
        <div className="grid gap-6 md:grid-cols-2 max-w-3xl">
          <div className="space-y-2">
            <Label htmlFor="ks-key">API key</Label>
            <Input id="ks-key" placeholder="sk-…" defaultValue="sk-live-9f2c" />
          </div>
          <div className="space-y-2">
            <Label htmlFor="ks-empty">Empty</Label>
            <Input id="ks-empty" placeholder="Placeholder state" />
          </div>
          <div className="space-y-2">
            <Label htmlFor="ks-disabled">Disabled</Label>
            <Input id="ks-disabled" disabled defaultValue="Cannot edit" />
          </div>
          <div className="space-y-2">
            <Label htmlFor="ks-error">Error</Label>
            <Input
              id="ks-error"
              aria-invalid
              defaultValue="not-a-port"
              className="border-danger-border focus-visible:border-danger-border focus-visible:ring-danger-border/40"
            />
            <p className="text-xs text-danger">Port must be a number between 1 and 65535.</p>
          </div>
          <div className="space-y-2">
            <Label htmlFor="ks-select">Native select</Label>
            <Select id="ks-select" defaultValue="uv">
              <option value="uv">uv sync</option>
              <option value="poetry">poetry install</option>
              <option value="pip">pip install -r requirements.txt</option>
            </Select>
          </div>
          <div className="space-y-2">
            <Label htmlFor="ks-select-disabled">Native select (disabled)</Label>
            <Select id="ks-select-disabled" disabled defaultValue="uv">
              <option value="uv">uv sync</option>
            </Select>
          </div>
          <div className="space-y-2 md:col-span-2">
            <Label htmlFor="ks-textarea">Textarea</Label>
            <Textarea id="ks-textarea" rows={3} defaultValue={"OPENVAULT_HOME=D:\\OpenVault\\.openvault"} />
          </div>
          <div className="space-y-2 md:col-span-2">
            <Label htmlFor="ks-textarea-disabled">Textarea (disabled)</Label>
            <Textarea id="ks-textarea-disabled" rows={2} disabled defaultValue="read-only" />
          </div>
        </div>

        <Row label="checkbox">
          <Checkbox checked={checkbox} onCheckedChange={(v) => setCheckbox(v)} aria-label="on" />
          <Checkbox checked={false} onCheckedChange={() => {}} aria-label="off" />
          <Checkbox checked="indeterminate" onCheckedChange={() => {}} aria-label="mixed" />
          <Checkbox checked tone="destructive" onCheckedChange={() => {}} aria-label="destructive" />
          <Checkbox checked disabled onCheckedChange={() => {}} aria-label="disabled on" />
          <Checkbox checked={false} disabled onCheckedChange={() => {}} aria-label="disabled off" />
          <Checkbox checked size="sm" onCheckedChange={() => {}} aria-label="small" />
          <Checkbox checked size="lg" onCheckedChange={() => {}} aria-label="large" />
        </Row>

        <Row label="switch">
          <Switch checked={switchOn} onChange={setSwitchOn} ariaLabel="fallback enabled" />
          <Switch checked={false} onChange={() => {}} ariaLabel="off" />
          <Switch checked size="sm" onChange={() => {}} ariaLabel="small on" />
          <Switch checked disabled onChange={() => {}} ariaLabel="disabled on" />
          <Switch checked={false} disabled onChange={() => {}} ariaLabel="disabled off" />
        </Row>

        <Row label="OTP">
          <OTPInput value={otp} onChange={setOtp} />
        </Row>
        <Row label="OTP disabled">
          <OTPInput value="0000" onChange={() => {}} disabled />
        </Row>
      </Section>

      <Section title="Selection">
        <Row label="tabs">
          <div className="w-full max-w-xl">
            <Tabs
              tabs={[
                { key: "smart", label: "SMART" },
                { key: "bench", label: "Benchmark" },
                { key: "errors", label: "Error log", icon: Zap },
              ]}
              value={tab}
              onChange={setTab}
            />
            <p className="mt-3 text-xs text-muted-foreground">
              active: <code className="text-foreground">{tab}</code>
            </p>
          </div>
        </Row>

        <Row label="segmented">
          <SlidingToggle
            options={[
              { value: "sentinel", label: "Sentinel" },
              { value: "bottleneck", label: "Bottleneck" },
            ]}
            value={segment}
            onChange={setSegment}
          />
          <SlidingToggle
            variant="square"
            size="sm"
            options={[
              { value: "sentinel", icon: <ShieldCheck className="size-3.5" /> },
              { value: "bottleneck", icon: <Zap className="size-3.5" /> },
            ]}
            value={segment}
            onChange={setSegment}
          />
        </Row>

        <Row label="custom select">
          <div className="w-72">
            <CustomSelect
              value={selected}
              onChange={setSelected}
              options={[
                { value: "priority", label: "Priority", icon: <KeyRound className="size-4" /> },
                { value: "weighted", label: "Weighted" },
                { value: "p2c", label: "Power of two choices" },
              ]}
              footerAction={{
                label: "Add a strategy",
                icon: <Zap className="size-4" />,
                onClick: () => {},
              }}
            />
          </div>
          <div className="w-56">
            <CustomSelect
              value={"priority" as const}
              onChange={() => {}}
              disabled
              options={[{ value: "priority", label: "Disabled" }]}
            />
          </div>
        </Row>

        <Row label="dropdown">
          <DropdownMenu
            actions={[
              { id: "copy", label: "Copy key id", icon: <Copy className="size-4" /> },
              { id: "rotate", label: "Rotate", icon: <KeyRound className="size-4" />, variant: "success" },
              { id: "d1", divider: true },
              { id: "revoke", label: "Revoke", icon: <Trash2 className="size-4" />, variant: "danger" },
              { id: "locked", label: "Needs elevation", disabled: true },
            ]}
          />
          <DropdownMenu
            align="left"
            trigger={<span className="text-sm">Custom trigger</span>}
            triggerClassName="px-3 py-1.5 rounded-xl border border-border text-foreground hover:bg-muted"
            actions={[{ id: "a", label: "Action" }]}
          />
          <DropdownMenu disabled actions={[{ id: "a", label: "Never shown" }]} />
        </Row>

        <Row label="popover">
          <DismissiblePopover open={popoverOpen} onOpenChange={setPopoverOpen} className="relative">
            <Button size="sm" variant="outline" onClick={() => setPopoverOpen((o) => !o)}>
              <Ellipsis /> Toggle popover
            </Button>
            {popoverOpen && (
              <div className="absolute z-50 mt-2 w-64 rounded-2xl border border-border bg-popover p-4 shadow-xl">
                <p className="text-sm text-foreground">Dismisses on outside click, focus loss or Escape.</p>
              </div>
            )}
          </DismissiblePopover>
        </Row>
      </Section>

      <Section title="Overlays">
        <Row label="modals">
          <Button onClick={() => setModalOpen(true)}>ui/Modal (portal)</Button>
          <Button variant="outline" onClick={() => setTitledOpen(true)}>
            shared/Modal (titled)
          </Button>
        </Row>

        <Modal isOpen={modalOpen} onClose={() => setModalOpen(false)} width="480px">
          <div className="p-6 space-y-4">
            <h3 className="text-lg font-semibold text-foreground">Add a key</h3>
            <div className="space-y-2">
              <Label htmlFor="ks-modal-key">Secret</Label>
              <Input id="ks-modal-key" placeholder="sk-…" />
            </div>
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={() => setModalOpen(false)}>
                Cancel
              </Button>
              <Button onClick={() => setModalOpen(false)}>Store</Button>
            </div>
          </div>
        </Modal>

        <TitledModal isOpen={titledOpen} onClose={() => setTitledOpen(false)} title="Rotate key">
          <p className="text-sm text-muted-foreground">
            The old key stays valid until the new one passes a precheck.
          </p>
          <div className="mt-4 flex justify-end">
            <Button size="sm" onClick={() => setTitledOpen(false)}>
              Done
            </Button>
          </div>
        </TitledModal>
      </Section>

      <Section title="Feedback">
        <div className="grid gap-4 md:grid-cols-3 mb-6">
          <AlertBox type="info" title="Info" message="Detection ran against the local reader." />
          <AlertBox type="warning" title="Warning" message="Lockfile is older than the manifest." />
          <AlertBox type="danger" title="Danger" message="Vault is sealed; keys cannot be read." />
        </div>

        <div className="space-y-4 max-w-3xl">
          <WarningCallout
            title="Sentinel is degraded"
            description="SMART reads fell back to WMI because the process is not elevated."
            actions={
              <>
                <Button size="sm" variant="outline">
                  Why?
                </Button>
                <Button size="sm">Restart elevated</Button>
              </>
            }
          />
          <WarningCallout
            tone="danger"
            title="Master key mismatch"
            description="Encrypted rows exist but the current key cannot decrypt them. Refusing to regenerate."
          />
          <WarningCallout tone="info" title="Mock data" description="This trace is synthetic — no admin timings were captured.">
            <ul className="mt-2 space-y-1 text-[13px] text-muted-foreground">
              <li>· needs Administrator</li>
              <li>· adapter fell back to WMI</li>
            </ul>
          </WarningCallout>
        </div>

        <div className="mt-6">
          <ToastRow />
        </div>
      </Section>

      <Section title="Cards">
        <div className="grid gap-4 md:grid-cols-3 mb-6">
          <Card>
            <CardHeader>
              <CardTitle>Card</CardTitle>
              <CardDescription>Header, content and footer.</CardDescription>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-muted-foreground">
                Surfaces read <code>bg-card</code>, so every skin restyles them for free.
              </p>
            </CardContent>
            <CardFooter className="gap-2">
              <Button size="sm" variant="outline">
                Cancel
              </Button>
              <Button size="sm">Save</Button>
            </CardFooter>
          </Card>

          <StatCard number="18" label="Keys in vault" icon={KeyRound} />
          <StatCard number="94%" label="Endurance left" tone="success" icon={ShieldCheck} />
        </div>

        <div className="grid gap-4 md:grid-cols-2 mb-6">
          <ValueCard
            icon={ShieldCheck}
            title="One vault"
            description="Every provider credential lives in one encrypted store, with custody and revocation."
          />
          <FeatureCard
            icon={Rocket}
            title="Ship"
            description="Detect the stack, pick a target, watch the build stream."
          />
        </div>

        <div className="rounded-2xl border border-dashed border-border p-6">
          <PageHero
            eyebrow="Section landing"
            title="PageHero"
            description="Retokened from the vendor's marketing hero so it survives a light theme."
          />
        </div>
      </Section>

      <Section
        title="Terminal + SSE contract"
        note="Frames are synthesised in the browser — this proves the contract without a backend."
      >
        <SseContractDemo />

        <div className="mt-8">
          <p className="mb-3 text-xs text-muted-foreground">
            BuildLogPane, idle (no stream URL): step rail, status line and terminal.
          </p>
          <BuildLogPane height="220px" />
        </div>
      </Section>

      <Section title="Data modules" note="lib/frameworks, lib/repoSlug, lib/random-uuid.">
        <Row label="categories">
          <SlidingToggle
            options={stackCategories.map((c) => ({ value: c.id, label: c.label }))}
            value={category}
            onChange={setCategory}
          />
        </Row>

        <div className="mt-4 flex flex-wrap gap-2">
          {frameworks
            .filter((fw) => fw.category === category)
            .map((fw) => (
              <div
                key={fw.id}
                className="flex items-center gap-2 rounded-xl border border-border bg-card px-3 py-2"
                title={`${fw.options.buildCommand || "no build"} · :${fw.defaultPort}`}
              >
                <span className="flex size-6 items-center justify-center rounded-md bg-muted text-[11px] font-bold text-muted-foreground">
                  {fw.letter}
                </span>
                <span className="text-sm text-foreground">{fw.name}</span>
              </div>
            ))}
        </div>

        <dl className="mt-6 space-y-1 text-xs text-muted-foreground">
          <div>
            <dt className="inline font-medium text-foreground">frameworks: </dt>
            <dd className="inline">{frameworks.length} stacks</dd>
          </div>
          <div>
            <dt className="inline font-medium text-foreground">getFrameworkConfig(&quot;nextjs&quot;): </dt>
            <dd className="inline">
              <code>{getFrameworkConfig("nextjs").options.buildCommand}</code>
            </dd>
          </div>
          <div>
            <dt className="inline font-medium text-foreground">getFrameworkConfig(&quot;nope&quot;): </dt>
            <dd className="inline">
              <code>{getFrameworkConfig("nope").id}</code> (fallback, never throws)
            </dd>
          </div>
          <div>
            <dt className="inline font-medium text-foreground">encodeRepoSlug: </dt>
            <dd className="inline">
              <code>{slug}</code> → <code>{JSON.stringify(decodeSlug(slug))}</code>
            </dd>
          </div>
          <div>
            <dt className="inline font-medium text-foreground">encodeLocalSlug: </dt>
            <dd className="inline">
              <code>{localSlug}</code> → <code>{JSON.stringify(decodeSlug(localSlug))}</code>
            </dd>
          </div>
          <div>
            <dt className="inline font-medium text-foreground">randomUUID: </dt>
            <dd className="inline">
              <code>{randomUUID()}</code>
            </dd>
          </div>
        </dl>
      </Section>

      <Section
        title="Error states"
        note="ErrorState brings its own PageContainer, so the offset below is doubled here — expected on this page only."
      >
        <div className="rounded-2xl border border-dashed border-border overflow-hidden">
          <ErrorState
            type="api-unreachable"
            help="The desktop shell writes the server log to %APPDATA%\OpenVault."
          />
        </div>
      </Section>
    </PageContainer>
  );
}

export default function KitchenSinkPage() {
  // Providers are mounted locally so this page works before the app shell wires
  // them in the root layout.
  return (
    <ThemeProvider>
      <ThemeScript />
      <ToastProvider>
        <KitchenSink />
      </ToastProvider>
    </ThemeProvider>
  );
}
