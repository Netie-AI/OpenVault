import { useTRPC } from '@/providers/query-provider';
import { useQuery } from '@tanstack/react-query';

export function useEmailAliases() {
  const trpc = useTRPC();
  const emailAliasesQuery = useQuery(
    trpc.mail.getEmailAliases.queryOptions(void 0, {
      initialData: [] as { email: string; name: string; primary: boolean }[],
      // Aliases are essentially static per session - they're the user's
      // own from-addresses configured on the mail account. Without these
      // flags, every component that mounts useEmailAliases (settings,
      // reply-composer, email-composer, create-email) refetches on
      // mount, producing N duplicate `mail.getEmailAliases` calls.
      staleTime: Infinity,
      refetchOnMount: false,
      refetchOnWindowFocus: false,
    }),
  );
  return emailAliasesQuery;
}
