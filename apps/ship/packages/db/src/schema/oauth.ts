import { pgTable, text, timestamp, boolean, index } from "drizzle-orm/pg-core";
import { user } from "./auth";

/**
 * Better Auth `mcp` / `oidc-provider` plugin tables — the OAuth 2.1
 * authorization-server state that backs standards-compliant MCP connections.
 *
 * Column names + types must match the plugin's model fields (the JS property
 * keys are the Better Auth field names; the SQL column names are snake_case).
 * See `better-auth/dist/plugins/oidc-provider/schema`. Managed entirely by the
 * plugin at runtime — openship never writes these directly.
 *
 * NOTE: `refreshToken`/`refreshTokenExpiresAt` are nullable — not every grant
 * issues a refresh token; Postgres unique indexes allow multiple NULLs.
 */

/** A registered OAuth client (an MCP app; usually created via dynamic registration). */
export const oauthApplication = pgTable(
  "oauth_application",
  {
    id: text("id").primaryKey(),
    name: text("name").notNull(),
    icon: text("icon"),
    metadata: text("metadata"), // JSON-stringified blob the plugin manages
    clientId: text("client_id").notNull().unique(),
    clientSecret: text("client_secret"),
    redirectUrls: text("redirect_urls").notNull(),
    type: text("type").notNull(), // "public" | "web" | "native" | "user-agent-based"
    disabled: boolean("disabled").notNull().default(false),
    userId: text("user_id").references(() => user.id, { onDelete: "cascade" }),
    createdAt: timestamp("created_at").notNull().defaultNow(),
    updatedAt: timestamp("updated_at").notNull().defaultNow(),
  },
  (t) => [index("oauth_application_user_idx").on(t.userId)],
);

/** An issued OAuth access/refresh token pair bound to a client + user. */
export const oauthAccessToken = pgTable(
  "oauth_access_token",
  {
    id: text("id").primaryKey(),
    accessToken: text("access_token").notNull().unique(),
    refreshToken: text("refresh_token").unique(),
    accessTokenExpiresAt: timestamp("access_token_expires_at").notNull(),
    refreshTokenExpiresAt: timestamp("refresh_token_expires_at"),
    clientId: text("client_id")
      .notNull()
      .references(() => oauthApplication.clientId, { onDelete: "cascade" }),
    userId: text("user_id").references(() => user.id, { onDelete: "cascade" }),
    scopes: text("scopes").notNull(),
    createdAt: timestamp("created_at").notNull().defaultNow(),
    updatedAt: timestamp("updated_at").notNull().defaultNow(),
  },
  (t) => [
    index("oauth_access_token_client_idx").on(t.clientId),
    index("oauth_access_token_user_idx").on(t.userId),
  ],
);

/** A user's recorded consent for a client + scope set (skips re-prompting). */
export const oauthConsent = pgTable(
  "oauth_consent",
  {
    id: text("id").primaryKey(),
    clientId: text("client_id")
      .notNull()
      .references(() => oauthApplication.clientId, { onDelete: "cascade" }),
    userId: text("user_id")
      .notNull()
      .references(() => user.id, { onDelete: "cascade" }),
    scopes: text("scopes").notNull(),
    consentGiven: boolean("consent_given").notNull(),
    createdAt: timestamp("created_at").notNull().defaultNow(),
    updatedAt: timestamp("updated_at").notNull().defaultNow(),
  },
  (t) => [
    index("oauth_consent_client_idx").on(t.clientId),
    index("oauth_consent_user_idx").on(t.userId),
  ],
);
