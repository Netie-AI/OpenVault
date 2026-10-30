// Modified by Netie AI, 2026: covers the upstream credit on the About surface.
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  useDeploymentInfo: vi.fn(() => {
    throw new Error("InstanceInfo must use the SSR platform version");
  }),
}));

vi.mock("@/context/PlatformContext", () => ({
  usePlatform: () => ({
    authMode: "local",
    deployMode: "docker",
    version: "0.6.9",
  }),
}));
vi.mock("@/context/AuthContext", () => ({
  useAuth: () => ({ user: { email: "owner@example.com" } }),
}));
vi.mock("@/hooks/useDeploymentInfo", () => ({
  useDeploymentInfo: mocks.useDeploymentInfo,
}));
vi.mock("@/components/i18n-provider", () => ({
  interpolate: (value: string, vars: Record<string, string>) =>
    value.replace("{{mode}}", vars.mode ?? ""),
  useI18n: () => ({
    t: {
      settings: {
        instance: {
          title: "Instance",
          descDesktop: "Desktop",
          descCloud: "Cloud",
          descSelfHosted: "Self-hosted",
          typeDesktop: "Desktop",
          typeCloud: "Cloud",
          typeSelfHosted: "Self-hosted",
          deployMode: "Mode: {{mode}}",
          authNone: "No auth",
          authCloud: "Cloud auth",
          authLocal: "Local auth",
          localUser: "Local user",
          change: "Change",
        },
      },
    },
  }),
}));
vi.mock("./SettingsSection", () => ({
  SettingsSection: ({ children }: { children: React.ReactNode }) => <section>{children}</section>,
}));
vi.mock("./UpgradeAuthModal", () => ({ UpgradeAuthModal: () => null }));

import { InstanceInfo } from "./InstanceInfo";

describe("InstanceInfo release version", () => {
  it("uses the SSR platform value without a duplicate client health request", () => {
    const html = renderToStaticMarkup(<InstanceInfo />);

    expect(html).toContain("v0.6.9");
    expect(mocks.useDeploymentInfo).not.toHaveBeenCalled();
  });

  it("is the About surface that credits the upstream project", () => {
    const html = renderToStaticMarkup(<InstanceInfo />);

    expect(html).toContain("FreeBuild is based on");
    expect(html).toContain('href="https://github.com/oblien/openship"');
    expect(html).toContain("Apache-2.0");
  });
});
