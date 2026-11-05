import React, { useState } from "react";
import { render, Box, Text, useInput } from "ink";
import { MenuSelect } from "../tui-components/MenuSelect.jsx";

function InterfaceMenuApp({ version, baseUrl, onChoice }) {
  return (
    <Box flexDirection="column" paddingX={2} paddingY={1}>
      <Box borderStyle="double" borderColor="cyan" paddingX={2} paddingY={1} flexDirection="column">
        <Text bold color="cyan">
          ⚡ FreeRoute {version ? `v${version}` : ""}
        </Text>
        <Text dimColor>{baseUrl}</Text>
      </Box>
      {/* FreeRoute ships and updates as part of OpenVault, so there is no update line. */}
      <Box marginTop={1}>
        <MenuSelect
          items={[
            { label: "🌐 Open Web UI in Browser", hint: "(default)" },
            { label: "💻 Interactive TUI Dashboard" },
            { label: "🔔 Start in Background (daemon)" },
            { label: "📊 Show Live Logs" },
            { label: "🚪 Exit" },
          ]}
          onSelect={(item) => onChoice(item.label)}
        />
      </Box>
      <Box marginTop={1}>
        <Text dimColor>[↑↓] navigate [Enter] select [1-5] shortcut [q] exit</Text>
      </Box>
    </Box>
  );
}

export async function showInterfaceMenu({ version, baseUrl } = {}) {
  return new Promise((resolve) => {
    const { unmount } = render(
      <InterfaceMenuApp
        version={version}
        baseUrl={baseUrl}
        onChoice={(choice) => {
          unmount();
          resolve(choice);
        }}
      />
    );
  });
}
