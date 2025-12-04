import { useCallback, useEffect, useMemo, useState } from "react";
import { RefreshCcw } from "lucide-react";

type DecisionRow = {
  trace_id?: string | null;
  ts?: string | null;
  reason?: string | null;
  direction?: string | null;
  action?: string | null;
  dir_expl?: string | null;
  spread_signal?: number | null;
  inv_l?: number | null;
  inv_e?: number | null;
  inv_after_l?: number | null;
  inv_after_e?: number | null;
  inv_before_str?: string | null;
  inv_after_str?: string | null;
  ob_l?: string | null;
  ob_e?: string | null;
};

type DecisionsTableProps = {
  botId: string;
  apiBase: string;
  authHeaders: Record<string, string>;
  mode: "live" | "test";
  onModeChange?: (mode: "live" | "test") => void;
};

export function DecisionsTable({ botId, apiBase, authHeaders, mode, onModeChange }: DecisionsTableProps) {
  const [rows, setRows] = useState<DecisionRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [lastUpdated, setLastUpdated] = useState("");

  const [symbolL, symbolE] = botId.split(":");
  const hasValidPair = useMemo(() => Boolean(symbolL && symbolE), [symbolL, symbolE]);

  const fetchDecisions = useCallback(
    async (showSpinner = false) => {
      if (!hasValidPair) {
        setError("Invalid bot identifier for decisions");
        return;
      }
      if (showSpinner) setLoading(true);
      try {
        const res = await fetch(`${apiBase}/api/tt/decisions?symbolL=${symbolL}&symbolE=${symbolE}&mode=${mode}`, {
          headers: authHeaders,
        });
        const data = await res.json();
        if (!res.ok) {
          throw new Error(typeof data?.detail === "string" ? data.detail : "Failed to load decisions");
        }
        const list: DecisionRow[] = Array.isArray(data?.rows)
          ? data.rows.map((r: any) => ({
              ...r,
              dir_expl: r?.dir_expl || `${r?.direction || ''} ${r?.reason || ''}`.trim(),
            }))
          : [];
        setRows(list);
        setError("");
        setLastUpdated(new Date().toLocaleTimeString());
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load decisions");
        setRows([]);
      } finally {
        if (showSpinner) setLoading(false);
      }
    },
    [apiBase, authHeaders, hasValidPair, mode, symbolE, symbolL]
  );

  useEffect(() => {
    fetchDecisions(true);
  }, [fetchDecisions]);

  useEffect(() => {
    const id = setInterval(() => {
      fetchDecisions();
    }, 4000);
    return () => clearInterval(id);
  }, [fetchDecisions]);

  const fmt = (val: any, digits = 4) => {
    if (val === null || val === undefined || val === "") return "—";
    if (typeof val === "number") return Number.isFinite(val) ? val.toFixed(digits) : String(val);
    return String(val);
  };

  const renderOb = (row: DecisionRow) => {
    const longVenue = row.reason === "TT_EL" || row.reason === "WARM_UP_EL" ? "E" : "L";
    const shortVenue = longVenue === "L" ? "E" : "L";
    const obLong = longVenue === "L" ? row.ob_l : row.ob_e;
    const obShort = shortVenue === "L" ? row.ob_l : row.ob_e;
    return (
      <div className="space-y-1 text-[11px] text-slate-200 font-mono">
        <div>{longVenue}: {obLong || "—"}</div>
        <div>{shortVenue}: {obShort || "—"}</div>
      </div>
    );
  };

  const renderInvBlock = (invStr: string | null | undefined, reason?: string | null, spread?: number | null) => {
    if (!invStr) return <div className="text-slate-500 text-xs">—</div>;
    const longVenue = reason === "TT_EL" || reason === "WARM_UP_EL" ? "E" : "L";
    const shortVenue = longVenue === "L" ? "E" : "L";
    const parts = invStr.split("|").map((p) => p.trim());
    const findPart = (key: string) => parts.find((p) => p.startsWith(key));
    const longPart = findPart(`${longVenue}:`) || parts[0] || "";
    const shortPart = findPart(`${shortVenue}:`) || parts[1] || "";
    const spreadPartRaw = parts.find((p) => p.startsWith("Δ")) || (spread !== null && spread !== undefined ? `Δ -> ${fmt(spread, 2)}%` : "");
    const spreadPart = spreadPartRaw ? spreadPartRaw.replace("Δ:", "Δ ->").replace("Δ :", "Δ ->") : "";
    const extraParts = parts.filter((p) => p.toLowerCase().includes("lat=") || p.toLowerCase().includes("fill="));
    return (
      <div className="space-y-1 text-[11px] text-slate-200 font-mono">
        <div>{longPart || "—"}</div>
        <div>{shortPart || "—"}</div>
        {spreadPart && <div>{spreadPart}</div>}
        {extraParts.map((p, idx) => (
          <div key={idx}>{p}</div>
        ))}
      </div>
    );
  };

  const renderDirReason = (row: DecisionRow) => {
    const title = row.direction ? row.direction.toUpperCase() : "—";
    const reason = row.reason || "—";
    const spread = row.spread_signal;
    return (
      <div className="text-[11px] text-slate-200 font-mono space-y-1">
        <div className="inline-flex items-center gap-1 px-2 py-1 rounded-md bg-slate-800/60 text-slate-200 uppercase">{title}</div>
        <div className="text-slate-300">{reason}</div>
        <div className="text-slate-500">Δ : {spread === null || spread === undefined ? "—" : `${fmt(spread, 2)}%`}</div>
      </div>
    );
  };

  return (
    <div>
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between mb-4">
        <div className="space-y-1">
          <p className="text-slate-200 text-sm font-semibold">Decisions · {symbolL}/{symbolE}</p>
          <div className="flex items-center gap-2 text-xs text-slate-400">
            <span className="px-2 py-1 rounded-md bg-slate-800/70 border border-slate-700/70">
              {loading ? "Loading..." : error ? "Error" : lastUpdated ? `Updated ${lastUpdated}` : "Waiting for data"}
            </span>
            <span className="px-2 py-1 rounded-md bg-slate-800/50 border border-slate-700/70">Rows: {rows.length}</span>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => fetchDecisions(true)}
            className="flex items-center gap-2 px-3 py-2 bg-slate-800/50 hover:bg-slate-700/50 text-white rounded-lg transition-all text-sm border border-slate-700/50"
            disabled={loading}
          >
            <RefreshCcw className="w-4 h-4" />
            {loading ? "Refreshing..." : "Refresh"}
          </button>
          <div className="flex bg-slate-900/70 rounded-lg overflow-hidden border border-slate-700/70">
            {(["live", "test"] as const).map((m) => (
              <button
                key={m}
                onClick={() => onModeChange && onModeChange(m)}
                className={`px-3 py-2 text-xs uppercase tracking-wide transition-all ${
                  mode === m
                    ? "bg-blue-600 text-white shadow-inner shadow-blue-500/30"
                    : "text-slate-300 hover:text-white hover:bg-slate-800/60"
                }`}
              >
                {m}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="bg-slate-950/80 backdrop-blur-sm border border-slate-800/50 rounded-xl overflow-hidden shadow-inner">
        {error ? (
          <div className="px-4 py-10 text-center text-red-400 text-sm">{error}</div>
        ) : loading ? (
          <div className="px-4 py-10 text-center text-slate-500 text-sm">Loading...</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs table-mono">
              <thead className="bg-slate-900/80 border-b border-slate-800/50">
                <tr>
                  <th className="px-3 py-2 text-left text-slate-400 uppercase tracking-wider whitespace-nowrap">Time</th>
                  <th className="px-3 py-2 text-left text-slate-400 uppercase tracking-wider whitespace-nowrap">Trace</th>
                  <th className="px-3 py-2 text-left text-slate-400 uppercase tracking-wider whitespace-nowrap">Dir / Reason</th>
                  <th className="px-3 py-2 text-left text-slate-400 uppercase tracking-wider whitespace-nowrap">OB</th>
                  <th className="px-3 py-2 text-left text-slate-400 uppercase tracking-wider whitespace-nowrap">Inv Before</th>
                  <th className="px-3 py-2 text-left text-slate-400 uppercase tracking-wider whitespace-nowrap">Inv After</th>
                </tr>
              </thead>
              <tbody>
                {rows.length === 0 ? (
                  <tr>
                    <td colSpan={5} className="px-3 py-6 text-center text-slate-500">
                      No decisions logged yet
                    </td>
                  </tr>
                ) : (
                  rows.map((row, idx) => (
                    <tr key={(row.trace_id || row.ts || idx).toString()} className={`border-b border-slate-800/40 hover:bg-slate-800/30 transition-colors ${idx % 2 === 0 ? "bg-slate-900/40" : ""}`}>
                      <td className="px-3 py-2 text-slate-200 font-mono whitespace-nowrap">
                        {row.ts ? new Date(row.ts).toLocaleTimeString() : "—"}
                      </td>
                      <td className="px-3 py-2 text-slate-400 text-[11px] font-mono whitespace-nowrap">
                        {row.trace_id ? row.trace_id.slice(0, 8) : "—"}
                      </td>
                      <td className="px-3 py-2">{renderDirReason(row)}</td>
                      <td className="px-3 py-2">{renderOb(row)}</td>
                      <td className="px-3 py-2">
                        {renderInvBlock(row.inv_before_str || `${fmt(row.inv_l, 4)}/${fmt(row.inv_e, 4)}`, row.reason, row.spread_signal)}
                      </td>
                      <td className="px-3 py-2">
                        {renderInvBlock(row.inv_after_str || `${fmt(row.inv_after_l, 4)}/${fmt(row.inv_after_e, 4)}`, row.reason, row.spread_signal)}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
