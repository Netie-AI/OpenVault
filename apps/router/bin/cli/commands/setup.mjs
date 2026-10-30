import { fileURLToPath, pathToFileURL } from "node:url";
import { dirname, resolve } from "node:path";
import { createPrompt, printHeading, printInfo, printSuccess } from "../io.mjs";
import { openOmniRouteDb } from "../sqlite.mjs";
import { getSettings, hashManagementPassword, updateSettings } from "../settings-store.mjs";
import { KEYS_MANAGED_BY_OPENVAULT_MESSAGE, refuseLocalKeyWrite } from "../openvault-keys.mjs";
import { registerSetupOpenCode } from "./setup-open-code.mjs";
import { t } from "../i18n.mjs";

const PROJECT_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");

async function getListCliTools() {
  const { listCliTools } = await import(pathToFileURL(resolve(PROJECT_ROOT, "src/shared/constants/cliTools.ts")).href);
  return listCliTools;
}

// An explicit request to add a provider. `--api-key` alone is not one: the global
// `--api-key` / OMNIROUTE_API_KEY is the FreeRoute server key (see mergeSetupOptions),
// so a server-key user running `setup` must not be told they asked for a provider.
function wantsProviderSetup(opts) {
  return Boolean(opts.addProvider) || Boolean(opts.provider);
}

async function resolvePassword(opts, prompt, nonInteractive, settings) {
  if (opts.password !== undefined) return opts.password;
  if (!settings.password && process.env.INITIAL_PASSWORD) return process.env.INITIAL_PASSWORD;
  if (nonInteractive) return "";

  const answer = await prompt.ask("Set an admin password now? [y/N]", "N");
  if (!/^y(es)?$/i.test(answer)) return "";

  const password = await prompt.askSecret("Admin password");
  const confirm = await prompt.askSecret("Confirm password");
  if (password !== confirm) {
    throw new Error("Passwords do not match.");
  }
  return password;
}

async function setupPassword(db, opts, prompt, nonInteractive) {
  const settings = getSettings(db);
  const password = await resolvePassword(opts, prompt, nonInteractive, settings);
  if (!password) {
    if (!settings.password) {
      updateSettings(db, { requireLogin: false });
    }
    if (!nonInteractive) {
      printInfo("Password setup skipped. Dashboard login remains disabled.");
    }
    return false;
  }

  if (password.length < 8) {
    throw new Error("Password must be at least 8 characters.");
  }

  const hashedPassword = await hashManagementPassword(password);
  updateSettings(db, {
    password: hashedPassword,
    requireLogin: true,
  });
  printSuccess("Admin password configured");
  return true;
}

// FreeRoute does not keep provider keys, so setup never asks for one and never
// writes one. An explicit --add-provider / --provider / --api-key is refused; the
// interactive flow just says where provider keys are added.
function setupProvider(opts) {
  if (wantsProviderSetup(opts)) return { refused: true };
  printInfo(KEYS_MANAGED_BY_OPENVAULT_MESSAGE);
  return { refused: false };
}

/**
 * Merge the `setup` subcommand options with the program-level ones.
 *
 * The program declares a global `--api-key` (the FreeRoute *server* key, see
 * bin/cli/program.mjs) and `setup` declares its own `--api-key` (the *provider*
 * key). Commander binds the value to the program-level option, so the
 * subcommand's `opts.apiKey` is always `undefined` and `--add-provider` failed
 * with "Provider API key is required" even when `--api-key` was passed. Falling
 * back to the global value also makes `OMNIROUTE_API_KEY` work, which the error
 * message already told users to use.
 *
 * @param {Record<string, unknown>} opts Subcommand options.
 * @param {Record<string, unknown>} globalOpts Result of `cmd.optsWithGlobals()`.
 * @returns {Record<string, unknown>} Options to hand to `runSetupCommand`.
 */
export function mergeSetupOptions(opts, globalOpts) {
  return {
    ...opts,
    apiKey: opts.apiKey ?? globalOpts.apiKey,
    output: globalOpts.output,
  };
}

export function registerSetup(program) {
  program
    .command("setup")
    .description(t("setup.title"))
    .option("--password <value>", "Set admin password")
    .option("--add-provider", "Refused: provider keys are stored in OpenVault, not in FreeRoute")
    .option("--provider <id>", "Refused: provider keys are stored in OpenVault, not in FreeRoute")
    .option("--provider-name <name>", "Unused: provider connections are configured in OpenVault")
    .option("--api-key <value>", "Unused here: FreeRoute never stores a provider key")
    .option("--default-model <model>", "Unused: provider connections are configured in OpenVault")
    .option("--provider-base-url <url>", "Unused: provider connections are configured in OpenVault")
    .option("--test-provider", "Unused: no provider is saved by setup")
    .option("--non-interactive", "Read all inputs from flags/env and do not prompt")
    .option("--list", "List all supported CLI tools")
    .action(async (opts, cmd) => {
      const globalOpts = cmd.optsWithGlobals();
      const exitCode = await runSetupCommand(mergeSetupOptions(opts, globalOpts));
      if (exitCode !== 0) process.exit(exitCode);
    });

  // Wire up `freeroute setup opencode` subcommand. Kept inside registerSetup
  // so it always travels with the parent command (avoids a separate register
  // call in the registry that would silently break if the parent renames).
  registerSetupOpenCode(program.commands.find((c) => c.name() === "setup"));
}

export async function runSetupCommand(opts = {}) {
  if (opts.list) {
    const listCliTools = await getListCliTools();
    const tools = listCliTools();
    if (opts.json || opts.output === "json") {
      console.log(JSON.stringify(tools, null, 2));
    } else {
      printHeading("Supported CLI Tools");
      for (const tool of tools) {
        const cmd = tool.defaultCommand || tool.defaultCommands?.[0] || "";
        const cmdStr = cmd ? `  \x1b[2m(${cmd})\x1b[0m` : "";
        console.log(`  • ${tool.name}${cmdStr}`);
      }
    }
    return 0;
  }

  const nonInteractive = opts.nonInteractive ?? false;
  const prompt = createPrompt();

  try {
    printHeading("FreeRoute Setup");
    const { db, dbPath } = await openOmniRouteDb();
    printInfo(`Database: ${dbPath}`);

    const passwordChanged = await setupPassword(db, opts, prompt, nonInteractive);
    const provider = setupProvider(opts);

    updateSettings(db, { setupComplete: true });
    const after = getSettings(db);
    db.close();

    console.log("");
    printSuccess("Setup complete");
    printInfo(
      `Login: ${after.requireLogin === true ? "enabled" : "disabled"}${
        passwordChanged ? " (password updated)" : ""
      }`
    );
    // An explicit provider-key request is refused (non-zero exit) after the rest
    // of setup, which has nothing to do with keys, has finished.
    return provider.refused ? refuseLocalKeyWrite() : 0;
  } finally {
    prompt.close();
  }
}
