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
   size?: number | null;
  inv_l?: number | null;
  inv_e?: number | null;
  inv_after_l?: number | null;
  inv_after_e?: number | null;
  inv_before_str?: string | null;
  inv_after_str?: string | null;
  ob_l?: string | null;
  ob_e?: string | null;
};

type InventoryEntry = {
  venue: string;
  qty: number | null;
  price: number | null;
};

type DecisionsTableProps = {
  botId: string;
  apiBase: string;
  authHeaders: Record<string, string>;
  mode: "live" | "test";
  onModeChange?: (mode: "live" | "test") => void;
};

const formatDetailedTimestamp = (ts?: string | null) => {
  if (!ts) return null;
  const date = new Date(ts);
  if (Number.isNaN(date.getTime())) return null;
  const pad = (val: number) => val.toString().padStart(2, "0");
  const datePart = `${pad(date.getDate())}/${pad(date.getMonth() + 1)}/${date.getFullYear()}`;
  const timePart = `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}.${date
    .getMilliseconds()
    .toString()
    .padStart(3, "0")}`;
  return { date: datePart, time: timePart };
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
              size: r?.size ?? null,
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

  const parseInventoryPayload = (raw?: string | null): InventoryEntry[] | null => {
    if (!raw) return null;
    const trimmed = raw.trim();
    // JSON array payload (preferred)
    if (trimmed.startsWith("[")) {
      try {
        const parsed = JSON.parse(trimmed);
        if (!Array.isArray(parsed)) return null;
        return parsed
          .map((entry: any) => {
            const venue = typeof entry?.venue === "string" ? entry.venue.toUpperCase() : "";
            if (!venue) return null;
            const qty = Number(entry?.qty);
            const price = Number(entry?.price);
            return {
              venue,
              qty: Number.isFinite(qty) ? qty : null,
              price: Number.isFinite(price) ? price : null,
            };
          })
          .filter((entry): entry is InventoryEntry => Boolean(entry && entry.venue));
      } catch {
        return null;
      }
    }
    // fallback: parse "L -> Qty: x, Price: y | E -> ..." strings
    const parts = trimmed.split("|");
    const entries: InventoryEntry[] = [];
    for (const part of parts) {
      const text = part.trim();
      const venueMatch = text.match(/^([A-Za-z])\s*(?:->|:)/);
      if (!venueMatch) continue;
      const venue = venueMatch[1].toUpperCase();
      const qtyMatch = text.match(/Qty:\s*([-\d.eE+]+)/i);
      const priceMatch = text.match(/Price:\s*([-\d.eE+]+)/i);
      const qtyVal = qtyMatch ? Number(qtyMatch[1]) : null;
      const priceVal = priceMatch ? Number(priceMatch[1]) : null;
      entries.push({
        venue,
        qty: qtyVal !== null && Number.isFinite(qtyVal) ? qtyVal : null,
        price: priceVal !== null && Number.isFinite(priceVal) ? priceVal : null,
      });
    }
    return entries.length ? entries : null;
  };

  const calcDeltaFromInventory = (entries: InventoryEntry[]): number | null => {
    if (!entries?.length) return null;
    const entryE = entries.find((e) => e.venue === "E");
    const entryL = entries.find((e) => e.venue === "L");
    if (!entryE || !entryL) return null;
    const { qty: eq, price: ee } = entryE;
    const { qty: lq, price: le } = entryL;
    if (eq === null || ee === null || lq === null || le === null) return null;
    if (lq > 0 && eq < 0 && le !== 0) {
      return ((ee - le) / le) * 100;
    }
    if (lq < 0 && eq > 0 && ee !== 0) {
      return ((le - ee) / ee) * 100;
    }
    return null;
  };

  const deriveSize = (row: DecisionRow): number | null => {
    if (row.size !== null && row.size !== undefined) return row.size;
    const before = parseInventoryPayload(row.inv_before_str || "");
    const after = parseInventoryPayload(row.inv_after_str || "");
    const toMap = (inv: InventoryEntry[] | null) =>
      (inv || []).reduce<Record<string, number>>((acc, entry) => {
        if (entry.qty !== null && entry.qty !== undefined && Number.isFinite(entry.qty)) {
          acc[entry.venue] = entry.qty;
        }
        return acc;
      }, {});
    const beforeMap = toMap(before);
    const afterMap = toMap(after);
    const venues = new Set([...Object.keys(beforeMap), ...Object.keys(afterMap)]);
    const deltas: number[] = [];
    venues.forEach((v) => {
      const b = beforeMap[v] ?? 0;
      const a = afterMap[v] ?? 0;
      const delta = a - b;
      if (Number.isFinite(delta)) {
        deltas.push(Math.abs(delta));
      }
    });
    if (deltas.length) {
      const maxDelta = Math.max(...deltas);
      if (Number.isFinite(maxDelta)) return maxDelta;
    }
    const parsedInv = after || before;
    if (!parsedInv || !parsedInv.length) return null;
    const qtys = parsedInv
      .map((e) => (e.qty === null || e.qty === undefined ? null : Math.abs(e.qty)))
      .filter((q): q is number => q !== null && Number.isFinite(q));
    if (!qtys.length) return null;
    return Math.max(...qtys);
  };

  const formatInventoryEntries = (entries: InventoryEntry[]): string[] => {
    return entries.map((entry) => {
      const qty = entry.qty === null ? "—" : entry.qty.toString();
      const price = entry.price === null ? "—" : Number(entry.price).toFixed(6);
      return `${entry.venue} -> Qty: ${qty}, Price: ${price}`;
    });
  };

  const renderInvBlock = (invStr: string | null | undefined, reason?: string | null, spread?: number | null) => {
    if (!invStr) return <div className="text-slate-500 text-xs">—</div>;
    const parsedInventory = parseInventoryPayload(invStr);
    if (parsedInventory && parsedInventory.length) {
      const withValue = parsedInventory.map((entry) => {
        const valueUsd = entry.qty != null && entry.price != null ? Math.abs(entry.qty) * entry.price : null;
        return { ...entry, valueUsd };
      });
      const deltaFromInv = calcDeltaFromInventory(parsedInventory);
      const hasQty = withValue.some((entry) => entry.qty !== null && entry.qty !== 0);
      const deltaText =
        deltaFromInv !== null
          ? `Δ -> ${fmt(deltaFromInv, 2)}%`
          : hasQty && spread !== null && spread !== undefined
          ? `Δ -> ${fmt(spread, 2)}%`
          : "";
      const formattedEntries = withValue.map((entry) => {
        const qty = entry.qty === null ? "—" : entry.qty.toString();
        const price = entry.price === null ? "—" : Number(entry.price).toFixed(6);
        const val = entry.valueUsd === null ? "—" : Number(entry.valueUsd).toFixed(4);
        return `${entry.venue} -> Qty: ${qty}, Price: ${price}, Value: ${val} USD`;
      });
      return (
        <div className="space-y-1 text-[11px] text-slate-200 font-mono">
          {formattedEntries.map((line, idx) => (
            <div key={`${line}-${idx}`}>{line}</div>
          ))}
          {deltaText && <div>{deltaText}</div>}
        </div>
      );
    }
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
    const size = deriveSize(row);
    return (
      <div className="text-[11px] text-slate-200 font-mono space-y-1">
        <div className="inline-flex items-center gap-1 px-2 py-1 rounded-md bg-slate-800/60 text-slate-200 uppercase">{title}</div>
        <div className="text-slate-300">{reason}</div>
        <div className="text-slate-300">Size: {size === null || size === undefined ? "—" : fmt(size, 4)}</div>
        <div className="text-slate-500">Δ : {spread === null || spread === undefined ? "—" : `${fmt(spread, 2)}%`}</div>
      </div>
    );
  };

  return (
    <div className="flex flex-col flex-1">
      <div className="mb-4 flex flex-wrap items-center gap-2 text-[10px] sm:text-xs">
        <span className="h-7 px-2 inline-flex items-center rounded-md bg-slate-900/70 border border-slate-800/70 text-slate-200">
          {loading ? "Loading..." : error ? "Error" : lastUpdated || "—"}
        </span>
        <span className="h-7 px-2 inline-flex items-center rounded-md bg-slate-900/70 border border-slate-800/70 text-slate-200">
          Rows: {rows.length}
        </span>
        <button
          onClick={() => fetchDecisions(true)}
          className="h-7 px-2 inline-flex items-center gap-1 bg-slate-800/50 hover:bg-slate-700/50 text-white rounded-md transition-all border border-slate-700/50"
          disabled={loading}
          title="Refresh decisions"
        >
          <RefreshCcw className="w-4 h-4" />
          <span className="hidden sm:inline">{loading ? "Refreshing..." : "Refresh"}</span>
        </button>
        <button
          onClick={() => onModeChange && onModeChange(mode === "live" ? "test" : "live")}
          className="h-7 px-2 inline-flex items-center bg-slate-900/70 border border-slate-700/70 rounded-md text-slate-200 uppercase"
          title="Toggle data source"
        >
          {mode === "live" ? "Live" : "Test"}
        </button>
      </div>

      <div className="bg-slate-950/80 backdrop-blur-sm border border-slate-800/50 rounded-xl overflow-hidden shadow-inner flex-1 flex flex-col min-h-0">
        {error ? (
          <div className="px-4 py-10 text-center text-red-400 text-sm">{error}</div>
        ) : loading ? (
          <div className="px-4 py-10 text-center text-slate-500 text-sm">Loading...</div>
        ) : (
          <div className="overflow-x-auto flex-1 min-h-0">
            <div className="overflow-y-auto max-h-[calc(100vh-320px)]">
              <table className="w-full text-xs table-mono">
                <thead className="bg-slate-900/80 border-b border-slate-800/50 sticky top-0 z-10">
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
                          {(() => {
                            const ts = formatDetailedTimestamp(row.ts);
                            if (!ts) return "—";
                            return (
                              <div className="text-[11px] space-y-0.5">
                                <div>{ts.date}</div>
                                <div className="text-slate-400">{ts.time}</div>
                              </div>
                            );
                          })()}
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
          </div>
        )}
      </div>
    </div>
  );
}
