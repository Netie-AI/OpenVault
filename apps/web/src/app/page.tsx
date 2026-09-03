import Link from "next/link";
import {
  Activity,
  KeyRound,
  Network,
  Rocket,
  Route,
  Server,
  Shield,
} from "lucide-react";
import { PageContainer } from "@/components/ui/PageContainer";
import { PageHeader } from "@/components/ui/PageHeader";
import { cn } from "@/lib/utils";

const LINKS = [
  {
    href: "/detect",
    title: "Detect",
    body: "Host inventory and path-trace. Live vs mock is always labeled.",
    icon: Activity,
  },
  {
    href: "/vault",
    title: "Vault",
    body: "Encrypted API keys — one-stop custody against OpenMW FastAPI.",
    icon: KeyRound,
  },
  {
    href: "/ship",
    title: "Ship",
    body: "Pick a folder or GitHub repo → auto-detect stack → deploy.",
    icon: Rocket,
  },
  {
    href: "/engine",
    title: "Engine",
    body: "Models grouped by provider whose keys pass precheck.",
    icon: Server,
  },
  {
    href: "/proxy",
    title: "Route",
    body: "LLM proxy strategies, breakers, fallback — OmniRoute algorithms on our backend.",
    icon: Route,
  },
  {
    href: "/peers",
    title: "Peers",
    body: "OpenVault ↔ Cortex ↔ OpenIDE mesh handshake.",
    icon: Network,
  },
  {
    href: "/gate",
    title: "Gate",
    body: "Policy check on demand — never auto-fires on mount.",
    icon: Shield,
  },
] as const;

export default function HomePage() {
  return (
    <PageContainer>
      <PageHeader
        title="OpenVault"
        description="Custody · ship · gate · mesh. One app on :3010."
      />

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {LINKS.map((item) => {
          const Icon = item.icon;
          return (
            <Link
              key={item.href}
              href={item.href}
              data-glass
              className={cn(
                "group rounded-2xl border border-border bg-card p-5 transition-colors",
                "hover:border-foreground/20 hover:bg-accent/40",
              )}
            >
              <div className="mb-3 flex items-center gap-2 text-foreground">
                <Icon className="size-4 text-muted-foreground transition-colors group-hover:text-foreground" />
                <h2 className="text-sm font-semibold tracking-tight">{item.title}</h2>
              </div>
              <p className="text-sm leading-relaxed text-muted-foreground">{item.body}</p>
            </Link>
          );
        })}
      </div>
    </PageContainer>
  );
}
