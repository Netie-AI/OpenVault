import bcrypt from "bcryptjs";
import { getSettings, updateSettings } from "@/lib/db/settings";

const BCRYPT_HASH_PATTERN = /^\$2[aby]\$\d{2}\$[./A-Za-z0-9]{53}$/;
const MANAGEMENT_PASSWORD_SALT_ROUNDS = 12;

// Well-known placeholder from old `.env` copies and deploy manifests (INITIAL_PASSWORD=CHANGEME).
// As INITIAL_PASSWORD it is treated as unset, so onboarding asks for a real password.
const INSECURE_DEFAULT_PASSWORDS = new Set(["CHANGEME"]);

type JsonRecord = Record<string, unknown>;

type MigrationSource = "stored_hash" | "stored_plaintext" | "env" | "missing";

interface EnsureManagementPasswordOptions {
  initialPassword?: string | null;
  logger?: Pick<Console, "log"> & Partial<Pick<Console, "warn">>;
  settings?: JsonRecord;
  source?: string;
}

export interface EnsuredManagementPassword {
  hash: string | null;
  migrated: boolean;
  settings: JsonRecord;
  source: MigrationSource;
}

function getInitialPasswordValue(value: string | null | undefined) {
  if (typeof value !== "string" || value.length === 0) return null;
  return INSECURE_DEFAULT_PASSWORDS.has(value.trim()) ? null : value;
}

// The placeholder also has to read as unset to code that only tests
// `process.env.INITIAL_PASSWORD` for truthiness (auth gate, setup wizard skip).
function discardPlaceholderInitialPasswordEnv(
  logger: EnsureManagementPasswordOptions["logger"]
): void {
  const fromEnv = process.env.INITIAL_PASSWORD;
  if (typeof fromEnv !== "string" || !INSECURE_DEFAULT_PASSWORDS.has(fromEnv.trim())) return;
  process.env.INITIAL_PASSWORD = "";
  const warn = logger?.warn?.bind(logger) ?? console.warn;
  warn(
    '[AUTH][SECURITY] INITIAL_PASSWORD is the well-known placeholder "CHANGEME". Ignoring it. ' +
      "Choose the dashboard password in onboarding, or set a different INITIAL_PASSWORD."
  );
}

export function getStoredManagementPassword(settings: JsonRecord | null | undefined) {
  return typeof settings?.password === "string" ? settings.password : "";
}

export function hasManagementPasswordConfigured(settings: JsonRecord | null | undefined) {
  return (
    getStoredManagementPassword(settings).length > 0 ||
    getInitialPasswordValue(process.env.INITIAL_PASSWORD) !== null
  );
}

export function isBcryptHash(value: unknown): value is string {
  return typeof value === "string" && BCRYPT_HASH_PATTERN.test(value);
}

// #13679 (PR D, item #5): the well-known INITIAL_PASSWORD placeholder shipped in
// .env.example / contrib/podman/omniroute.container / docker deploy manifests is a
// public, guessable credential. Callers on the authentication path use this to refuse
// a successful match from non-loopback requests instead of only warning on boot.
export function isKnownInsecureManagementPassword(password: string): boolean {
  return INSECURE_DEFAULT_PASSWORDS.has(password);
}

export async function hashManagementPassword(password: string) {
  return bcrypt.hash(password, MANAGEMENT_PASSWORD_SALT_ROUNDS);
}

export async function verifyManagementPassword(password: string, hash: string) {
  if (!isBcryptHash(hash)) return false;
  return bcrypt.compare(password, hash);
}

export async function ensurePersistentManagementPasswordHash(
  options: EnsureManagementPasswordOptions = {}
): Promise<EnsuredManagementPassword> {
  const settings = options.settings ?? ((await getSettings()) as JsonRecord);
  const storedPassword = getStoredManagementPassword(settings);

  if (isBcryptHash(storedPassword)) {
    return {
      hash: storedPassword,
      migrated: false,
      settings,
      source: "stored_hash",
    };
  }

  discardPlaceholderInitialPasswordEnv(options.logger);

  if (storedPassword && INSECURE_DEFAULT_PASSWORDS.has(storedPassword)) {
    const warn = options.logger?.warn?.bind(options.logger) ?? console.warn;
    warn(
      '[AUTH][SECURITY] The stored management password is the well-known placeholder "CHANGEME". ' +
        "Change it in the dashboard."
    );
  }

  const bootstrapPassword =
    storedPassword ||
    getInitialPasswordValue(options.initialPassword ?? process.env.INITIAL_PASSWORD);

  if (!bootstrapPassword) {
    return {
      hash: null,
      migrated: false,
      settings,
      source: "missing",
    };
  }

  const passwordHash = await hashManagementPassword(bootstrapPassword);
  const updates: JsonRecord = { password: passwordHash };

  if (settings.setupComplete !== true) {
    updates.setupComplete = true;
  }
  if (!storedPassword) {
    updates.requireLogin = true;
  }

  const nextSettings = (await updateSettings(updates)) as JsonRecord;
  if (options.logger) {
    const context = options.source ? ` during ${options.source}` : "";
    const migrationSource = storedPassword ? "stored plaintext password" : "INITIAL_PASSWORD";
    options.logger.log(`[AUTH] Migrated ${migrationSource} to bcrypt hash${context}`);
  }

  return {
    hash: getStoredManagementPassword(nextSettings) || passwordHash,
    migrated: true,
    settings: nextSettings,
    source: storedPassword ? "stored_plaintext" : "env",
  };
}
