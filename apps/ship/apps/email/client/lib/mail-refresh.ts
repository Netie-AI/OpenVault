import {
  focusManager,
  onlineManager,
  type QueryClient,
  type QueryKey,
} from "@tanstack/react-query";
import type { createStore } from "jotai";
import { optimisticActionsAtom } from "../store/optimistic-updates";
import { backgroundQueueAtom } from "../store/backgroundQueue";

/** One subscription per mail layout, shared by every list/row query observer. */
export function startMailRefresh(
  queryClient: QueryClient,
  store: ReturnType<typeof createStore>,
  queryKey: QueryKey,
) {
  const lists = { queryKey, type: "active" } as const;
  let refreshing = false;
  let stopped = false;

  const hasPendingActions = () =>
    Object.keys(store.get(optimisticActionsAtom)).length > 0 ||
    store.get(backgroundQueueAtom).length > 0 ||
    queryClient.isMutating() > 0;

  const refresh = () => {
    if (
      stopped ||
      refreshing ||
      !focusManager.isFocused() ||
      !onlineManager.isOnline() ||
      hasPendingActions() ||
      queryClient.isFetching(lists) > 0
    ) {
      return;
    }

    refreshing = true;
    // Keep every loaded page and its current folder/search/label inputs. Do
    // not interrupt pagination or refetch inactive folders and message bodies.
    void queryClient.refetchQueries(lists, { cancelRefetch: false }).finally(() => {
      refreshing = false;
    });
  };

  const cancelDuringAction = () => {
    if (refreshing && hasPendingActions()) {
      // An action may start AFTER a poll. Discard that older response without
      // reverting cache patches, including a polled folder just navigated away from.
      void queryClient.cancelQueries({ queryKey });
    }
  };

  const unsubscribe = [
    store.sub(optimisticActionsAtom, cancelDuringAction),
    store.sub(backgroundQueueAtom, cancelDuringAction),
    queryClient.getMutationCache().subscribe(cancelDuringAction),
    focusManager.subscribe((focused) => {
      if (focused) refresh();
    }),
    onlineManager.subscribe((online) => {
      if (online) refresh();
    }),
  ];
  const timer = setInterval(refresh, 30_000);

  return () => {
    stopped = true;
    clearInterval(timer);
    unsubscribe.forEach((stop) => stop());
    if (refreshing) void queryClient.cancelQueries({ queryKey });
  };
}
