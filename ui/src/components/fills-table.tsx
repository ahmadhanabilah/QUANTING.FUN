import { useCallback, useEffect, useState } from 'react';
import { Pause, Play, RefreshCcw } from 'lucide-react';

type FillRow = {
  trace?: string | null;
  ts?: string | null;
  venue?: string | null;
  base_amount?: number | null;
  fill_price?: number | null;
  latency?: number | null;
};

interface FillsTableProps {
  botId: string;
  apiBase: string;
  authHeaders: Record<string, string>;
  mode?: 'live' | 'test';
}

export function FillsTable({ botId, apiBase, authHeaders, mode = 'live' }: FillsTableProps) {
  const [rows, setRows] = useState<FillRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [lastUpdated, setLastUpdated] = useState('');
  const [isPaused, setIsPaused] = useState(false);

  const [symbolL, symbolE] = botId.split(':');
  const hasValidPair = Boolean(symbolL && symbolE);

  const fetchFills = useCallback(
    async (showSpinner = false) => {
      if (!hasValidPair) {
        setError('Invalid bot identifier for fills');
        return;
      }
      if (showSpinner) setLoading(true);
      try {
        const res = await fetch(`${apiBase}/api/tt/fills?symbolL=${symbolL}&symbolE=${symbolE}&mode=${mode}`, {
          headers: authHeaders,
        });
        const data = await res.json();
        if (!res.ok) {
          throw new Error(typeof data?.detail === 'string' ? data.detail : 'Failed to load fills');
        }
        const bodyRows: FillRow[] = Array.isArray(data.rows) ? data.rows : [];
        setRows(bodyRows);
        setError('');
        setLastUpdated(new Date().toLocaleTimeString());
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load fills');
        setRows([]);
      } finally {
        if (showSpinner) setLoading(false);
      }
    },
    [apiBase, authHeaders, hasValidPair, mode, symbolE, symbolL]
  );

  useEffect(() => {
    fetchFills(true);
  }, [fetchFills]);

  useEffect(() => {
    if (isPaused) return;
    const id = setInterval(() => fetchFills(), 4000);
    return () => clearInterval(id);
  }, [fetchFills, isPaused]);

  const fmt = (val: any, digits = 4) => {
    if (val === null || val === undefined || val === '') return '—';
    if (typeof val === 'number') return Number.isFinite(val) ? val.toFixed(digits) : String(val);
    return String(val);
  };

  return (
    <div>
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between mb-4">
        <div>
          <p className="text-slate-400 text-sm">Fills ({symbolL}/{symbolE})</p>
          <p className="text-xs text-slate-500">
            {loading
              ? 'Loading...'
              : error
              ? `Error: ${error}`
              : lastUpdated
              ? `Updated at ${lastUpdated}`
              : 'Waiting for data'}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button
            onClick={() => setIsPaused((p) => !p)}
            className="flex items-center gap-2 px-3 py-2 bg-slate-800/50 hover:bg-slate-700/50 text-white rounded-lg transition-all text-sm border border-slate-700/50"
          >
            {isPaused ? <Play className="w-4 h-4" /> : <Pause className="w-4 h-4" />}
            {isPaused ? 'Resume Auto-Refresh' : 'Pause Auto-Refresh'}
          </button>
          <button
            onClick={() => fetchFills(true)}
            className="flex items-center gap-2 px-3 py-2 bg-slate-800/50 hover:bg-slate-700/50 text-white rounded-lg transition-all text-sm border border-slate-700/50"
            disabled={loading}
          >
            <RefreshCcw className="w-4 h-4" />
            {loading ? 'Refreshing...' : 'Refresh'}
          </button>
        </div>
      </div>

      <div className="bg-slate-950/80 backdrop-blur-sm border border-slate-800/50 rounded-xl overflow-hidden shadow-inner">
        <div className="overflow-x-auto">
          {error ? (
            <div className="px-4 py-10 text-center text-red-400 text-sm">{error}</div>
          ) : !rows.length && !loading ? (
            <div className="px-4 py-10 text-center text-slate-500 text-sm">No fills recorded yet</div>
          ) : (
            <table className="w-full">
              <thead className="bg-slate-900/80 border-b border-slate-800/50">
                <tr>
                  <th className="px-4 py-3 text-left text-slate-400 text-xs uppercase tracking-wider whitespace-nowrap">Time</th>
                  <th className="px-4 py-3 text-left text-slate-400 text-xs uppercase tracking-wider whitespace-nowrap">Trace</th>
                  <th className="px-4 py-3 text-left text-slate-400 text-xs uppercase tracking-wider whitespace-nowrap">Venue</th>
                  <th className="px-4 py-3 text-left text-slate-400 text-xs uppercase tracking-wider whitespace-nowrap">Base Amount</th>
                  <th className="px-4 py-3 text-left text-slate-400 text-xs uppercase tracking-wider whitespace-nowrap">Fill Price</th>
                  <th className="px-4 py-3 text-left text-slate-400 text-xs uppercase tracking-wider whitespace-nowrap">Latency (ms)</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row, rowIdx) => (
                  <tr key={rowIdx} className="border-b border-slate-800/30 hover:bg-slate-800/20 transition-all">
                    <td className="px-4 py-3 text-slate-200 text-xs font-mono whitespace-nowrap">
                      {row.ts ? new Date(row.ts).toLocaleTimeString() : '—'}
                    </td>
                    <td className="px-4 py-3 text-slate-400 text-[11px] font-mono whitespace-nowrap">
                      {row.trace ? row.trace.slice(0, 8) : '—'}
                    </td>
                    <td className="px-4 py-3 text-slate-200 text-xs whitespace-nowrap">{row.venue || '—'}</td>
                    <td className="px-4 py-3 text-slate-200 text-xs font-mono whitespace-nowrap">{fmt(row.base_amount, 4)}</td>
                    <td className="px-4 py-3 text-slate-200 text-xs font-mono whitespace-nowrap">{fmt(row.fill_price, 6)}</td>
                    <td className="px-4 py-3 text-slate-200 text-xs font-mono whitespace-nowrap">{fmt(row.latency, 2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      <div className="mt-4 flex items-center justify-between text-sm bg-slate-800/30 rounded-lg px-4 py-3 border border-slate-700/50">
        <span className="text-slate-400">
          Showing <span className="text-white font-medium">{rows.length}</span> fills
        </span>
        <span className="text-slate-500 text-xs">Source: DB ({mode})</span>
      </div>
    </div>
  );
}
