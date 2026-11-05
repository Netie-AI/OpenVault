/**
 * Self-hosted shim for upstream Zero's Autumn-based billing hook.
 *
 * The forked Zero codebase has feature gates wired through autumn-js for
 * chat messages, connections, and brain activity. None of that applies to
 * a self-hosted openship deploy - everything is unlimited, always.
 *
 * Keep the hook's shape (callers across the UI destructure these fields)
 * but make every limit "unlimited" and every action a no-op.
 */

type FeatureState = {
  total: number;
  remaining: number;
  unlimited: boolean;
  enabled: boolean;
  usage: number;
  nextResetAt: number | null;
  interval: string;
  included_usage: number;
};

const UNLIMITED: FeatureState = {
  total: 0,
  remaining: 0,
  unlimited: true,
  enabled: true,
  usage: 0,
  nextResetAt: null,
  interval: '',
  included_usage: 0,
};

const noop = async () => undefined;

export const useBilling = () => ({
  isLoading: false,
  customer: null,
  refetch: noop,
  attach: noop,
  track: noop,
  openBillingPortal: noop,
  isPro: true,
  chatMessages: UNLIMITED,
  connections: UNLIMITED,
  brainActivity: UNLIMITED,
});
