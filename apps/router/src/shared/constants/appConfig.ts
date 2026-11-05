import pkg from "../../../package.json" with { type: "json" };

export const APP_CONFIG = {
  name: "FreeRoute",
  description: "Netie's free AI gateway",
  version: pkg.version,
};

export const THEME_CONFIG = {
  storageKey: "theme",
  defaultTheme: "system",
};
