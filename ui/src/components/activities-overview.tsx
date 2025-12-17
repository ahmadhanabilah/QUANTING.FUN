import { useEffect, useState } from "react";
import { ActivitiesTable } from "./activities-table";
import { usePaginatedActivities } from "../hooks/use-paginated-activities";

type ActivitiesOverviewProps = {
  apiBase: string;
  authHeaders: Record<string, string>;
  mode: "live" | "test";
};

export function ActivitiesOverview({ apiBase, authHeaders, mode }: ActivitiesOverviewProps) {
  const [overviewAtTop, setOverviewAtTop] = useState(true);
  const {
    activities,
    loading,
    error,
    hasMore,
    isLoadingMore,
    loadMore,
    refresh,
    refreshSilent,
  } = usePaginatedActivities({ apiBase, authHeaders, mode });

  useEffect(() => {
    const id = setInterval(() => {
      if (overviewAtTop) {
        refreshSilent();
      }
    }, 4000);
    return () => clearInterval(id);
  }, [overviewAtTop, refreshSilent]);

  return (
    <div className="bg-gradient-to-br from-slate-900 to-slate-900/50 border border-slate-800/50 rounded-xl p-6 shadow-xl space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-white text-lg">Global Activities</h2>
          <p className="text-slate-400 text-sm">Live decisions, trades, and fills across every bot.</p>
        </div>
      </div>
      {error && (
        <div className="px-4 py-3 rounded-lg border border-red-500/40 bg-red-500/10 text-red-300 text-sm">
          {error}
        </div>
      )}
      {loading && (
        <div className="px-4 py-6 text-slate-400 text-sm">Loading activity history…</div>
      )}
      {!loading && activities.length === 0 && (
        <div className="px-4 py-6 text-slate-400 text-sm">No trace records yet. Activity will appear after bots log decisions/trades/fills.</div>
      )}
      {!loading && activities.length > 0 && (
        <ActivitiesTable
          activities={activities}
          hasMore={hasMore}
          isLoadingMore={isLoadingMore}
          onLoadMore={loadMore}
          onScrollPositionChange={(isTop) => setOverviewAtTop(isTop)}
        />
      )}
    </div>
  );
}
