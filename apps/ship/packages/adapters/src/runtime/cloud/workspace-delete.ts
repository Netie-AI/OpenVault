// Modified by Netie AI, 2026: FreeBuild naming; upstream names and links removed from shipped text.
import { setTimeout as delay } from "node:timers/promises";
import type { WorkspaceHandle } from "oblien";

/** An accepted deletion is still running on Oblien. Keep local cleanup pending
 * until the scoped resource is absent; a failed read must not erase ownership. */
export async function deleteCloudWorkspace(
  workspace: Pick<WorkspaceHandle, "delete" | "get">,
  options: { timeoutMs?: number } = {},
): Promise<void> {
  try {
    const result = await workspace.delete();
    if (result?.accepted !== true) return;
  } catch (error) {
    if ((error as { status?: number })?.status === 404) return;
    throw error;
  }

  const deadline = Date.now() + (options.timeoutMs ?? 60_000);
  while (true) {
    try { await workspace.get(); }
    catch (error) {
      if ((error as { status?: number })?.status === 404) return;
      throw error;
    }
    if (Date.now() >= deadline) {
      throw new Error("The cloud provider is still deleting the workspace. Retry cleanup to confirm removal.");
    }
    await delay(Math.min(1000, Math.max(1, deadline - Date.now())));
  }
}
