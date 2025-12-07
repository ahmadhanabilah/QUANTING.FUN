import { Power, TrendingUp, TrendingDown, Activity, ChevronRight, Pin, PinOff } from 'lucide-react';

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
  onPin?: () => void;
  pinned?: boolean;
}

export function BotCard({ bot, onToggle, onView, onPin, pinned }: BotCardProps) {
  const isRunning = bot.status === 'running';

  return (
    <div
      className="group relative bg-gradient-to-br from-slate-900 to-slate-900/50 border border-slate-800/50 rounded-xl p-4 hover:border-slate-700/50 hover:shadow-lg hover:shadow-blue-500/5 transition-all cursor-pointer"
      onClick={() => onView && onView()}
    >
      {/* Gradient overlay on hover */}
      <div className="absolute inset-0 bg-gradient-to-br from-blue-500/0 to-purple-500/0 group-hover:from-blue-500/5 group-hover:to-purple-500/5 rounded-xl transition-all" />
      
      <div className="relative">
        <div className="flex items-start justify-between mb-4">
          <div className="flex-1">
            <div className="flex items-center gap-2">
              <h3 className="text-white text-lg">
                {bot.L || bot.name}
                <span className="text-xs text-slate-400 font-mono ml-2">{bot.E || bot.pair}</span>
              </h3>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {onPin && (
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  onPin();
                }}
                className={`p-2 rounded-lg border transition-all text-xs ${
                  pinned
                    ? 'bg-yellow-500/15 text-yellow-300 border-yellow-500/40'
                    : 'bg-slate-800/50 text-slate-400 hover:text-white hover:bg-slate-700/50 border-slate-700/50'
                }`}
                title={pinned ? 'Unpin bot' : 'Pin bot'}
              >
                {pinned ? <Pin className="w-3 h-3" /> : <PinOff className="w-3 h-3" />}
              </button>
            )}
            <button
              onClick={(e) => {
                e.stopPropagation();
                onToggle();
              }}
              className={`p-2 rounded-lg transition-all text-xs ${
                isRunning
                  ? 'bg-green-500/10 text-green-400 hover:bg-green-500/20 border border-green-500/20'
                  : 'bg-slate-800/50 text-slate-400 hover:bg-slate-700/50 border border-slate-700/50'
              }`}
            >
              <Power className="w-4 h-4" />
            </button>
          </div>
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
