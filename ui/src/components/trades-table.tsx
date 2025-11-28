import { useCallback, useEffect, useState } from 'react';
import { Download, Pause, Play, RefreshCcw } from 'lucide-react';

interface TradesTableProps {
  botId: string;
  botName: string;
  apiBase: string;
  authHeaders: Record<string, string>;
}

type CsvRow = string[];

function parseCsvLine(line: string): CsvRow {
  const cells: string[] = [];
  let current = '';
  let inQuotes = false;

  for (let i = 0; i < line.length; i++) {
    const char = line[i];
    if (char === '"') {
      if (inQuotes && line[i + 1] === '"') {
        current += '"';
        i++;
        continue;
      }
      inQuotes = !inQuotes;
      continue;
    }
    if (char === ',' && !inQuotes) {
      cells.push(current);
      current = '';
      continue;
    }
    current += char;
  }
  cells.push(current);
  return cells;
}

export function TradesTable({ botId, botName, apiBase, authHeaders }: TradesTableProps) {
  const [headers, setHeaders] = useState<string[]>([]);
  const [rows, setRows] = useState<CsvRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [lastUpdated, setLastUpdated] = useState('');
  const [isPaused, setIsPaused] = useState(false);

  const [symbolL, symbolE] = botId.split(':');
  const hasValidPair = Boolean(symbolL && symbolE);

  const fetchTrades = useCallback(
    async (showSpinner = false) => {
      if (!hasValidPair) {
        setError('Invalid bot identifier for trades');
        return;
      }

      if (showSpinner) setLoading(true);

      try {
        const res = await fetch(`${apiBase}/api/trades/${symbolL}/${symbolE}`, { headers: authHeaders });
        const data = await res.json();
        if (!res.ok) {
          throw new Error(typeof data?.detail === 'string' ? data.detail : 'Failed to load trades');
        }

        const headerCells = data.header ? parseCsvLine(data.header) : [];
        const bodyRows: CsvRow[] = Array.isArray(data.rows)
          ? data.rows.map((r: any) => parseCsvLine(String(r?.raw ?? '')))
          : [];

        setHeaders(headerCells);
        setRows(bodyRows);
        setError('');
        setLastUpdated(new Date().toLocaleTimeString());
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load trades');
        setHeaders([]);
        setRows([]);
      } finally {
        if (showSpinner) setLoading(false);
      }
    },
    [apiBase, authHeaders, hasValidPair, symbolE, symbolL]
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

  const handleExport = async () => {
    if (!hasValidPair) return;
    try {
      const res = await fetch(`${apiBase}/api/trades/${symbolL}/${symbolE}/csv`, { headers: authHeaders });
      const text = await res.text();
      if (!res.ok) {
        throw new Error(text || 'Unable to export CSV');
      }
      const blob = new Blob([text], { type: 'text/csv' });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `${botName}-trades.csv`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to export CSV');
    }
  };

  const formatHeader = (key: string, idx: number) => {
    const normalized = key.toLowerCase();
    if (normalized === 'venue_l') return 'Venue Long';
    if (normalized === 'venue_e') return 'Venue Short';
    return key || `Col ${idx + 1}`;
  };

  return (
    <div>
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between mb-4">
        <div>
          <p className="text-slate-400 text-sm">Trades from trades.csv ({symbolL}/{symbolE})</p>
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
            onClick={() => fetchTrades(true)}
            className="flex items-center gap-2 px-3 py-2 bg-slate-800/50 hover:bg-slate-700/50 text-white rounded-lg transition-all text-sm border border-slate-700/50"
            disabled={loading}
          >
            <RefreshCcw className="w-4 h-4" />
            {loading ? 'Refreshing...' : 'Refresh'}
          </button>
          <button
            onClick={handleExport}
            className="flex items-center gap-2 px-3 py-2 bg-slate-800/50 hover:bg-slate-700/50 text-white rounded-lg transition-all text-sm border border-slate-700/50 disabled:opacity-50"
            disabled={!rows.length}
          >
            <Download className="w-4 h-4" />
            Export CSV
          </button>
        </div>
      </div>

      <div className="bg-slate-950/80 backdrop-blur-sm border border-slate-800/50 rounded-xl overflow-hidden shadow-inner">
        <div className="overflow-x-auto">
          {error ? (
            <div className="px-4 py-10 text-center text-red-400 text-sm">{error}</div>
          ) : !rows.length && !loading ? (
            <div className="px-4 py-10 text-center text-slate-500 text-sm">No trades recorded yet</div>
          ) : (
            <table className="w-full">
              <thead className="bg-slate-900/80 border-b border-slate-800/50">
                <tr>
                  {headers.map((cell, idx) => (
                    <th key={idx} className="px-4 py-3 text-left text-slate-400 text-xs uppercase tracking-wider whitespace-nowrap">
                      {formatHeader(cell, idx)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((row, rowIdx) => (
                  <tr key={rowIdx} className="border-b border-slate-800/30 hover:bg-slate-800/20 transition-all">
                    {row.map((cell, cellIdx) => (
                      <td key={cellIdx} className="px-4 py-3 text-slate-200 text-xs font-mono whitespace-nowrap">
                        {cell || '—'}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      <div className="mt-4 flex items-center justify-between text-sm bg-slate-800/30 rounded-lg px-4 py-3 border border-slate-700/50">
        <span className="text-slate-400">
          Showing <span className="text-white font-medium">{rows.length}</span> trades
        </span>
        <span className="text-slate-500 text-xs">Source: logs/{symbolL}:{symbolE}/trades.csv</span>
      </div>
    </div>
  );
}
