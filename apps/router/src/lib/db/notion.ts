import { getDbInstance } from "./core";

const NOTION_NAMESPACE = "notion";
const NOTION_TOKEN_KEY = "integration_token";

type KeyValueRow = {
  value?: string;
};

// FreeRoute: no writer. The Notion token lives in OpenVault; this module only
// reads and clears a copy an older build stored (see scrubLocalSecrets.ts).
export function getNotionToken(): string | null {
  try {
    const db = getDbInstance();
    const row = db
      .prepare("SELECT value FROM key_value WHERE namespace = ? AND key = ?")
      .get(NOTION_NAMESPACE, NOTION_TOKEN_KEY) as KeyValueRow | undefined;
    return typeof row?.value === "string" ? JSON.parse(row.value) : null;
  } catch {
    return null;
  }
}

export function clearNotionToken(): void {
  try {
    const db = getDbInstance();
    db.prepare("DELETE FROM key_value WHERE namespace = ? AND key = ?").run(
      NOTION_NAMESPACE,
      NOTION_TOKEN_KEY
    );
  } catch {
    // Non-fatal.
  }
}

export function getNotionConfig(): { token: string | null; connected: boolean } {
  const token = getNotionToken();
  return { token, connected: token !== null && token.length > 0 };
}
