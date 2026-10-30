import { useDoState } from '@/components/mail/use-do-state';

export const useStats = () => {
  const [doState] = useDoState();
  // refetch is a no-op for now - counts come from a local DO state, not a
  // remote query - but consumers expect the React Query-style shape.
  return { data: doState.counts, refetch: async () => undefined };
};
