import { useState } from 'react';
import { TrendingUp, TrendingDown, Filter, Download } from 'lucide-react';

interface TradesTableProps {
  botId: string;
  botName: string;
}

const generateMockTrades = (botName: string, pair: string) => [
  { id: 1, timestamp: '2025-11-27 10:26:15', pair, type: 'sell', amount: 0.0045, price: 42245.75, profit: 0.23, status: 'completed' },
  { id: 2, timestamp: '2025-11-27 10:24:12', pair, type: 'buy', amount: 0.0045, price: 42150.50, profit: 0, status: 'completed' },
  { id: 3, timestamp: '2025-11-27 09:45:30', pair, type: 'sell', amount: 0.0032, price: 42180.20, profit: 0.18, status: 'completed' },
  { id: 4, timestamp: '2025-11-27 09:12:45', pair, type: 'buy', amount: 0.0032, price: 42104.50, profit: 0, status: 'completed' },
  { id: 5, timestamp: '2025-11-27 08:55:12', pair, type: 'sell', amount: 0.0038, price: 42095.40, profit: -0.12, status: 'completed' },
  { id: 6, timestamp: '2025-11-27 08:23:50', pair, type: 'buy', amount: 0.0038, price: 42145.80, profit: 0, status: 'completed' },
  { id: 7, timestamp: '2025-11-27 07:48:22', pair, type: 'sell', amount: 0.0052, price: 42320.15, profit: 0.31, status: 'completed' },
  { id: 8, timestamp: '2025-11-27 07:15:05', pair, type: 'buy', amount: 0.0052, price: 42189.40, profit: 0, status: 'completed' },
  { id: 9, timestamp: '2025-11-27 06:42:18', pair, type: 'sell', amount: 0.0041, price: 42055.90, profit: 0.15, status: 'completed' },
  { id: 10, timestamp: '2025-11-27 06:18:33', pair, type: 'buy', amount: 0.0041, price: 41992.75, profit: 0, status: 'completed' },
];

export function TradesTable({ botId, botName }: TradesTableProps) {
  const [filter, setFilter] = useState<'all' | 'buy' | 'sell'>('all');
  
  // Generate trades based on bot name to show relevant data
  const getPair = () => {
    if (botName.includes('BTC')) return 'BTC/USDT';
    if (botName.includes('ETH')) return 'ETH/USDT';
    if (botName.includes('SOL')) return 'SOL/USDT';
    if (botName.includes('MATIC')) return 'MATIC/USDT';
    return 'BTC/USDT';
  };

  const trades = generateMockTrades(botName, getPair());
  const filteredTrades = filter === 'all' ? trades : trades.filter(trade => trade.type === filter);

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2">
            <Filter className="w-4 h-4 text-slate-400" />
            <span className="text-slate-400 text-sm">Type:</span>
            <div className="flex gap-2">
              {(['all', 'buy', 'sell'] as const).map(type => (
                <button
                  key={type}
                  onClick={() => setFilter(type)}
                  className={`px-4 py-2 rounded-lg text-sm transition-all ${
                    filter === type
                      ? 'bg-gradient-to-r from-blue-500 to-blue-600 text-white shadow-lg shadow-blue-500/25'
                      : 'bg-slate-800/50 text-slate-400 hover:bg-slate-700/50 border border-slate-700/50'
                  }`}
                >
                  {type.charAt(0).toUpperCase() + type.slice(1)}
                </button>
              ))}
            </div>
          </div>
        </div>
        <button className="flex items-center gap-2 px-3 py-2 bg-slate-800/50 hover:bg-slate-700/50 text-white rounded-lg transition-all text-sm border border-slate-700/50">
          <Download className="w-4 h-4" />
          Export CSV
        </button>
      </div>

      {/* Table */}
      <div className="bg-slate-950/80 backdrop-blur-sm border border-slate-800/50 rounded-xl overflow-hidden shadow-inner">
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead className="bg-slate-900/80 border-b border-slate-800/50">
              <tr>
                <th className="px-4 py-3 text-left text-slate-400 text-xs uppercase tracking-wider">Timestamp</th>
                <th className="px-4 py-3 text-left text-slate-400 text-xs uppercase tracking-wider">Pair</th>
                <th className="px-4 py-3 text-left text-slate-400 text-xs uppercase tracking-wider">Type</th>
                <th className="px-4 py-3 text-right text-slate-400 text-xs uppercase tracking-wider">Amount</th>
                <th className="px-4 py-3 text-right text-slate-400 text-xs uppercase tracking-wider">Price</th>
                <th className="px-4 py-3 text-right text-slate-400 text-xs uppercase tracking-wider">Profit</th>
                <th className="px-4 py-3 text-center text-slate-400 text-xs uppercase tracking-wider">Status</th>
              </tr>
            </thead>
            <tbody>
              {filteredTrades.map(trade => (
                <tr key={trade.id} className="border-b border-slate-800/30 hover:bg-slate-800/20 transition-all">
                  <td className="px-4 py-4 text-slate-300 text-sm font-mono">{trade.timestamp}</td>
                  <td className="px-4 py-4 text-slate-300 text-sm font-mono">{trade.pair}</td>
                  <td className="px-4 py-4">
                    <span className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium ${
                      trade.type === 'buy'
                        ? 'bg-green-500/10 text-green-400 border border-green-500/30'
                        : 'bg-red-500/10 text-red-400 border border-red-500/30'
                    }`}>
                      {trade.type === 'buy' ? <TrendingUp className="w-3 h-3" /> : <TrendingDown className="w-3 h-3" />}
                      {trade.type.toUpperCase()}
                    </span>
                  </td>
                  <td className="px-4 py-4 text-right text-slate-300 text-sm font-mono">{trade.amount}</td>
                  <td className="px-4 py-4 text-right text-slate-300 text-sm font-mono">${trade.price.toLocaleString()}</td>
                  <td className="px-4 py-4 text-right text-sm font-mono">
                    {trade.profit !== 0 && (
                      <span className={`font-medium ${trade.profit > 0 ? 'text-green-400' : 'text-red-400'}`}>
                        {trade.profit > 0 ? '+' : ''}{trade.profit.toFixed(2)}%
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-4 text-center">
                    <span className="inline-flex px-3 py-1 rounded-full text-xs font-medium bg-green-500/10 text-green-400 border border-green-500/30">
                      {trade.status}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Summary */}
      <div className="mt-4 flex items-center justify-between text-sm bg-slate-800/30 rounded-lg px-4 py-3 border border-slate-700/50">
        <span className="text-slate-400">Showing <span className="text-white font-medium">{filteredTrades.length}</span> trades</span>
        <div className="flex items-center gap-4">
          <span className="text-slate-400">Total Profit:</span>
          <span className="text-green-400 font-medium text-lg">
            +{filteredTrades.reduce((sum, t) => sum + t.profit, 0).toFixed(2)}%
          </span>
        </div>
      </div>
    </div>
  );
}