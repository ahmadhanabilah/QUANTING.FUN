import { useCallback, useEffect, useState } from 'react';
import { Pause, Play, RefreshCcw } from 'lucide-react';

interface TradesTableProps {
  botId: string;
  botName: string;
  apiBase: string;
  authHeaders: Record<string, string>;
  mode?: 'live' | 'test';
  onModeChange?: (mode: 'live' | 'test') => void;
}

type TradeRow = {
  trace?: string | null;
  ts?: string | null;
  venue?: string | null;
  size?: number | null;
  ob_price?: number | null;
  exec_price?: number | null;
  lat_order?: number | null;
  status?: string | null;
  payload?: string | null;
  resp?: string | null;
};
type FillRow = {
  trace?: string | null;
  ts?: string | null;
  venue?: string | null;
  base_amount?: number | null;
  fill_price?: number | null;
  latency?: number | null;
};

type SideInfo = {
  venue?: string | null;
  size?: number | null;
  ob_price?: number | null;
  exec_price?: number | null;
  fill_price?: number | null;
  lat_order?: number | null;
  lat_fill?: number | null;
  slippage?: number | null;
  status?: string | null;
  payload?: string | null;
  resp?: string | null;
};

type CombinedRow = {
  trace?: string | null;
  ts?: string | null;
  reason?: string | null;
  direction?: string | null;
  spread?: number | null;
  inv_before_str?: string | null;
  inv_after_str?: string | null;
  long?: SideInfo;
  short?: SideInfo;
};

const formatDetailedTimestamp = (ts?: string | null) => {
  if (!ts) return null;
  const date = new Date(ts);
  if (Number.isNaN(date.getTime())) return null;
  const pad = (val: number) => val.toString().padStart(2, '0');
  const datePart = `${pad(date.getDate())}/${pad(date.getMonth() + 1)}/${date.getFullYear()}`;
  const timePart = `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}.${date
    .getMilliseconds()
    .toString()
    .padStart(3, '0')}`;
  return { date: datePart, time: timePart };
};

