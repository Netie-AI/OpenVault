// @vitest-environment happy-dom

import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { baseDictionary } from "@/i18n";
import type { Service } from "@/lib/api/services";
import { ApiError } from "@/lib/api/client";
import { Deployments } from "./Deployments";

const mocks = vi.hoisted(() => ({
  context: vi.fn(),
  trigger: vi.fn(),
  showModal: vi.fn(),
  hideModal: vi.fn(),
  setActiveTab: vi.fn(),
  openBuild: vi.fn(),
  showToast: vi.fn(),
  commitStatus: vi.fn(),
}));
vi.mock("@/context/ProjectSettingsContext", () => ({ useProjectSettings: mocks.context }));
vi.mock("@/context/ToastContext", () => ({ useToast: () => ({ showToast: mocks.showToast }) }));
vi.mock("@/context/ModalContext", () => ({
  useModal: () => ({ showModal: mocks.showModal, hideModal: mocks.hideModal }),
}));
vi.mock("@/lib/api", async () => {
  const { getApiErrorMessage } = await import("@/lib/api/client");
  return {
    deployApi: { trigger: mocks.trigger },
    projectsApi: { getCommitStatus: mocks.commitStatus },
    isAbortError: () => false,
    getApiErrorMessage,
  };
});
vi.mock("@/lib/deploy-nav", () => ({ openTriggeredBuild: mocks.openBuild }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/components/i18n-provider", () => ({
  useI18n: () => ({ t: baseDictionary }),
  interpolate: (text: string, values: Record<string, string>) =>
    text.replace(/\{(\w+)\}/g, (_, key: string) => values[key] ?? key),
}));
vi.mock("@/app/(dashboard)/deployments/components", () => ({ DeploymentsContent: () => null }));

const service: Service = {
  id: "web",
  name: "web",
  kind: "compose",
  image: "web:1",
  build: null,
  dockerfile: null,
  buildArgs: null,
  ports: ["20020:9000"],
  dependsOn: [],
  environment: {},
  volumes: [],
  command: null,
  restart: "unless-stopped",
  exposed: false,
  exposedPort: "9000",
  domain: null,
  customDomain: null,
  domainType: "custom",
  publicEndpoints: [],
  enabled: true,
  sortOrder: 0,
};
let root: Root;
let container: HTMLDivElement;
let context: {
  id: string;
  projectData: { id: string; name: string; port: number };
  hasMultipleServices: boolean;
  servicesData: { services: Service[] };
  domainsData: { domains: Array<{ hostname: string; serviceId?: string; targetPort?: number }> };
  refreshServices: ReturnType<typeof vi.fn>;
  setActiveTab: typeof mocks.setActiveTab;
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  context = {
    id: "project",
    projectData: { id: "project", name: "demo", port: 9000 },
    hasMultipleServices: true,
    servicesData: { services: [service] },
    domainsData: { domains: [] },
    refreshServices: vi.fn().mockResolvedValue([service]),
    setActiveTab: mocks.setActiveTab,
  };
  mocks.context.mockImplementation(() => context);
  mocks.trigger.mockResolvedValue({ data: { deploymentId: "deployment" } });
  mocks.commitStatus.mockResolvedValue({ data: { supported: false } });
  mocks.showModal.mockReturnValue("warning");
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
});

function button(text: string) {
  const result = [...container.querySelectorAll("button")].find((el) => el.textContent === text);
  expect(result, text).toBeDefined();
  return result!;
}

async function redeploy() {
  await act(async () => root.render(<Deployments />));
  await act(async () => button(baseDictionary.projects.redeploy.redeployProject).click());
}

it.each(["project", "new-commit"])(
  "shows the API rejection when deploying from %s",
  async (entry) => {
    context.hasMultipleServices = false;
    const reason = "Compose environment needs review (postgres: POSTGRES_PASSWORD).";
    mocks.trigger.mockRejectedValue(new ApiError(409, "Conflict", { error: reason }));
    mocks.commitStatus.mockResolvedValue({
      data: {
        supported: true,
        behind: true,
        mode: "commit",
        latestSha: "803526d",
        deployedSha: "1d4bfc0",
        branch: "main",
      },
    });

    await act(async () => root.render(<Deployments />));
    await act(async () =>
      button(
        entry === "new-commit"
          ? baseDictionary.projects.redeploy.redeployLatest
          : baseDictionary.projects.redeploy.redeployProject,
      ).click(),
    );

    expect(mocks.showToast).toHaveBeenCalledWith(
      reason,
      "error",
      baseDictionary.projects.redeploy.errorTitle,
    );
    expect(mocks.openBuild).not.toHaveBeenCalled();
    expect(button(baseDictionary.projects.redeploy.redeployProject).disabled).toBe(false);
  },
);

it.each([
  { hostname: "app.example.test", serviceId: "web", targetPort: 9000 },
  { hostname: "app.example.test", targetPort: 20020 },
  { hostname: "app.example.test" },
])("redeploys using the loaded domain route without a false warning: %j", async (domain) => {
  context.domainsData.domains = [domain];
  await redeploy();
  expect(mocks.showModal).not.toHaveBeenCalled();
  expect(mocks.trigger).toHaveBeenCalledWith({ projectId: "project", smartRoute: true });
  expect(mocks.openBuild).toHaveBeenCalled();
});

it("checks freshly loaded services against the project's domain routes", async () => {
  context.servicesData.services = [];
  context.domainsData.domains = [{ hostname: "app.example.test", serviceId: "web" }];
  await redeploy();
  expect(context.refreshServices).toHaveBeenCalledOnce();
  expect(mocks.trigger).toHaveBeenCalledOnce();
  expect(mocks.showModal).not.toHaveBeenCalled();
});

it("keeps the warning for another service's route and sends the operator to Domains", async () => {
  context.domainsData.domains = [
    { hostname: "other.example.test", serviceId: "other", targetPort: 9000 },
  ];
  await redeploy();
  expect(mocks.trigger).not.toHaveBeenCalled();
  expect(mocks.showModal).toHaveBeenCalledOnce();
  const modal = mocks.showModal.mock.calls[0][0] as { customContent: ReactNode };
  await act(async () => root.render(modal.customContent));
  await act(async () => button(baseDictionary.projects.redeploy.openDomains).click());
  expect(mocks.setActiveTab).toHaveBeenCalledWith("domains");
  expect(mocks.hideModal).toHaveBeenCalledWith("warning");
});
