import type { RegistryEntry } from "../../../shared.ts";
import { CHAT_OPENAI_COMPAT_MODELS } from "../../../shared.ts";

export const gitlawb_gmiProvider: RegistryEntry = {
  id: "gitlawb-gmi",
  alias: "glb-gmi",
  format: "openai",
  executor: "default",
  baseUrl: "https://opengateway.gitlawb.com/v1/gmi-cloud",
  authType: "apikey",
  authHeader: "bearer",
  passthroughModels: true,
  models: CHAT_OPENAI_COMPAT_MODELS["gitlawb-gmi"],
};
