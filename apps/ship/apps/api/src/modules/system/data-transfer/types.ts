// Modified by Netie AI, 2026
/**
 * Wire types for instance and project data export / import.
 *
 * The export file wraps an UNCHANGED `DatabaseDump` (so restoreSubgraph's
 * format-version gate is untouched) plus a portable bundle of secret values.
 * Project files can carry these values as plain JSON; password-protected files
 * retain their sealed bundle. Imports encrypt values with the destination key.
 */

import type { DatabaseDump } from "@repo/db";
import type { ExportSelection, TransferManifest } from "@repo/core";
export type {
  ExportHistoryCategory,
  ExportSelection,
  ExportPreview,
  ImportSelection,
  ImportPreview,
  TransferManifest,
  TransferProject,
  TransferServer,
} from "@repo/core";

export type ImportMode = "wipe" | "merge";

/** How a given column is encrypted at rest — drives decrypt/re-encrypt dispatch. */
export type SecretScheme = "scalar" | "enc1" | "map" | "notification-config" | "plaintext" | "json";

/** One secret cell's plaintext, keyed to its row. Only one payload field is set. */
export interface SecretEntry {
  table: string; // sqlName
  id: string; // row primary key
  column: string; // drizzle field name
  scheme: SecretScheme;
  /** scalar | enc1 | plaintext */
  value?: string;
  /** map — e.g. deployment.envVars */
  map?: Record<string, string>;
  /** notification-config — decrypted secret sub-fields (hmacSecret, webhookUrl, botToken) */
  config?: Record<string, string>;
  /** JSON configuration that can contain literal passwords or private keys. */
  json?: unknown;
}

export interface SecretBundle {
  version: 1;
  entries: SecretEntry[];
}

/**
 * The secret bundle sealed for transport. `blob` is the app's standard
 * AES-256-GCM envelope (base64 iv||authTag||ciphertext) of
 * `JSON.stringify(SecretBundle)`, under a key = scrypt(passphrase, salt).
 */
export interface SealedSecrets {
  version: 1;
  kdf: { algo: "scrypt"; N: number; r: number; p: number; keyLen: number; salt: string };
  blob: string;
}

export interface PlaintextSecrets extends SecretBundle {
  encoding: "plaintext";
}

export type TransferSecrets = SealedSecrets | PlaintextSecrets;

/** Kinds this build writes into an export file. */
export type DataTransferKind = "freebuild-instance-export" | "freebuild-project-export";

/**
 * Kinds written by Openship and by earlier FreeBuild builds. They are still read, so an
 * old archive imports, and they are never written.
 */
export type LegacyDataTransferKind = "openship-instance-export" | "openship-project-export";

export function isProjectExportKind(kind: string): boolean {
  return kind === "freebuild-project-export" || kind === "openship-project-export";
}

export function isInstanceExportKind(kind: string): boolean {
  return kind === "freebuild-instance-export" || kind === "openship-instance-export";
}

export interface DataTransferFile {
  kind: DataTransferKind | LegacyDataTransferKind;
  envelopeVersion: 1 | 2 | 3;
  createdAt: string;
  sourceDriver: "pg" | "pglite";
  /** Absent on legacy files, which always contained all history groups. */
  selection?: ExportSelection;
  manifest?: TransferManifest;
  summary?: { rows: number; tables: number };
  dump: DatabaseDump;
  /** Version 3 project files support plaintext values. null omits credentials. */
  secrets: TransferSecrets | null;
}

export interface ImportResult {
  mode: ImportMode;
  rowsRestored: number;
  secretsRehydrated: number;
  /** true when the file had no secrets or restoring secrets was disabled. */
  secretsSkipped: boolean;
  /**
   * Projects whose source is a LOCAL FOLDER path (localPath / folder-upload).
   * That path is machine-specific — it points at the SOURCE machine and almost
   * certainly does not exist on this install (e.g. a Mac path imported onto a
   * Linux server). The next deploy of these projects can't find the folder until
   * you re-point localPath (or re-deploy from a folder on THIS machine). Warn-only
   * — we never guess a rewrite. Empty when nothing needs attention.
   */
  localPathProjects: Array<{ slug: string; localPath: string }>;
  warnings?: string[];
  projectsCreated?: number;
  projectsUpdated?: number;
  projectsSkipped?: number;
}

/** One-time capability copied from the destination to the source instance. */
export interface DirectTransferConnection {
  version: 1;
  apiBase: string;
  recipientRuntimeId: string;
  sessionId: string;
  token: string;
  recipientPublicKey: string;
  mode: ImportMode;
  expiresAt: string;
}

export interface DirectTransferEnvelope {
  version: 1;
  sessionId: string;
  senderPublicKey: string;
  blob: string;
}

export interface DirectTransferPayload {
  version: 1;
  authorizationToken: string;
  file: DataTransferFile;
  secrets: SecretBundle | null;
}

export interface DirectTransferResult extends ImportResult {
  destination: string;
}
