import { useState } from 'react';
import { Power, FileText, TrendingUp, Settings } from 'lucide-react';
import { LogsViewer } from './logs-viewer';
import { TradesTable } from './trades-table';
import { BotSettings } from './bot-settings';

type DetailTab = 'logs' | 'trades' | 'settings';

interface BotDetailViewProps {
  bot: {
    id: string;
    name: string;
    status: 'running' | 'stopped';
    pair: string;
    profit24h: number;
    trades24h: number;
  };
  onToggle: () => void;
}

export function BotDetailView({ bot, onToggle }: BotDetailViewProps) {
  const [activeTab, setActiveTab] = useState<DetailTab>('logs');
  const isRunning = bot.status === 'running';

  const tabs = [
    { id: 'logs' as DetailTab, label: 'Logs', icon: FileText },
    { id: 'trades' as DetailTab, label: 'Trades', icon: TrendingUp },
    { id: 'settings' as DetailTab, label: 'Bot Settings', icon: Settings },
  ];

  return (
    <div>
      {/* Bot status card */}
      <div className="bg-gradient-to-br from-slate-900 to-slate-900/50 border border-slate-800/50 rounded-xl p-6 mb-6 shadow-xl">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-4">
            <div className={`p-4 rounded-xl ${
              isRunning 
                ? 'bg-green-500/10 border border-green-500/20' 
                : 'bg-slate-800/50 border border-slate-700/50'
            }`}>
              <div className="flex items-center gap-2">
                <div className={`w-3 h-3 rounded-full ${isRunning ? 'bg-green-400 animate-pulse' : 'bg-slate-500'}`} />
                <span className={`text-sm ${isRunning ? 'text-green-400' : 'text-slate-400'}`}>
                  {isRunning ? 'Running' : 'Stopped'}
                </span>
              </div>
            </div>
          </div>
          <div className="flex items-center gap-6">
            <div className="text-center px-6 py-3 bg-slate-950/50 rounded-xl border border-slate-800/50">
              <p className="text-slate-400 text-xs mb-1">24h Profit</p>
              <p className={`text-2xl ${bot.profit24h >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                {bot.profit24h >= 0 ? '+' : ''}{bot.profit24h.toFixed(2)}%
              </p>
            </div>
            <div className="text-center px-6 py-3 bg-slate-950/50 rounded-xl border border-slate-800/50">
              <p className="text-slate-400 text-xs mb-1">24h Trades</p>
              <p className="text-2xl text-white">{bot.trades24h}</p>
            </div>
            <button
              onClick={onToggle}
              className={`flex items-center gap-2 px-5 py-3 rounded-xl transition-all shadow-lg ${
                isRunning
                  ? 'bg-red-500/10 text-red-400 hover:bg-red-500/20 border border-red-500/30 shadow-red-500/20'
                  : 'bg-green-500/10 text-green-400 hover:bg-green-500/20 border border-green-500/30 shadow-green-500/20'
              }`}
            >
              <Power className="w-4 h-4" />
              {isRunning ? 'Stop Bot' : 'Start Bot'}
            </button>
          </div>
        </div>
      </div>

      {/* Tabs */}
      <div className="bg-slate-900/30 backdrop-blur-sm border border-slate-800/50 rounded-t-xl overflow-hidden">
        <nav className="flex gap-1 px-2 pt-2">
          {tabs.map(tab => {
            const Icon = tab.icon;
            return (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`flex items-center gap-2 px-5 py-3 rounded-t-xl transition-all ${
                  activeTab === tab.id
                    ? 'bg-slate-900 text-white border-t border-x border-slate-800/50 shadow-lg'
                    : 'text-slate-400 hover:text-slate-300 hover:bg-slate-800/30'
                }`}
              >
                <Icon className="w-4 h-4" />
                {tab.label}
              </button>
            );
          })}
        </nav>
      </div>

      {/* Tab content */}
      <div className="bg-gradient-to-br from-slate-900 to-slate-900/50 border border-slate-800/50 border-t-0 rounded-b-xl p-6 shadow-xl">
        {activeTab === 'logs' && <LogsViewer botId={bot.id} botName={bot.name} />}
        {activeTab === 'trades' && <TradesTable botId={bot.id} botName={bot.name} />}
        {activeTab === 'settings' && <BotSettings botId={bot.id} botName={bot.name} />}
      </div>
    </div>
  );
}