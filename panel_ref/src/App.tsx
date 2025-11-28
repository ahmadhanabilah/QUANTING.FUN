import { useState } from 'react';
import { Activity, Settings, ArrowLeft } from 'lucide-react';
import { BotCard } from './components/bot-card';
import { BotDetailView } from './components/bot-detail-view';
import { SettingsPanel } from './components/settings-panel';

type Tab = 'dashboard' | 'settings';

export default function App() {
  const [activeTab, setActiveTab] = useState<Tab>('dashboard');
  const [selectedBotId, setSelectedBotId] = useState<string | null>(null);
  const [bots, setBots] = useState([
    { id: '1', name: 'BTC Scalper', status: 'running', pair: 'BTC/USDT', profit24h: 2.34, trades24h: 47 },
    { id: '2', name: 'ETH Grid Bot', status: 'stopped', pair: 'ETH/USDT', profit24h: 0, trades24h: 0 },
    { id: '3', name: 'SOL DCA', status: 'running', pair: 'SOL/USDT', profit24h: 1.87, trades24h: 12 },
    { id: '4', name: 'MATIC Arbitrage', status: 'running', pair: 'MATIC/USDT', profit24h: 0.92, trades24h: 23 },
  ]);

  const toggleBot = (id: string) => {
    setBots(bots.map(bot => 
      bot.id === id 
        ? { ...bot, status: bot.status === 'running' ? 'stopped' : 'running' as 'running' | 'stopped' }
        : bot
    ));
  };

  const totalProfit = bots.reduce((sum, bot) => sum + bot.profit24h, 0);
  const activeBots = bots.filter(bot => bot.status === 'running').length;
  const selectedBot = bots.find(bot => bot.id === selectedBotId);

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-950 via-slate-900 to-slate-950">
      {/* Header */}
      <header className="bg-slate-900/80 backdrop-blur-xl border-b border-slate-800/50 sticky top-0 z-10">
        <div className="px-6 py-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-4">
              {selectedBot && (
                <button
                  onClick={() => setSelectedBotId(null)}
                  className="p-2 hover:bg-slate-800/50 rounded-lg transition-all text-slate-400 hover:text-white"
                >
                  <ArrowLeft className="w-5 h-5" />
                </button>
              )}
              <div>
                <h1 className="text-white bg-gradient-to-r from-white to-slate-300 bg-clip-text">
                  {selectedBot ? selectedBot.name : 'Trading Bot Manager'}
                </h1>
                <p className="text-slate-400 text-sm mt-1">
                  {selectedBot ? selectedBot.pair : 'Manage and monitor your trading bots'}
                </p>
              </div>
            </div>
            <div className="flex gap-4">
              <div className="bg-slate-800/50 backdrop-blur-sm border border-slate-700/50 rounded-xl px-4 py-3">
                <p className="text-slate-400 text-xs mb-0.5">Active Bots</p>
                <p className="text-white text-xl">{activeBots}<span className="text-slate-500">/{bots.length}</span></p>
              </div>
              <div className="bg-gradient-to-br from-green-500/10 to-emerald-500/10 border border-green-500/20 rounded-xl px-4 py-3">
                <p className="text-slate-400 text-xs mb-0.5">24h Profit</p>
                <p className={`text-xl ${totalProfit >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                  {totalProfit >= 0 ? '+' : ''}{totalProfit.toFixed(2)}%
                </p>
              </div>
            </div>
          </div>
        </div>
      </header>

      {/* Navigation Tabs - only show when not viewing bot details */}
      {!selectedBot && (
        <div className="bg-slate-900/30 backdrop-blur-sm border-b border-slate-800/50">
          <div className="px-6">
            <nav className="flex gap-2">
              <button
                onClick={() => setActiveTab('dashboard')}
                className={`flex items-center gap-2 px-4 py-3 border-b-2 transition-all ${
                  activeTab === 'dashboard'
                    ? 'border-blue-500 text-white bg-blue-500/5'
                    : 'border-transparent text-slate-400 hover:text-slate-300 hover:bg-slate-800/30'
                }`}
              >
                <Activity className="w-4 h-4" />
                Dashboard
              </button>
              <button
                onClick={() => setActiveTab('settings')}
                className={`flex items-center gap-2 px-4 py-3 border-b-2 transition-all ${
                  activeTab === 'settings'
                    ? 'border-blue-500 text-white bg-blue-500/5'
                    : 'border-transparent text-slate-400 hover:text-slate-300 hover:bg-slate-800/30'
                }`}
              >
                <Settings className="w-4 h-4" />
                Settings
              </button>
            </nav>
          </div>
        </div>
      )}

      {/* Main Content */}
      <main className="p-6">
        <div className="max-w-7xl mx-auto">
          {selectedBot ? (
            <BotDetailView bot={selectedBot} onToggle={() => toggleBot(selectedBot.id)} />
          ) : (
            <>
              {activeTab === 'dashboard' && (
                <div>
                  <div className="mb-6">
                    <h2 className="text-white mb-1">Active Trading Bots</h2>
                    <p className="text-slate-400 text-sm">Click on a bot to view details, logs, and trades</p>
                  </div>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    {bots.map(bot => (
                      <BotCard 
                        key={bot.id} 
                        bot={bot} 
                        onToggle={() => toggleBot(bot.id)}
                        onView={() => setSelectedBotId(bot.id)}
                      />
                    ))}
                  </div>
                </div>
              )}

              {activeTab === 'settings' && <SettingsPanel />}
            </>
          )}
        </div>
      </main>
    </div>
  );
}