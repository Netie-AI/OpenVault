// FreeRoute: `freeroute update` is disabled. The upstream command queried npm
// for the upstream package and installed it globally, which would replace
// FreeRoute with the upstream product. FreeRoute ships and updates as part of
// OpenVault, so this answers with the same named code the server returns
// (upstream_updates_disabled, open-sse/netie/policy.ts).
import { printError } from "../io.mjs";

export const UPSTREAM_UPDATES_DISABLED_CODE = "upstream_updates_disabled";
export const UPSTREAM_UPDATES_DISABLED_MESSAGE =
  "Checking for, downloading, or installing upstream releases is disabled in this " +
  "edition of FreeRoute. FreeRoute ships and updates as part of OpenVault.";

export function registerUpdate(program) {
  program
    .command("update")
    .description("Disabled in this edition. FreeRoute updates ship with OpenVault.")
    .option("--check", "Disabled in this edition")
    .option("--apply", "Disabled in this edition")
    .option("--changelog", "Disabled in this edition")
    .option("--dry-run", "Disabled in this edition")
    .option("--no-backup", "Disabled in this edition")
    .option("--yes", "Disabled in this edition")
    .action(async (opts, cmd) => {
      const globalOpts = cmd.optsWithGlobals();
      const exitCode = await runUpdateCommand({ ...opts, output: globalOpts.output });
      if (exitCode !== 0) process.exit(exitCode);
    });
}

export async function runUpdateCommand(opts = {}) {
  if (opts.output === "json") {
    console.log(
      JSON.stringify({
        error: { code: UPSTREAM_UPDATES_DISABLED_CODE, message: UPSTREAM_UPDATES_DISABLED_MESSAGE },
      })
    );
  } else {
    printError(`${UPSTREAM_UPDATES_DISABLED_CODE}: ${UPSTREAM_UPDATES_DISABLED_MESSAGE}`);
  }
  return 1;
}
