import { Power, TrendingUp, TrendingDown, Activity, ChevronRight } from 'lucide-react';

interface BotCardProps {
  bot: {
    id: string;
    name: string;
    status: 'running' | 'stopped';
    pair: string;
    L?: string;
    E?: string;
    profit24h: number;
    trades24h: number;
  };
  onToggle: () => void;
  onView?: () => void;
}

export function BotCard({ bot, onToggle, onView }: BotCardProps) {
  const isRunning = bot.status === 'running';

  return (
    <div className="group relative bg-gradient-to-br from-slate-900 to-slate-900/50 border border-slate-800/50 rounded-xl p-6 hover:border-slate-700/50 hover:shadow-lg hover:shadow-blue-500/5 transition-all">
      {/* Gradient overlay on hover */}
      <div className="absolute inset-0 bg-gradient-to-br from-blue-500/0 to-purple-500/0 group-hover:from-blue-500/5 group-hover:to-purple-500/5 rounded-xl transition-all" />
      
      <div className="relative">
        <div className="flex items-start justify-between mb-4">
          <div className="flex-1">
            <div className="flex items-center gap-2 mb-2">
              <h3 className="text-white text-lg">{bot.L || bot.name}</h3>
              <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs ${
                isRunning 
                  ? 'bg-green-500/10 text-green-400 border border-green-500/30 shadow-lg shadow-green-500/10' 
                  : 'bg-slate-700/50 text-slate-400 border border-slate-600/50'
              }`}>
                <div className={`w-1.5 h-1.5 rounded-full ${isRunning ? 'bg-green-400 animate-pulse' : 'bg-slate-500'}`} />
                {isRunning ? 'Running' : 'Stopped'}
              </span>
            </div>
            <p className="text-slate-400 text-sm font-mono">{bot.E || bot.pair}</p>
          </div>
          <button
            onClick={(e) => {
              e.stopPropagation();
              onToggle();
            }}
            className={`p-2.5 rounded-xl transition-all ${
              isRunning
                ? 'bg-green-500/10 text-green-400 hover:bg-green-500/20 border border-green-500/20 shadow-lg shadow-green-500/10'
                : 'bg-slate-800/50 text-slate-400 hover:bg-slate-700/50 border border-slate-700/50'
            }`}
          >
            <Power className="w-5 h-5" />
          </button>
        </div>

        <div className="grid grid-cols-2 gap-3 mb-4">
          <div className="bg-slate-950/50 backdrop-blur-sm border border-slate-800/50 rounded-lg p-4">
            <div className="flex items-center gap-1.5 mb-2">
              {bot.profit24h >= 0 ? (
                <TrendingUp className="w-4 h-4 text-green-400" />
              ) : (
                <TrendingDown className="w-4 h-4 text-red-400" />
              )}
              <span className="text-slate-400 text-xs">24h Profit</span>
            </div>
            <p className={`text-xl ${bot.profit24h >= 0 ? 'text-green-400' : 'text-red-400'}`}>
              {bot.profit24h >= 0 ? '+' : ''}{bot.profit24h.toFixed(2)}%
            </p>
          </div>
          <div className="bg-slate-950/50 backdrop-blur-sm border border-slate-800/50 rounded-lg p-4">
            <div className="flex items-center gap-1.5 mb-2">
              <Activity className="w-4 h-4 text-blue-400" />
              <span className="text-slate-400 text-xs">24h Trades</span>
            </div>
            <p className="text-xl text-white">{bot.trades24h}</p>
          </div>
        </div>

        <button
          onClick={() => onView && onView()}
          className="w-full flex items-center justify-center gap-2 px-4 py-2.5 bg-gradient-to-r from-blue-500/10 to-purple-500/10 hover:from-blue-500/20 hover:to-purple-500/20 text-slate-200 hover:text-white border border-blue-500/20 hover:border-blue-500/30 rounded-lg transition-all text-sm"
        >
          View Details
          <ChevronRight className="w-4 h-4" />
        </button>
      </div>
    </div>
  );
}
