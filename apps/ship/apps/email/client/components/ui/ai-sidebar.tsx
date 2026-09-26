/**
 * AI sidebar stub. The legacy SaaS build shipped a Cloudflare-Workers-backed
 * agent + Anthropic chat panel; self-host strips the LLM dependency. The
 * hooks and component are kept as no-ops so existing callsites in
 * mail.tsx / thread-display.tsx / app-sidebar.tsx continue to compile.
 */

export function useAIFullScreen(): { isFullScreen: boolean; setIsFullScreen: (v: boolean) => void } {
  return { isFullScreen: false, setIsFullScreen: () => {} };
}

export function useAISidebar(): {
  open: boolean;
  setOpen: (v: boolean) => void;
  toggleOpen: () => void;
} {
  return { open: false, setOpen: () => {}, toggleOpen: () => {} };
}

export default function AISidebar(): null {
  return null;
}
