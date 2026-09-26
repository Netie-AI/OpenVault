/**
 * Keyboard shortcut config. The client renders these in the help
 * sheet; the server stores per-user overrides in `user_hotkeys`.
 *
 * `ShortcutEntry` shape is intentionally permissive - the client owns
 * the rendering and we just round-trip whatever it sends.
 */

export interface ShortcutEntry {
  /** Stable id, e.g. `mail.archive`. */
  id: string;
  /** Display label, e.g. "Archive thread". */
  label: string;
  /** Section header, e.g. "Mail". */
  group: string;
  /** Default key combo (e.g. `e` or `mod+enter`). */
  keys: string[];
  /** Per-user override (if set). */
  override?: string[] | null;
}

export const defaultShortcuts: ShortcutEntry[] = [
  { id: 'mail.archive', label: 'Archive thread', group: 'Mail', keys: ['e'] },
  { id: 'mail.delete', label: 'Move to trash', group: 'Mail', keys: ['#'] },
  { id: 'mail.markRead', label: 'Mark as read', group: 'Mail', keys: ['shift+i'] },
  { id: 'mail.markUnread', label: 'Mark as unread', group: 'Mail', keys: ['shift+u'] },
  { id: 'mail.star', label: 'Toggle star', group: 'Mail', keys: ['s'] },
  { id: 'mail.important', label: 'Toggle important', group: 'Mail', keys: ['shift+s'] },
  { id: 'mail.reply', label: 'Reply', group: 'Compose', keys: ['r'] },
  { id: 'mail.replyAll', label: 'Reply all', group: 'Compose', keys: ['a'] },
  { id: 'mail.forward', label: 'Forward', group: 'Compose', keys: ['f'] },
  { id: 'mail.compose', label: 'Compose', group: 'Compose', keys: ['c'] },
  { id: 'nav.inbox', label: 'Go to inbox', group: 'Navigation', keys: ['g', 'i'] },
  { id: 'nav.sent', label: 'Go to sent', group: 'Navigation', keys: ['g', 't'] },
  { id: 'nav.drafts', label: 'Go to drafts', group: 'Navigation', keys: ['g', 'd'] },
  { id: 'nav.starred', label: 'Go to starred', group: 'Navigation', keys: ['g', 's'] },
  { id: 'nav.search', label: 'Focus search', group: 'Navigation', keys: ['/'] },
];
