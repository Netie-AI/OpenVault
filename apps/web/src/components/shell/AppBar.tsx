"use client";

import type { CSSProperties } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Activity,
  KeyRound,
  LayoutDashboard,
  Network,
  Palette,
  Rocket,
  Route,
  Server,
  Settings,
  Shield,
} from "lucide-react";
import { cn } from "@/lib/utils";
import DropdownMenu, { type MenuAction } from "@/components/ui/DropdownMenu";
import { THEMES, useTheme, type Theme } from "@/components/theme-provider";

/**
 * Full-width fixed top bar — the nav AND the Electron drag region.
 * No sidebar. PageContainer owns the only top padding (reads --ov-topbar-h).
 */
const NAV = [
  { href: "/", label: "Overview", icon: LayoutDashboard },
  { href: "/detect", label: "Detect", icon: Activity },
  { href: "/vault", label: "Vault", icon: KeyRound },
  { href: "/engine", label: "Engine", icon: Server },
  { href: "/ship", label: "Ship", icon: Rocket },
  { href: "/proxy", label: "Route", icon: Route },
  { href: "/peers", label: "Peers", icon: Network },
  { href: "/gate", label: "Gate", icon: Shield },
  { href: "/settings", label: "Settings", icon: Settings },
] as const;

function SkinPicker() {
  const { theme, setTheme } = useTheme();
  const actions: MenuAction[] = THEMES.map((t) => ({
    id: t.id,
    label: `${t.label}${theme === t.id ? " ✓" : ""}`,
    onClick: () => setTheme(t.id as Theme),
  }));

  return (
    <DropdownMenu
      align="right"
      actions={actions}
      triggerClassName="inline-flex"
      trigger={
        <span
          className="inline-flex h-8 items-center gap-1.5 rounded-lg px-3 text-xs font-medium text-muted-foreground no-drag hover:bg-accent hover:text-accent-foreground"
          aria-label="Choose skin"
        >
          <Palette className="size-3.5" />
          <span className="hidden sm:inline">
            {THEMES.find((t) => t.id === theme)?.label ?? "Theme"}
          </span>
        </span>
      }
    />
  );
}

export function AppBar() {
  const pathname = usePathname();

  return (
    <header
      data-glass
      className={cn(
        "fixed inset-x-0 top-0 z-40 h-[var(--ov-topbar-h)]",
        "border-b border-border bg-card/80",
        "app-titlebar",
      )}
      style={{ WebkitAppRegion: "drag" } as CSSProperties}
    >
      <div className="mx-auto flex h-full max-w-[1600px] items-center gap-3 px-4 lg:px-6">
        <Link
          href="/"
          className="no-drag flex shrink-0 items-center gap-2.5"
          style={{ WebkitAppRegion: "no-drag" } as CSSProperties}
        >
          <span className="flex size-7 items-center justify-center rounded-lg bg-primary text-[11px] font-bold tracking-wide text-primary-foreground">
            OV
          </span>
          <span className="text-sm font-semibold tracking-tight text-foreground">
            OpenVault
          </span>
        </Link>

        <nav
          aria-label="Primary"
          className="no-drag flex min-w-0 flex-1 items-center gap-0.5 overflow-x-auto"
          style={{ WebkitAppRegion: "no-drag" } as CSSProperties}
        >
          {NAV.map((item) => {
            const active =
              item.href === "/"
                ? pathname === "/"
                : pathname.startsWith(item.href);
            const Icon = item.icon;
            return (
              <Link
                key={item.href}
                href={item.href}
                className={cn(
                  "inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium transition-colors",
                  active
                    ? "bg-accent text-accent-foreground"
                    : "text-muted-foreground hover:bg-accent/60 hover:text-foreground",
                )}
              >
                <Icon className="size-3.5 shrink-0" strokeWidth={2} />
                <span className="hidden md:inline">{item.label}</span>
              </Link>
            );
          })}
        </nav>

        <div
          className="no-drag flex shrink-0 items-center gap-2"
          style={{ WebkitAppRegion: "no-drag" } as CSSProperties}
        >
          <span className="hidden rounded-full border border-border px-2 py-0.5 text-[10px] uppercase tracking-wide text-muted-foreground sm:inline">
            local
          </span>
          <SkinPicker />
        </div>
      </div>
    </header>
  );
}

export default AppBar;
