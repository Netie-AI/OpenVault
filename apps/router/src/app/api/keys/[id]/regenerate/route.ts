import { requireManagementAuth } from "@/lib/api/requireManagementAuth";
import { renderClientKeysManagedByOpenVault } from "@omniroute/open-sse/netie/policy.ts";

/**
 * POST /api/keys/[id]/regenerate
 *
 * FreeRoute: router-side inbound-client keys are minted by OpenVault
 * (POST http://127.0.0.1:5000/api/apikeys, surfaced at
 * http://127.0.0.1:3010/keys) — see src/lib/netie/keyvault.ts. Regenerating
 * one here would just create a second, locally-minted key OpenVault doesn't
 * know about, so this always 501s instead of calling regenerateApiKey().
 */
export async function POST(request: Request) {
  const authError = await requireManagementAuth(request);
  if (authError) return authError;
  return renderClientKeysManagedByOpenVault();
}
