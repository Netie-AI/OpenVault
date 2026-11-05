/**
 * `freeroute login`: disabled in this edition.
 *
 * The upstream helper ran a consumer-subscription OAuth login (the embedded
 * Antigravity desktop client) on the operator's machine and printed or pushed a
 * refresh-token blob. FreeRoute does not pool consumer subscription accounts, so
 * the command stops at start with the named 501 text and a non-zero exit. No
 * listener is opened, no browser is launched and nothing contacts Google or a
 * FreeRoute server.
 *
 * The code and text match `consumer_subscription_pooling_disabled` in
 * open-sse/netie/policy.ts and src/lib/netie/hardDisabledRoutes.ts. They are
 * repeated here because the CLI must not load the provider registry to print a
 * refusal.
 */

export const LOGIN_DISABLED_CODE = "consumer_subscription_pooling_disabled";
export const LOGIN_DISABLED_MESSAGE =
  "Pooling a consumer subscription account (e.g. Claude Code, Codex/ChatGPT, Cursor, " +
  "GitHub Copilot, Antigravity, Kiro, Kimi Coding, Trae, Devin) is disabled in this " +
  "edition of FreeRoute. Connect this provider with a real API key instead.";

/** Print the named 501 refusal and exit non-zero. Streams and exit are injectable for tests. */
export function runLoginDisabled(provider, deps = {}) {
  const write = deps.write ?? ((text) => process.stderr.write(text));
  const exit = deps.exit ?? ((code) => process.exit(code));
  write(`error: ${LOGIN_DISABLED_CODE} (501): ${LOGIN_DISABLED_MESSAGE}\n`);
  exit(1);
  return { provider, code: LOGIN_DISABLED_CODE };
}

export function registerLogin(program) {
  const login = program
    .command("login")
    .description("Consumer subscription sign-in helpers (disabled in this edition)");

  login
    .command("antigravity")
    .description("Disabled: consumer subscription login (answers consumer_subscription_pooling_disabled)")
    .allowUnknownOption(true)
    .argument("[ignored...]", "ignored")
    .action(() => {
      runLoginDisabled("antigravity");
    });
}
