// Modified by Netie AI, 2026: values an earlier build sealed under the public placeholder
// secret still open (fallbackSecrets), now that BETTER_AUTH_SECRET is a per-install secret.
/** API process composition over the shared encryption implementation. */
import { createEncryption, type Encryption } from "@repo/platform";
import { DEFAULT_ENCRYPTION_SECRET } from "@repo/db/encryption";
import { env } from "../config/env";
export { encryptWithKey, decryptWithKey, encryptBytesWithKey, decryptBytesWithKey } from "@repo/platform";
let secrets: Encryption | undefined;
function instanceSecrets(): Encryption { return secrets ??= createEncryption(env.BETTER_AUTH_SECRET, { fallbackSecrets: [DEFAULT_ENCRYPTION_SECRET] }); }
export const encrypt = (plaintext: string) => instanceSecrets().encrypt(plaintext);
export const decrypt = (sealed: string) => instanceSecrets().decrypt(sealed);
export const decryptEnvMap: Encryption["decryptEnvMap"] = (...args) => instanceSecrets().decryptEnvMap(...args);
export function closeEncryption(): void { secrets?.close(); secrets = undefined; }
