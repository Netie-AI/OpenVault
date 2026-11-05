// Copyright (c) 2026 Netie AI. Licensed under Apache-2.0.
// Free project domains hang off HOST_DOMAIN. Without it there is no base domain,
// and the card must not offer one (it used to fall back to the upstream cloud
// domain and show "my-project.opsh.io" to a user who does not own it).
import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";

import { I18nProvider } from "@/components/i18n-provider";
import { ModalProvider } from "@/context/ModalContext";
import { PlatformProvider } from "@/context/PlatformContext";
import { RoutingSettingsCard } from "./RoutingSettingsCard";

function render(hostDomain: string | undefined, domainType: "free" | "custom") {
  return renderToStaticMarkup(
    <I18nProvider>
      <PlatformProvider hostDomain={hostDomain}>
        <ModalProvider>
        <RoutingSettingsCard
          projectName="my-project"
          domain="my-project"
          customDomain="app.example.test"
          domainType={domainType}
          exposed
          onDomainTypeChange={() => {}}
          onDomainChange={() => {}}
          onCustomDomainChange={() => {}}
        />
        </ModalProvider>
      </PlatformProvider>
    </I18nProvider>,
  );
}

describe("RoutingSettingsCard free domain", () => {
  it("shows no upstream domain and no Free option when HOST_DOMAIN is unset", () => {
    for (const type of ["free", "custom"] as const) {
      const html = render(undefined, type);
      expect(html).not.toMatch(/opsh\.io|oblien|openship/i);
      expect(html).not.toContain("my-project.");
    }
    // Even a project saved as "free" falls back to the custom-domain input.
    expect(render(undefined, "free")).toContain("app.example.test");
  });

  it("offers the free subdomain under HOST_DOMAIN when it is set", () => {
    const html = render("apps.example.test", "free");
    expect(html).toContain(".apps.example.test");
    expect(html).not.toMatch(/opsh\.io/);
  });
});
