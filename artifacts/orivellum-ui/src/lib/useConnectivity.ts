/**
 * useConnectivity — shared connectivity state with browser online/offline events.
 *
 * Single source of truth for API and AI reachability across the whole app.
 * Wraps TanStack Query health polling + listens to browser online/offline events
 * so the UI reacts immediately when the laptop wakes or WiFi changes.
 */
import { useEffect, useCallback } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  useGetSystemHealth,
  getGetSystemHealthQueryKey,
} from "@workspace/api-client-react";

const HEALTH_QUERY_KEY = getGetSystemHealthQueryKey();
const POLL_INTERVAL_MS = 15_000;

export function useConnectivity() {
  const queryClient = useQueryClient();

  const { data, isError, isFetching } = useGetSystemHealth({
    query: {
      queryKey: HEALTH_QUERY_KEY,
      refetchInterval: POLL_INTERVAL_MS,
      staleTime: 10_000,
      retry: false,
    },
  });

  const recheckNow = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: HEALTH_QUERY_KEY });
  }, [queryClient]);

  const markOffline = useCallback(() => {
    // Immediately write a failed health value into the cache without a fetch
    queryClient.setQueryData(HEALTH_QUERY_KEY, undefined);
    queryClient.invalidateQueries({ queryKey: HEALTH_QUERY_KEY });
  }, [queryClient]);

  // React to browser network events immediately
  useEffect(() => {
    const handleOnline  = () => recheckNow();
    const handleOffline = () => markOffline();

    window.addEventListener("online",  handleOnline);
    window.addEventListener("offline", handleOffline);
    return () => {
      window.removeEventListener("online",  handleOnline);
      window.removeEventListener("offline", handleOffline);
    };
  }, [recheckNow, markOffline]);

  const apiReachable = !isError;
  const aiReachable  = !isError && (data?.services as any)?.ai?.status === "ok";
  const ok           = !isError && data?.status === "ok";

  return { data, ok, apiReachable, aiReachable, isError, isFetching, recheckNow };
}

/** Export the query key so consumers can subscribe to the same cache entry. */
export { HEALTH_QUERY_KEY };
