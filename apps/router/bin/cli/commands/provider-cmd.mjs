export function registerProvider(program) {
  program
    .command("provider [subcommand]")
    .description("Manage provider connections (use 'providers' for the full interface)")
    .allowUnknownOption()
    .allowExcessArguments()
    .action(() => {
      console.log(`
  Use \`freeroute providers\` for the full provider management interface:

    freeroute providers available   — show provider catalog
    freeroute providers list        — list configured connections
    freeroute providers test <name> — test a provider connection
    freeroute providers test-all    — test all active connections
    freeroute providers validate    — validate local configuration
    freeroute providers add <id>    — add an API-key connection
    freeroute providers auth <id>   — start an existing OAuth flow
    freeroute providers remove <id> — remove a connection (requires confirmation)
`);
    });
}
