import { pgTable, text, integer, timestamp, index } from "drizzle-orm/pg-core";
import { user } from "./auth";
import { servers } from "./servers";

// ─── Terminal sessions ───────────────────────────────────────────────────────

/**
 * Audit log of interactive SSH terminal sessions opened from the dashboard.
 *
 * One row per session, written at open + finalized at close. Stores ONLY
 * session metadata - never keystrokes, never PTY bytes. Used for:
 *   - Per-user concurrent session cap (`countActiveByUser` for the
 *     handshake authorization check).
 *   - Operational forensics ("when did $user touch $server?").
 *
 * `endedAt = NULL` means the session is currently active. The session
 * manager finalizes the row on WS close / shell exit / timeout, but a
 * crashed API leaves rows with endedAt=NULL behind. A boot-time sweep in
 * the session manager closes any orphaned rows with reason='server_error'.
 *
 * ON DELETE CASCADE on both FKs - dropping a user or server drops their
 * audit trail (matches the rest of the schema and avoids dangling FKs).
 */
export const terminalSessions = pgTable(
  "terminal_sessions",
  {
    id: text("id")
      .primaryKey()
      .$defaultFn(() => crypto.randomUUID()),

    userId: text("user_id")
      .notNull()
      .references(() => user.id, { onDelete: "cascade" }),
    serverId: text("server_id")
      .notNull()
      .references(() => servers.id, { onDelete: "cascade" }),

    startedAt: timestamp("started_at").notNull().defaultNow(),
    endedAt: timestamp("ended_at"),

    /** Remote shell exit code (null when the session ended for a non-exit reason). */
    exitCode: integer("exit_code"),
    /**
     * Why the session ended:
     *   - "client_close"   → user closed the tab / disconnected cleanly
     *   - "remote_exit"    → remote shell process exited (typed `exit`)
     *   - "idle_timeout"   → no client input for TERMINAL_IDLE_TIMEOUT_MS
     *   - "session_cap"    → reached TERMINAL_HARD_CAP_MS
     *   - "max_sessions"   → rejected at handshake (concurrent cap)
     *   - "server_error"   → ssh failure / process crash / orphaned row
     */
    exitReason: text("exit_reason"),

    /** Client IP captured at handshake (best-effort, may be a proxy address). */
    clientIp: text("client_ip"),
    userAgent: text("user_agent"),
  },
  (t) => [
    index("terminal_sessions_user_idx").on(t.userId, t.startedAt),
    index("terminal_sessions_server_idx").on(t.serverId, t.startedAt),
  ],
);
