import { useCallback, useEffect, useRef, useState } from 'react';

interface LogsViewerProps {
  botId: string;
  botName: string;
  apiBase: string;
  authHeaders: Record<string, string>;
}

type LogType = 'realtime' | 'maker' | 'spread';

const LOG_OPTIONS: Array<{ id: LogType; label: string }> = [
  { id: 'realtime', label: 'Realtime Log' },
  { id: 'spread', label: 'Spread Log' },
  { id: 'maker', label: 'Maker Log' },
];

export function LogsViewer({ botId, botName, apiBase, authHeaders }: LogsViewerProps) {
  const [logType, setLogType] = useState<LogType>('realtime');
  const [logText, setLogText] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [lineInfo, setLineInfo] = useState({ total: 0, shown: 0 });
  const logsEndRef = useRef<HTMLDivElement>(null);
  const MAX_LINES = 50;
  const wsRef = useRef<WebSocket | null>(null);
  const totalLinesRef = useRef(0);
  const tokenRef = useRef<string | null>(null);
  const closingRef = useRef(false);

  const stopStream = useCallback(() => {
    if (wsRef.current) {
      closingRef.current = true;
      wsRef.current.close();
      wsRef.current = null;
    }
  }, []);

  const startStream = useCallback(async () => {
    const [symbolL, symbolE] = botId.split(':');
    if (!symbolL || !symbolE) {
      setError('Invalid bot identifier for logs');
      return;
    }
    stopStream();
    setError('');
    setLogText('');
    setLineInfo({ total: 0, shown: 0 });
    totalLinesRef.current = 0;

    const auth = (authHeaders as any)?.Authorization;
    const token =
      auth && typeof auth === "string" && auth.toLowerCase().startsWith("basic ")
        ? auth.split(" ")[1]
        : tokenRef.current;
    const proto = apiBase.startsWith('https') ? 'wss' : 'ws';
    const wsUrl = `${proto}://${apiBase.replace(/^https?:\/\//, '')}/ws/logs/${symbolL}/${symbolE}/${logType}${token ? `?token=${encodeURIComponent(token)}` : ''}`;
    try {
      console.info('[logs] opening websocket', wsUrl);
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onmessage = (event) => {
        const data = event.data as string;
        const lines = data ? data.split(/\r?\n/).filter(Boolean) : [];
        totalLinesRef.current = lines.length;
        const nextLines = lines.slice(-MAX_LINES);
        setLineInfo({ total: lines.length, shown: nextLines.length });
        setLogText(nextLines.join('\n'));
      };
      ws.onerror = () => {
        console.error('[logs] websocket error', wsUrl);
        setError('Stream error');
      };
      ws.onclose = (evt) => {
        wsRef.current = null;
        if (closingRef.current) {
          closingRef.current = false;
          return;
        }
        console.warn('[logs] websocket closed', evt.code);
        if (evt.code !== 1000 && evt.code !== 1005) {
          setError('Stream closed');
        }
      };
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Stream error');
    }
  }, [apiBase, authHeaders, botId, stopStream, MAX_LINES, logType]);

  useEffect(() => {
    stopStream();
    setError('');
    setLogText('');
    setLineInfo({ total: 0, shown: 0 });
    setLoading(true);
    startStream().finally(() => setLoading(false));
    return () => stopStream();
  }, [logType, startStream, stopStream]);

  useEffect(() => {
    logsEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [logText]);

  const lineNote =
    lineInfo.total > 0
      ? lineInfo.total > MAX_LINES
        ? `Showing last ${lineInfo.shown} of ${lineInfo.total} lines. `
        : `Showing ${lineInfo.total} lines. `
      : '';

  return (
    <div>
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between mb-3">
        <div className="text-xs text-slate-400">
          {loading
            ? `Loading ${logType} log...`
            : error
            ? `Error: ${error}`
            : 'Live tailing'}
        </div>
      </div>

      <div className="flex flex-wrap gap-2 mb-4">
        {LOG_OPTIONS.map((option) => (
          <button
            key={option.id}
            onClick={() => setLogType(option.id)}
            className={`px-4 py-3 rounded-lg text-left transition-all border ${
              logType === option.id
                ? 'bg-gradient-to-r from-blue-500 to-blue-600 text-white border-blue-500/70 shadow-lg shadow-blue-500/25'
                : 'bg-slate-800/50 text-slate-300 hover:bg-slate-700/50 border-slate-700/50'
            }`}
          >
            <div className="text-sm font-semibold">{option.label}</div>
          </button>
        ))}
      </div>

      <div className="bg-slate-950/80 backdrop-blur-sm border border-slate-800/50 rounded-xl overflow-hidden shadow-inner">
        <div className="h-[500px] overflow-y-auto p-4 font-mono text-sm">
          {error ? (
            <div className="flex items-center justify-center h-full text-red-400">{error}</div>
          ) : logText ? (
            <pre className="whitespace-pre-wrap text-slate-200 leading-relaxed">
              {logText}
            </pre>
          ) : (
            <div className="flex items-center justify-center h-full text-slate-500">
              {loading ? 'Loading logs...' : 'No log entries yet'}
            </div>
          )}
          <div ref={logsEndRef} />
        </div>
      </div>
    </div>
  );
}
