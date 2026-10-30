"use client";

import { useSearchParams, useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { cn } from "@/shared/utils/cn";
import { MuxServiceTab } from "./tabs/MuxServiceTab";
import { BifrostServiceTab } from "./tabs/BifrostServiceTab";
import { OpenwaServiceTab } from "./tabs/OpenwaServiceTab";

// FreeRoute: the "9router", "cliproxy" and "dario" tabs are removed. Installing
// or adopting 9router (a separate upstream product) answers
// upstream_service_not_included, and the CLIProxyAPI and Dario sidecars (Claude
// subscription pooling) answer consumer_subscription_pooling_disabled, at every
// /api/services/{9router,cliproxy,dario}/* route (src/lib/netie/hardDisabledRoutes.ts).
type Tab = "mux" | "bifrost" | "openwa";

const TABS: { id: Tab; label: string; icon: string }[] = [
  { id: "mux", label: "Mux", icon: "hub" },
  { id: "bifrost", label: "Bifrost", icon: "bolt" },
  { id: "openwa", label: "open-wa", icon: "chat" },
];

export default function ServicesPage() {
  const t = useTranslations("embeddedServices");
  const sp = useSearchParams();
  const router = useRouter();
  const active = (sp.get("tab") ?? "mux") as Tab;

  function setTab(tab: Tab) {
    router.push(`/dashboard/providers/services?tab=${tab}`);
  }

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-xl font-semibold text-text-primary">{t("title")}</h1>
        <p className="text-sm text-text-muted mt-1">{t("description")}</p>
      </header>

      {/* Tab strip */}
      <div className="flex gap-1 border-b border-border">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            type="button"
            onClick={() => setTab(tab.id)}
            className={cn(
              "inline-flex items-center gap-1.5 px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors",
              active === tab.id
                ? "border-primary text-text-primary"
                : "border-transparent text-text-muted hover:text-text-primary hover:border-border"
            )}
            aria-current={active === tab.id ? "page" : undefined}
          >
            <span className="material-symbols-outlined text-[16px] leading-none">{tab.icon}</span>
            {tab.label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div>
        {active === "mux" && <MuxServiceTab />}
        {active === "bifrost" && <BifrostServiceTab />}
        {active === "openwa" && <OpenwaServiceTab />}
      </div>
    </div>
  );
}
