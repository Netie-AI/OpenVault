import { useTRPC } from '@/providers/query-provider';
import { useQuery } from '@tanstack/react-query';
import { useMemo } from 'react';

const desiredSystemLabels = new Set([
  'IMPORTANT',
  'FORUMS',
  'PROMOTIONS',
  'SOCIAL',
  'UPDATES',
  'STARRED',
  'UNREAD',
]);

// IMAP system keywords (RFC 5788 / vendor-namespaced) - e.g. $Important,
// $Snoozed, $Forwarded, $Junk, $NotJunk, $MDNSent - are NOT user labels
// even though they live in the same `permanentFlags` list. The server
// filters them out too, but we re-filter client-side so the sidebar
// doesn't show stale `$Important`-style entries from any cached labels
// query before the next fetch lands.
function isSystemKeyword(name: string): boolean {
  return name.startsWith('$');
}

export function useLabels() {
  const trpc = useTRPC();
  const labelQuery = useQuery(
    trpc.labels.list.queryOptions(void 0, {
      // Short stale window so server-side label changes (e.g. our $-prefix
      // filter) propagate fast. 1h was masking the fix until next reload.
      staleTime: 1000 * 60, // 1 minute
    }),
  );

  const { userLabels, systemLabels } = useMemo(() => {
    if (!labelQuery.data) return { userLabels: [], systemLabels: [] };
    const cleanedName = labelQuery.data
      .filter((label) => label.type === 'system')
      .map((label) => {
        return {
          ...label,
          name: label.name.replace('CATEGORY_', ''),
        };
      });
    const cleanedSystemLabels = cleanedName.filter((label) => desiredSystemLabels.has(label.name));
    return {
      userLabels: labelQuery.data
        .filter((label) => label.type === 'user')
        .filter((label) => !isSystemKeyword(label.name) && !isSystemKeyword(label.id)),
      systemLabels: cleanedSystemLabels,
    };
  }, [labelQuery.data]);

  return { userLabels, systemLabels, ...labelQuery };
}

export function useThreadLabels(ids: string[]) {
  const { userLabels: labels = [] } = useLabels();

  const threadLabels = useMemo(() => {
    if (!labels) return [];
    return labels.filter((label) => (label.id ? ids.includes(label.id) : false));
  }, [labels, ids]);

  return { labels: threadLabels };
}
