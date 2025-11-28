import { useEffect, useRef, useState } from 'react';
import { Download, Trash2, Play, Pause } from 'lucide-react';

interface LogsViewerProps {
  botId: string;
  botName: string;
}

const generateMockLogs = (botName: string) => [
  { id: 1, timestamp: '2025-11-27 10:23:45', level: 'info', message: 'Bot started successfully' },
  { id: 2, timestamp: '2025-11-27 10:24:12', level: 'success', message: 'Buy order executed: 0.0045 BTC at $42,150.50' },
  { id: 3, timestamp: '2025-11-27 10:24:45', level: 'info', message: 'Checking market conditions...' },
  { id: 4, timestamp: '2025-11-27 10:25:03', level: 'success', message: 'Position opened at market price' },
  { id: 5, timestamp: '2025-11-27 10:25:30', level: 'warning', message: 'Spread below threshold: 0.12%' },
  { id: 6, timestamp: '2025-11-27 10:26:15', level: 'success', message: 'Sell order executed: 0.0045 BTC at $42,245.75, Profit: +0.23%' },
  { id: 7, timestamp: '2025-11-27 10:26:42', level: 'info', message: 'Monitoring price action...' },
  { id: 8, timestamp: '2025-11-27 10:27:10', level: 'error', message: 'Connection timeout, retrying...' },
  { id: 9, timestamp: '2025-11-27 10:27:35', level: 'info', message: 'Analyzing order book depth...' },
  { id: 10, timestamp: '2025-11-27 10:28:02', level: 'success', message: 'Trade completed: +0.45% profit' },
];

export function LogsViewer({ botId, botName }: LogsViewerProps) {
  const [logs, setLogs] = useState(() => generateMockLogs(botName));
  const [isPaused, setIsPaused] = useState(false);
  const [filter, setFilter] = useState<'all' | 'info' | 'success' | 'warning' | 'error'>('all');
  const logsEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (isPaused) return;

    const interval = setInterval(() => {
      const levels: Array<'info' | 'success' | 'warning' | 'error'> = ['info', 'success', 'warning', 'error'];
      const messages: Record<string, string[]> = {
        info: ['Analyzing market conditions...', 'Checking price action...', 'Monitoring order book...', 'Scanning for opportunities...'],
        success: ['Order executed successfully', 'Trade completed with profit', 'Position closed', 'Entry signal confirmed'],
        warning: ['Low liquidity detected', 'Spread below threshold', 'High volatility warning', 'Slippage detected'],
        error: ['Connection timeout', 'API rate limit exceeded', 'Order failed', 'Insufficient balance'],
      };

      const level = levels[Math.floor(Math.random() * levels.length)];
      const message = messages[level][Math.floor(Math.random() * messages[level].length)];
      const now = new Date();
      const timestamp = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')} ${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}:${String(now.getSeconds()).padStart(2, '0')}`;

      setLogs(prev => [...prev, { id: Date.now(), timestamp, level, message }]);
    }, 3000);

    return () => clearInterval(interval);
  }, [isPaused, botName]);

  useEffect(() => {
    if (!isPaused) {
      logsEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
  }, [logs, isPaused]);

  const filteredLogs = filter === 'all' ? logs : logs.filter(log => log.level === filter);

  const getLevelColor = (level: string) => {
    switch (level) {
      case 'info': return 'text-blue-400';
      case 'success': return 'text-green-400';
      case 'warning': return 'text-yellow-400';
      case 'error': return 'text-red-400';
      default: return 'text-slate-400';
    }
  };

  const getLevelBg = (level: string) => {
    switch (level) {
      case 'info': return 'bg-blue-500/10 border-blue-500/20';
      case 'success': return 'bg-green-500/10 border-green-500/20';
      case 'warning': return 'bg-yellow-500/10 border-yellow-500/20';
      case 'error': return 'bg-red-500/10 border-red-500/20';
      default: return 'bg-slate-500/10 border-slate-500/20';
    }
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <button
            onClick={() => setIsPaused(!isPaused)}
            className="flex items-center gap-2 px-3 py-2 bg-slate-800/50 hover:bg-slate-700/50 text-white rounded-lg transition-all text-sm border border-slate-700/50"
          >
            {isPaused ? <Play className="w-4 h-4" /> : <Pause className="w-4 h-4" />}
            {isPaused ? 'Resume' : 'Pause'}
          </button>
          <button className="flex items-center gap-2 px-3 py-2 bg-slate-800/50 hover:bg-slate-700/50 text-white rounded-lg transition-all text-sm border border-slate-700/50">
            <Download className="w-4 h-4" />
            Export
          </button>
          <button
            onClick={() => setLogs([])}
            className="flex items-center gap-2 px-3 py-2 bg-red-500/10 hover:bg-red-500/20 text-red-400 rounded-lg transition-all text-sm border border-red-500/20"
          >
            <Trash2 className="w-4 h-4" />
            Clear
          </button>
        </div>
      </div>

      {/* Filter buttons */}
      <div className="flex gap-2 mb-4">
        {(['all', 'info', 'success', 'warning', 'error'] as const).map(level => (
          <button
            key={level}
            onClick={() => setFilter(level)}
            className={`px-4 py-2 rounded-lg text-sm transition-all ${
              filter === level
                ? 'bg-gradient-to-r from-blue-500 to-blue-600 text-white shadow-lg shadow-blue-500/25'
                : 'bg-slate-800/50 text-slate-400 hover:bg-slate-700/50 border border-slate-700/50'
            }`}
          >
            {level.charAt(0).toUpperCase() + level.slice(1)}
          </button>
        ))}
      </div>

      {/* Logs container */}
      <div className="bg-slate-950/80 backdrop-blur-sm border border-slate-800/50 rounded-xl overflow-hidden shadow-inner">
        <div className="h-[500px] overflow-y-auto p-4 font-mono text-sm">
          {filteredLogs.length === 0 ? (
            <div className="flex items-center justify-center h-full text-slate-500">
              No logs to display
            </div>
          ) : (
            filteredLogs.map(log => (
              <div key={log.id} className="mb-2 hover:bg-slate-800/30 p-2.5 rounded-lg transition-all">
                <div className="flex items-start gap-3">
                  <span className="text-slate-500 text-xs whitespace-nowrap">{log.timestamp}</span>
                  <span className={`px-2.5 py-1 rounded-md text-xs border ${getLevelBg(log.level)} ${getLevelColor(log.level)} uppercase whitespace-nowrap font-medium`}>
                    {log.level}
                  </span>
                  <span className="text-slate-300 text-xs leading-relaxed">{log.message}</span>
                </div>
              </div>
            ))
          )}
          <div ref={logsEndRef} />
        </div>
      </div>
    </div>
  );
}