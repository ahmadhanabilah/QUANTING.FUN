import { useCallback, useEffect, useState } from "react";
import { ActivityRow } from "../components/activities-table";

const PAGE_LIMIT = 10;

type UsePaginatedActivitiesArgs = {
  apiBase: string;
  authHeaders: Record<string, string>;
  mode: "live" | "test";
  botId?: string;
};

export function usePaginatedActivities({ apiBase, authHeaders, mode, botId }: UsePaginatedActivitiesArgs) {
  const [activities, setActivities] = useState<ActivityRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [offset, setOffset] = useState(0);
  const [hasMore, setHasMore] = useState(true);
  const [isLoadingMore, setIsLoadingMore] = useState(false);

  const fetchPage = useCallback(
    async (targetOffset: number, append: boolean, showLoader = true) => {
      if (!apiBase) {
        return;
      }
      if (append) {
        setIsLoadingMore(true);
      } else {
        if (showLoader) {
          setLoading(true);
          setError("");
        }
      }
      try {
        const params = new URLSearchParams();
        params.set("mode", mode);
        params.set("limit", PAGE_LIMIT.toString());
        params.set("offset", targetOffset.toString());
        if (botId) {
          params.set("botId", botId);
        }
        const res = await fetch(`${apiBase}/api/tt/activities?${params.toString()}`, {
          headers: authHeaders,
        });
        if (!res.ok) {
          const text = await res.text();
          throw new Error(text || "Failed to load activities");
        }
        const data = await res.json();
        const rows = Array.isArray(data?.rows) ? data.rows : [];
        setActivities((prev) => (append ? [...prev, ...rows] : rows));
        setOffset(targetOffset + rows.length);
        setHasMore(rows.length === PAGE_LIMIT);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load activities");
        if (!append && showLoader) {
          setActivities([]);
          setOffset(0);
          setHasMore(false);
        }
      } finally {
        if (append) {
          setIsLoadingMore(false);
        } else {
          if (showLoader) {
            setLoading(false);
          }
        }
      }
    },
    [apiBase, authHeaders, botId, mode],
  );

  const loadMore = useCallback(() => {
    if (isLoadingMore || !hasMore) {
      return;
    }
    fetchPage(offset, true);
  }, [fetchPage, hasMore, isLoadingMore, offset]);

  const refresh = useCallback(() => {
    setHasMore(true);
    fetchPage(0, false, true);
  }, [fetchPage]);

  const refreshSilent = useCallback(() => {
    setHasMore(true);
    fetchPage(0, false, false);
  }, [fetchPage]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return {
    activities,
    loading,
    error,
    hasMore,
    isLoadingMore,
    loadMore,
    refresh,
    refreshSilent,
  };
}
