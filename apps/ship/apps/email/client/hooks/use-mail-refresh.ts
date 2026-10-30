import { useQueryClient } from "@tanstack/react-query";
import { useTRPC } from "@/providers/query-provider";
import { startMailRefresh } from "@/lib/mail-refresh";
import { useEffect } from "react";
import { useStore } from "jotai";

export function useMailRefresh(enabled: boolean) {
  const queryClient = useQueryClient();
  const store = useStore();
  const trpc = useTRPC();

  useEffect(() => {
    if (!enabled) return;
    return startMailRefresh(queryClient, store, trpc.mail.listThreads.pathKey());
  }, [enabled, queryClient, store, trpc]);
}
