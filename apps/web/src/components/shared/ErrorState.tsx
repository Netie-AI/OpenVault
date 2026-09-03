"use client";

import { useRouter } from "next/navigation";
import {
  AlertTriangle,
  GitBranch,
  Lock,
  ArrowLeft,
  Plus,
  PlugZap,
  RefreshCw,
  HelpCircle,
} from "lucide-react";
import { PageContainer } from "@/components/ui/PageContainer";

/* ── Error type configs ─────────────────────────────────────────────── */

type ErrorType = "repo-not-found" | "project-not-found" | "access-denied" | "api-unreachable";

interface ErrorAction {
  label: string;
  icon: typeof ArrowLeft;
  variant: "primary" | "secondary";
  path?: string;
  onClick?: () => void;
}

interface ErrorStateProps {
  type?: ErrorType;
  error?: {
    message?: string;
    details?: string;
  };
  /** Replaces the default action row entirely when supplied. */
  actions?: ErrorAction[];
  /** Extra help text under the card. Omit to hide the help panel. */
  help?: string;
}

const ERROR_CONFIGS: Record<
  ErrorType,
  {
    icon: typeof AlertTriangle;
    iconColor: string;
    iconBg: string;
    title: string;
    subtitle: string;
    hints: string[];
    actions: ErrorAction[];
  }
> = {
  "repo-not-found": {
    icon: GitBranch,
    iconColor: "text-destructive",
    iconBg: "bg-destructive/10",
    title: "Repository not found",
    subtitle: "We could not read that repository.",
    hints: [
      "The repository was renamed, moved, or deleted",
      "Your GitHub token no longer has access to it",
      "It is private and the token is missing the `repo` scope",
    ],
    actions: [
      { label: "Back to Ship", icon: ArrowLeft, variant: "secondary", path: "/ship" },
      { label: "Pick another repo", icon: Plus, variant: "primary", path: "/ship" },
    ],
  },
  "project-not-found": {
    icon: AlertTriangle,
    iconColor: "text-destructive",
    iconBg: "bg-destructive/10",
    title: "Project not found",
    subtitle: "This project is no longer in the library.",
    hints: [
      "The folder was moved or deleted on disk",
      "The library entry was removed",
    ],
    actions: [
      { label: "Back to overview", icon: ArrowLeft, variant: "secondary", path: "/" },
      { label: "Add a project", icon: Plus, variant: "primary", path: "/ship" },
    ],
  },
  "access-denied": {
    icon: Lock,
    iconColor: "text-warning",
    iconBg: "bg-warning-bg",
    title: "Access denied",
    subtitle: "This action is restricted to the local machine.",
    hints: [
      "Deploy and device-control routes only answer on loopback",
      "You are reaching the UI through a tunnel or another host",
    ],
    actions: [{ label: "Back to overview", icon: ArrowLeft, variant: "secondary", path: "/" }],
  },
  "api-unreachable": {
    icon: PlugZap,
    iconColor: "text-destructive",
    iconBg: "bg-destructive/10",
    title: "OpenVault API is not responding",
    subtitle: "The UI is running, but the backend on port 5000 did not answer.",
    hints: [
      "The server process is still starting up",
      "It exited — check the desktop shell's server log",
      "Another process is holding port 5000",
    ],
    actions: [{ label: "Retry", icon: RefreshCw, variant: "primary" }],
  },
};

/* ── Component ──────────────────────────────────────────────────────── */

export default function ErrorState({
  error = {},
  type = "repo-not-found",
  actions,
  help,
}: ErrorStateProps) {
  const router = useRouter();

  const config = ERROR_CONFIGS[type] ?? ERROR_CONFIGS["repo-not-found"];
  const Icon = config.icon;

  const title = error.message || config.title;
  const subtitle = error.details || config.subtitle;
  const resolvedActions = actions ?? config.actions;

  return (
    <PageContainer>
      <div className="max-w-xl mx-auto pt-12">
        {/* ── Error card ───────────────────────────────────────── */}
        <div className="bg-card rounded-2xl border border-border/50">
          <div className="px-6 py-5 border-b border-border/50">
            <div className="flex items-center gap-4">
              <div
                className={`w-11 h-11 rounded-xl ${config.iconBg} flex items-center justify-center`}
              >
                <Icon className={`size-5 ${config.iconColor}`} />
              </div>
              <div>
                <h2 className="text-lg font-semibold text-foreground">{title}</h2>
                <p className="text-sm text-muted-foreground/70 mt-0.5">{subtitle}</p>
              </div>
            </div>
          </div>

          <div className="px-6 py-5 space-y-5">
            {config.hints.length > 0 && (
              <div>
                <h3 className="text-sm font-medium text-foreground mb-2.5">Common causes</h3>
                <ul className="space-y-1.5">
                  {config.hints.map((hint, i) => (
                    <li
                      key={i}
                      className="flex items-start gap-2.5 text-sm text-muted-foreground"
                    >
                      <span className="mt-1.5 size-1 rounded-full bg-muted-foreground/40 shrink-0" />
                      {hint}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            <div className="flex gap-3 pt-1">
              {resolvedActions.map((action, i) => {
                const ActionIcon = action.icon;
                return (
                  <button
                    key={i}
                    // No path and no handler means "just reload" — that is the
                    // right retry for a backend that was not up yet.
                    onClick={() =>
                      action.onClick
                        ? action.onClick()
                        : action.path
                          ? router.push(action.path)
                          : router.refresh()
                    }
                    className={`flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm font-medium transition-colors ${
                      action.variant === "primary"
                        ? "bg-foreground text-background hover:bg-foreground/90"
                        : "bg-muted/60 text-foreground hover:bg-muted"
                    }`}
                  >
                    <ActionIcon className="size-4" />
                    {action.label}
                  </button>
                );
              })}
            </div>
          </div>
        </div>

        {/* ── Help card ────────────────────────────────────────── */}
        {help && (
          <div className="bg-card rounded-2xl border border-border/50 mt-4 px-5 py-4">
            <div className="flex items-start gap-3">
              <div className="w-9 h-9 rounded-xl bg-primary/10 flex items-center justify-center shrink-0">
                <HelpCircle className="size-[18px] text-primary" />
              </div>
              <p className="text-xs text-muted-foreground/70 leading-relaxed">{help}</p>
            </div>
          </div>
        )}
      </div>
    </PageContainer>
  );
}