export function TradesTable({ botId, botName, apiBase, authHeaders, mode = 'live', onModeChange }: TradesTableProps) {
  const [rows, setRows] = useState<TradeRow[]>([]);
  const [fillRows, setFillRows] = useState<FillRow[]>([]);
  const [combinedRows, setCombinedRows] = useState<CombinedRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [lastUpdated, setLastUpdated] = useState('');
  const [isPaused, setIsPaused] = useState(false);
  const [showDebug, setShowDebug] = useState(false);
  const [expandedDebug, setExpandedDebug] = useState<Record<string, boolean>>({});

  const [symbolL, symbolE] = botId.split(':');
  const hasValidPair = Boolean(symbolL && symbolE);

  // Rewritten fetch to include decisions cleanly
  const fetchTrades = useCallback(
    async (showSpinner = false) => {
      if (!hasValidPair) {
        setError('Invalid bot identifier for trades');
        return;
      }

      if (showSpinner) setLoading(true);

      try {
        const [tradeRes, fillRes, decRes] = await Promise.all([
          fetch(`${apiBase}/api/tt/trades?symbolL=${symbolL}&symbolE=${symbolE}&mode=${mode}`, { headers: authHeaders }),
          fetch(`${apiBase}/api/tt/fills?symbolL=${symbolL}&symbolE=${symbolE}&mode=${mode}`, { headers: authHeaders }),
          fetch(`${apiBase}/api/tt/decisions?symbolL=${symbolL}&symbolE=${symbolE}&mode=${mode}`, { headers: authHeaders }),
        ]);
        const tradeData = await tradeRes.json();
        const fillData = await fillRes.json();
        const decData = await decRes.json();
        if (!tradeRes.ok) {
          throw new Error(typeof tradeData?.detail === 'string' ? tradeData.detail : 'Failed to load trades');
        }
        if (!fillRes.ok) {
          throw new Error(typeof fillData?.detail === 'string' ? fillData.detail : 'Failed to load fills');
        }
        if (!decRes.ok) {
          throw new Error(typeof decData?.detail === 'string' ? decData.detail : 'Failed to load decisions');
        }

        const bodyTrades: TradeRow[] = Array.isArray(tradeData.rows) ? tradeData.rows : [];
        const bodyFills: FillRow[] = Array.isArray(fillData.rows) ? fillData.rows : [];
        const bodyDecisions: any[] = Array.isArray(decData.rows) ? decData.rows : [];

        setRows(bodyTrades);
        setFillRows(bodyFills);
        setCombinedRows(buildCombined(bodyTrades, bodyFills, bodyDecisions));
        setError('');
        setLastUpdated(new Date().toLocaleTimeString());
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load trades');
        setRows([]);
        setFillRows([]);
        setCombinedRows([]);
      } finally {
        if (showSpinner) setLoading(false);
      }
    },
    [apiBase, authHeaders, hasValidPair, mode, symbolE, symbolL]
  );

  useEffect(() => {
    fetchTrades(true);
  }, [fetchTrades]);

  useEffect(() => {
    if (isPaused) return;
    const id = setInterval(() => {
      fetchTrades();
    }, 4000);
    return () => clearInterval(id);
  }, [fetchTrades, isPaused]);

  const fmt = (val: any, digits = 4) => {
    if (val === null || val === undefined || val === '') return '—';
    if (typeof val === 'number') return Number.isFinite(val) ? val.toFixed(digits) : String(val);
    return String(val);
  };
  const tradeCount = combinedRows.length;

  function buildCombined(trades: TradeRow[], fills: FillRow[], decisions: any[]): CombinedRow[] {
    const decMap: Record<string, { reason?: string | null; direction?: string | null; spread?: number | null; inv_before_str?: string | null; inv_after_str?: string | null }> = {};
    decisions.forEach((d) => {
      const trace = d.trace_id;
      if (!trace) return;
      decMap[trace] = {
        reason: d.reason,
        direction: d.direction,
        spread: d.spread_signal,
        inv_before_str: d.inv_before_str,
        inv_after_str: d.inv_after_str,
      };
    });

    const byTrace: Record<string, { ts?: string | null; reason?: string | null; direction?: string | null; spread?: number | null; inv_before_str?: string | null; inv_after_str?: string | null; legs: Record<string, SideInfo> }> = {};
    trades.forEach((t) => {
      if (!t.trace) return;
      const decInfo = decMap[t.trace] || {};
      if (!byTrace[t.trace]) {
        byTrace[t.trace] = {
          ts: t.ts,
          reason: decInfo.reason,
          direction: decInfo.direction,
          spread: decInfo.spread,
          inv_before_str: decInfo.inv_before_str,
          inv_after_str: decInfo.inv_after_str,
          legs: {},
        };
      } else {
        byTrace[t.trace].reason = byTrace[t.trace].reason || decInfo.reason;
        byTrace[t.trace].direction = byTrace[t.trace].direction || decInfo.direction;
        byTrace[t.trace].spread = byTrace[t.trace].spread ?? decInfo.spread;
        byTrace[t.trace].inv_before_str = byTrace[t.trace].inv_before_str || decInfo.inv_before_str;
        byTrace[t.trace].inv_after_str = byTrace[t.trace].inv_after_str || decInfo.inv_after_str;
      }
      const side: SideInfo = {
        venue: t.venue,
        size: t.size,
        ob_price: t.ob_price,
        exec_price: t.exec_price,
        lat_order: t.lat_order,
        status: t.status,
        payload: t.payload,
        resp: t.resp,
      };
      if (t.venue) byTrace[t.trace].legs[t.venue] = side;
    });
    fills.forEach((f) => {
      if (!f.trace || !f.venue) return;
      if (!byTrace[f.trace]) byTrace[f.trace] = { ts: f.ts, legs: {} };
      const leg = byTrace[f.trace].legs[f.venue] || {};
      leg.fill_price = f.fill_price;
      leg.lat_fill = f.latency;
      leg.size = leg.size ?? f.base_amount;
      byTrace[f.trace].legs[f.venue] = leg;
    });

    const entries = Object.entries(byTrace);
    const singles = entries.filter(([, d]) => Object.keys(d.legs).length === 1);
    const usedSingles = new Set<string>();
    const mergedEntries: Array<[string, { ts?: string | null; reason?: string | null; direction?: string | null; spread?: number | null; inv_before_str?: string | null; inv_after_str?: string | null; legs: Record<string, SideInfo> }]> = [];

    const ms = (ts?: string | null) => (ts ? new Date(ts).getTime() : 0);
    const oppSign = (a?: SideInfo, b?: SideInfo) => {
      const sa = a?.size ?? 0;
      const sb = b?.size ?? 0;
      return sa !== 0 && sb !== 0 && Math.sign(sa) !== Math.sign(sb);
    };

    // Attempt to pair single-leg traces that share close timestamps and opposite signs
    singles.forEach(([traceA, dataA], idxA) => {
      if (usedSingles.has(traceA)) return;
      const legA = Object.values(dataA.legs)[0];
      singles.slice(idxA + 1).some(([traceB, dataB]) => {
        if (usedSingles.has(traceB)) return false;
        const legB = Object.values(dataB.legs)[0];
        if (!oppSign(legA, legB)) return false;
        const dt = Math.abs(ms(dataA.ts) - ms(dataB.ts));
        if (dt > 2000) return false;
        usedSingles.add(traceA);
        usedSingles.add(traceB);
        mergedEntries.push([
          `${traceA}+${traceB}`,
          {
            ts: ms(dataA.ts) <= ms(dataB.ts) ? dataA.ts : dataB.ts,
            reason: dataA.reason || dataB.reason,
            direction: dataA.direction || dataB.direction,
            spread: dataA.spread ?? dataB.spread,
            legs: { ...dataA.legs, ...dataB.legs },
          },
        ]);
        return true;
      });
    });

    const out: CombinedRow[] = [];
    const allEntries = [
      ...entries.filter(([t, d]) => Object.keys(d.legs).length > 1 || !usedSingles.has(t)),
      ...mergedEntries,
    ];

    allEntries.forEach(([trace, data]) => {
      const legs = Object.values(data.legs);
      let long: SideInfo | undefined;
      let short: SideInfo | undefined;
      legs.forEach((leg) => {
        const size = leg.size ?? 0;
        if (size > 0 && !long) long = leg;
        if (size < 0 && !short) short = leg;
      });
      // fallback assignment if signs missing
      if (!long && legs.length > 0) long = legs[0];
      if (!short && legs.length > 1) short = legs[1];

      const calcSlip = (leg?: SideInfo, isLong?: boolean) => {
        if (!leg || leg.fill_price == null || leg.ob_price == null) return null;
        if (!leg.ob_price) return null;
        return isLong
          ? ((leg.fill_price - leg.ob_price) / leg.ob_price) * 100
          : ((leg.ob_price - leg.fill_price) / leg.ob_price) * 100;
      };
      const traceRow: CombinedRow = {
        trace,
        ts: data.ts,
        reason: data.reason,
        direction: data.direction,
        spread: data.spread,
        inv_before_str: data.inv_before_str,
        inv_after_str: data.inv_after_str,
        long: long
          ? {
              ...long,
              slippage: calcSlip(long, true),
            }
          : undefined,
        short: short
          ? {
              ...short,
              slippage: calcSlip(short, false),
            }
          : undefined,
      };
      out.push(traceRow);
    });
    return out.sort((a, b) => {
      const ta = a.ts ? new Date(a.ts).getTime() : 0;
      const tb = b.ts ? new Date(b.ts).getTime() : 0;
      return tb - ta;
    });
  }

  const renderSide = (_label: string, side?: SideInfo) => {
    if (!side) return <div className="text-slate-500 text-xs">—</div>;
    return (
      <div className="space-y-1">
        <div className="text-slate-400 text-[11px]">
          <span className="text-slate-500">Venue:</span> {side.venue || '—'}
        </div>
        <div className="text-slate-400 text-[11px] font-mono">
          <span className="text-slate-500">Size:</span> {fmt(side.size, 4)}
        </div>
        <div className="text-slate-400 text-[11px] font-mono">
          <span className="text-slate-500">OB:</span> {fmt(side.ob_price, 6)}
        </div>
        <div className="text-slate-400 text-[11px] font-mono">
          <span className="text-slate-500">Exec:</span> {fmt(side.exec_price, 6)}
        </div>
        <div className="text-slate-400 text-[11px] font-mono">
          <span className="text-slate-500">Fill:</span> {fmt(side.fill_price, 6)}
        </div>
        <div className="text-slate-400 text-[11px] font-mono">
          <span className="text-slate-500">Order Lat:</span> {fmt(side.lat_order, 2)} ms
        </div>
        <div className="text-slate-400 text-[11px] font-mono">
          <span className="text-slate-500">Fill Lat:</span> {fmt(side.lat_fill, 2)} ms
        </div>
        <div className="text-slate-400 text-[11px] font-mono">
          <span className="text-slate-500">Slippage:</span> {side.slippage == null ? '—' : `${fmt(side.slippage, 2)}%`}
        </div>
        <div className="text-slate-400 text-[11px] font-mono">
          <span className="text-slate-500">Status:</span> {side.status || '—'}
        </div>
      </div>
    );
  };

  const renderDebugInfo = (side?: SideInfo, opts?: { trace?: string | null; variant: 'long' | 'short' }) => {
    if (!side) return <div className="text-slate-500 text-xs whitespace-pre-wrap break-words">—</div>;

    const makeKey = (field: 'payload' | 'resp') => `${opts?.trace || 'unknown'}:${opts?.variant || 'side'}:${field}`;
    const toggleField = (field: 'payload' | 'resp') => {
      const key = makeKey(field);
      setExpandedDebug((prev) => ({
        ...prev,
        [key]: !prev[key],
      }));
    };
    const shouldExpand = (field: 'payload' | 'resp') => expandedDebug[makeKey(field)];
    const TRUNCATE_LIMIT = 90;

    const renderField = (label: 'Payload' | 'Resp', field: 'payload' | 'resp', value?: string | null) => {
      if (!value) return (
        <div className="text-slate-400 whitespace-pre-wrap break-words">
          <span className="text-slate-500">{label}:</span> —
        </div>
      );
      const expanded = shouldExpand(field);
      const needsToggle = value.length > TRUNCATE_LIMIT;
      const displayValue = !needsToggle || expanded ? value : `${value.slice(0, TRUNCATE_LIMIT)}…`;
      return (
        <div className="text-slate-400 whitespace-pre-wrap break-words">
          <span className="text-slate-500">{label}:</span> {displayValue}
          {needsToggle && (
            <button
              type="button"
              className="ml-2 text-xs text-blue-400 hover:text-blue-200 underline"
              onClick={() => toggleField(field)}
            >
              {expanded ? 'Show less' : 'Show full'}
            </button>
          )}
        </div>
      );
    };

    return (
      <div className="space-y-1 text-[11px] font-mono">
        {renderField('Payload', 'payload', side.payload)}
        {renderField('Resp', 'resp', side.resp)}
      </div>
    );
  };

  const renderInv = (label: string, inv?: string | null, opts?: { showHeading?: boolean }) => {
    const showHeading = opts?.showHeading !== false;
    if (!inv) return null;

    const fmtDelta = (l?: { qty?: number | null; price?: number | null }, e?: { qty?: number | null; price?: number | null }) => {
      if (!l || !e || l.price == null || e.price == null) return null;
      const lq = l.qty ?? 0;
      const eq = e.qty ?? 0;
      if (lq > 0 && eq < 0 && l.price) return ((e.price - l.price) / l.price) * 100;
      if (lq < 0 && eq > 0 && e.price) return ((l.price - e.price) / e.price) * 100;
      return null;
    };

    const entries: Array<{ venue: string; qty: number | null; price: number | null; valueUsd: number | null }> = [];
    let deltaLine: string | null = null;

    // try JSON shape
    try {
      const parsed = JSON.parse(inv);
      const arr = Array.isArray(parsed) ? parsed : [];
      const e = arr.find((x) => x && x.venue === 'E');
      const l = arr.find((x) => x && x.venue === 'L');
      const pushEntry = (obj: any) => {
        if (!obj) return;
        const venue = obj.venue;
        const qty = Number.isFinite(obj.qty) ? Number(obj.qty) : null;
        const price = Number.isFinite(obj.price) ? Number(obj.price) : null;
        const valueUsd = qty != null && price != null ? Math.abs(qty) * price : null;
        entries.push({ venue, qty, price, valueUsd });
      };
      pushEntry(e);
      pushEntry(l);
      const d = fmtDelta(l, e);
      if (d != null) deltaLine = `Δ -> ${fmt(d, 2)}%`;
    } catch {
      // fallback to legacy string parsing
      const norm = inv.replace("Δ:", "Δ ->").replace("Δ :", "Δ ->");
      const parts = norm.split("|").map((p) => p.trim()).filter(Boolean);
      parts.forEach((part) => {
        const venueMatch = part.match(/^([A-Za-z])\s*(?:->|:)/);
        if (!venueMatch) return;
        const venue = venueMatch[1].toUpperCase();
        const qtyMatch = part.match(/Qty:\s*([-\d.eE+]+)/i);
        const priceMatch = part.match(/Price:\s*([-\d.eE+]+)/i);
        const qty = qtyMatch ? Number(qtyMatch[1]) : null;
        const price = priceMatch ? Number(priceMatch[1]) : null;
        const valueUsd = qty != null && price != null ? Math.abs(qty) * price : null;
        entries.push({
          venue,
          qty: qty !== null && Number.isFinite(qty) ? qty : null,
          price: price !== null && Number.isFinite(price) ? price : null,
          valueUsd: valueUsd !== null && Number.isFinite(valueUsd) ? valueUsd : null,
        });
      });
      const deltaPart = parts.find((p) => p.includes("Δ"));
      if (deltaPart) deltaLine = deltaPart;
    }

    return (
      <div className="space-y-1">
        {showHeading && <div className="text-slate-300 text-xs font-semibold">{label}</div>}
        {entries.map((entry, idx) => (
          <div key={`${entry.venue}-${idx}`} className="text-slate-400 text-[11px] font-mono">
            {entry.venue} &rarr; Qty: {fmt(entry.qty, 6)}, Price: {fmt(entry.price, 6)}, Value: {fmt(entry.valueUsd, 4)} USD
          </div>
        ))}
        {deltaLine && <div className="text-slate-400 text-[11px] font-mono">{deltaLine}</div>}
      </div>
    );
  };

  return (
    <div className="flex flex-col flex-1">
      <div className="mb-4 flex flex-wrap items-center gap-2 text-[10px] sm:text-xs">
        <span className="h-7 px-2 inline-flex items-center rounded-md bg-slate-900/70 border border-slate-800/70 text-slate-200">
          {loading ? 'Loading...' : error ? 'Error' : lastUpdated || '—'}
        </span>
        <span className="h-7 px-2 inline-flex items-center rounded-md bg-slate-900/70 border border-slate-800/70 text-slate-200">
          Rows: {tradeCount}
        </span>
        <button
          onClick={() => setIsPaused((p) => !p)}
          className="h-7 px-2 inline-flex items-center gap-1 bg-slate-800/50 hover:bg-slate-700/50 text-white rounded-md transition-all border border-slate-700/50"
          title={isPaused ? 'Resume updates' : 'Pause updates'}
        >
          {isPaused ? <Play className="w-4 h-4" /> : <Pause className="w-4 h-4" />}
          <span className="hidden sm:inline">{isPaused ? 'Resume' : 'Pause'}</span>
        </button>
        <button
          onClick={() => fetchTrades(true)}
          className="h-7 px-2 inline-flex items-center gap-1 bg-slate-800/50 hover:bg-slate-700/50 text-white rounded-md transition-all border border-slate-700/50"
          disabled={loading}
          title="Refresh now"
        >
          <RefreshCcw className="w-4 h-4" />
          <span className="hidden sm:inline">{loading ? 'Refreshing...' : 'Refresh'}</span>
        </button>
        <button
          onClick={() => onModeChange && onModeChange(mode === 'live' ? 'test' : 'live')}
          className="h-7 px-2 inline-flex items-center bg-slate-900/70 border border-slate-700/70 rounded-md text-slate-200 uppercase text-[11px]"
          title="Toggle data source"
        >
          {mode === 'live' ? 'Live' : 'Test'}
        </button>
        <button
          onClick={() => setShowDebug((prev) => !prev)}
          aria-pressed={showDebug}
          className={`h-7 px-2 inline-flex items-center uppercase tracking-wide rounded-md transition ${
            showDebug
              ? 'bg-blue-600 text-white border border-blue-500/60 shadow-blue-500/30'
              : 'border border-slate-700/60 bg-slate-900/60 text-slate-300 hover:text-white hover:border-slate-600/80'
          }`}
        >
          Debug
        </button>
      </div>

      <div className="bg-slate-950/80 backdrop-blur-sm border border-slate-800/50 rounded-xl overflow-hidden shadow-inner flex-1 flex flex-col min-h-0">
        <div className="overflow-x-auto flex-1 min-h-0">
          <div className="overflow-y-auto max-h-[calc(100vh-320px)]">
            {(() => {
              const columnWidth = `${100 / (showDebug ? 7 : 5)}%`;
              const minWidth = showDebug ? '1200px' : '900px';
              return (
                <table className="w-full table-fixed table-mono" style={{ minWidth }}>
                  <thead className="bg-slate-900/80 border-b border-slate-800/50 sticky top-0 z-10">
                    <tr>
                      <th className="px-4 py-3 text-left text-slate-400 text-xs uppercase tracking-wider whitespace-nowrap" style={{ width: columnWidth }}>Key</th>
                      <th className="px-4 py-3 text-left text-slate-400 text-xs uppercase tracking-wider whitespace-nowrap" style={{ width: columnWidth }}>Inv Before</th>
                      <th className="px-4 py-3 text-left text-slate-400 text-xs uppercase tracking-wider whitespace-nowrap" style={{ width: columnWidth }}>Inv After</th>
                      <th className="px-4 py-3 text-left text-slate-400 text-xs uppercase tracking-wider whitespace-nowrap" style={{ width: columnWidth }}>Long Side</th>
                      {showDebug && (
                        <th className="px-4 py-3 text-left text-slate-400 text-xs uppercase tracking-wider whitespace-nowrap" style={{ width: columnWidth }}>Long Debug</th>
                      )}
                      <th className="px-4 py-3 text-left text-slate-400 text-xs uppercase tracking-wider whitespace-nowrap" style={{ width: columnWidth }}>Short Side</th>
                      {showDebug && (
                        <th className="px-4 py-3 text-left text-slate-400 text-xs uppercase tracking-wider whitespace-nowrap" style={{ width: columnWidth }}>Short Debug</th>
                      )}
                    </tr>
                  </thead>
                  <tbody>
                    {error ? (
                      <tr>
                        <td colSpan={5} className="px-4 py-10 text-center text-red-400 text-sm">
                          {error}
                        </td>
                      </tr>
                    ) : !combinedRows.length && !loading ? (
                      <tr>
                        <td colSpan={5} className="px-4 py-10 text-center text-slate-500 text-sm">
                          No trades recorded yet
                        </td>
                      </tr>
                    ) : (
                      combinedRows.map((row, rowIdx) => (
                        <tr
                          key={rowIdx}
                          className={`border-b border-slate-800/30 hover:bg-slate-800/20 transition-all ${
                            rowIdx % 2 === 0 ? "bg-slate-900/40" : ""
                          }`}
                        >
                          <td className="px-4 py-3 text-slate-200 text-xs align-top" style={{ width: columnWidth }}>
                      <div className="space-y-1 font-mono">
                        {(() => {
                          const ts = formatDetailedTimestamp(row.ts);
                          if (!ts) {
                            return <div className="text-slate-500">—</div>;
                          }
                          return (
                            <>
                              <div className="text-slate-500">{ts.date}</div>
                              <div className="text-white">{ts.time}</div>
                            </>
                          );
                        })()}
                        <div className="text-white">{row.trace ? row.trace.slice(0, 12) : '—'}</div>
                        <div className="text-[11px] text-slate-200 space-y-1">
                          <div className="text-slate-300">{row.direction ? row.direction.toUpperCase() : '—'}</div>
                          <div className="text-slate-400">{row.reason || '—'}</div>
                          <div className="text-slate-500">Δ : {row.spread == null ? '—' : `${fmt(row.spread, 2)}%`}</div>
                        </div>
                      </div>
                    </td>
                    <td className="px-4 py-3 align-top" style={{ width: columnWidth }}>
                      {renderInv("Before", row.inv_before_str, { showHeading: false }) || (
                        <div className="text-slate-500 text-[11px] font-mono">—</div>
                      )}
                    </td>
                    <td className="px-4 py-3 align-top" style={{ width: columnWidth }}>
                      {renderInv("After", row.inv_after_str, { showHeading: false }) || (
                        <div className="text-slate-500 text-[11px] font-mono">—</div>
                      )}
                    </td>
                    <td className="px-4 py-3 align-top" style={{ width: columnWidth }}>{renderSide('Long', row.long)}</td>
                    {showDebug && (
                      <td className="px-4 py-3 align-top" style={{ width: columnWidth }}>
                        {renderDebugInfo(row.long, { trace: row.trace, variant: 'long' })}
                      </td>
                    )}
                    <td className="px-4 py-3 align-top" style={{ width: columnWidth }}>{renderSide('Short', row.short)}</td>
                    {showDebug && (
                      <td className="px-4 py-3 align-top" style={{ width: columnWidth }}>
                        {renderDebugInfo(row.short, { trace: row.trace, variant: 'short' })}
                      </td>
                    )}
                  </tr>
                ))
              )}
                  </tbody>
                </table>
              );
            })()}
          </div>
        </div>
      </div>

      <div className="mt-4 flex items-center justify-between text-sm bg-slate-800/30 rounded-lg px-4 py-3 border border-slate-700/50">
        <span className="text-slate-400">
          Showing <span className="text-white font-medium">{combinedRows.length}</span> trades
        </span>
        <span className="text-slate-500 text-xs">Source: DB ({mode})</span>
      </div>
    </div>
  );
}
