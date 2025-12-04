import { useCallback, useEffect, useMemo, useState } from 'react';
import { Power, TrendingUp, Settings, ListChecks } from 'lucide-react';
import { DecisionsTable } from './decisions-table';
import { TradesTable } from './trades-table';
import { BotSettings } from './bot-settings';

type DetailTab = 'decisions' | 'trades' | 'settings';

const NUMBER_FIELDS = ['MIN_SPREAD', 'SPREAD_TP', 'REPRICE_TICK', 'MAX_POSITION_VALUE', 'MAX_TRADE_VALUE', 'MAX_OF_OB', 'MAX_TRADES', 'MIN_HITS'];
const BOOL_FIELDS = ['TEST_MODE', 'DEDUP_OB', 'WARM_UP_ORDERS'];

export type DetailTab = 'decisions' | 'trades' | 'settings';

interface BotDetailViewProps {
  bot: {
    id: string;
    name: string;
    status: 'running' | 'stopped';
    pair: string;
    profit24h: number;
    trades24h: number;
  };
  apiBase: string;
  authHeaders: Record<string, string>;
  mode: 'live' | 'test';
  onToggle: () => void;
  onBack?: () => void;
  initialTab?: DetailTab;
  onTabChange?: (tab: DetailTab) => void;
  onClose?: () => void;
  onModeChange?: (mode: 'live' | 'test') => void;
  onSettingsSaved?: (draft: any) => void;
}

export function BotDetailView({
  bot,
  apiBase,
  authHeaders,
  mode,
  onToggle,
  onBack,
  initialTab = 'logs',
  onTabChange,
  onClose,
  onModeChange,
  onSettingsSaved,
}: BotDetailViewProps) {
  const [activeTab, setActiveTab] = useState<DetailTab>(initialTab);
  const [configSymbols, setConfigSymbols] = useState<any[]>([]);
  const [settingsDraft, setSettingsDraft] = useState<any | null>(null);
  const [settingsOriginal, setSettingsOriginal] = useState<any | null>(null);
  const [settingsError, setSettingsError] = useState('');
  const [settingsLoading, setSettingsLoading] = useState(false);
  const isRunning = bot.status === 'running';
  const [symbolL, symbolE] = bot.id.split(':');
  const pairLabel = useMemo(() => (symbolL && symbolE ? `${symbolL}/${symbolE}` : bot.name), [symbolE, symbolL, bot.name]);

  const loadPairConfig = useCallback(async () => {
    setSettingsLoading(true);
    if (!symbolL || !symbolE) {
      setSettingsError('Invalid bot identifier for settings');
      setSettingsDraft(null);
      setSettingsOriginal(null);
      setSettingsLoading(false);
      return;
    }
    try {
      const res = await fetch(`${apiBase}/api/config`, { headers: authHeaders });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(typeof data?.detail === 'string' ? data.detail : 'Failed to load config');
      }
      const symbols = Array.isArray(data.symbols) ? data.symbols : [];
      setConfigSymbols(symbols);
      const match = symbols.find(
        (s) => s.SYMBOL_LIGHTER === symbolL && s.SYMBOL_EXTENDED === symbolE
      );
      if (!match) {
        setSettingsError('Pair not found in config.json');
        setSettingsDraft(null);
        setSettingsOriginal(null);
        return;
      }
      setSettingsError('');
      setSettingsDraft(match);
      setSettingsOriginal(match);
    } catch (err) {
      setSettingsError(err instanceof Error ? err.message : 'Failed to load config');
      setSettingsDraft(null);
      setSettingsOriginal(null);
      setConfigSymbols([]);
    } finally {
      setSettingsLoading(false);
    }
  }, [apiBase, authHeaders, symbolE, symbolL]);

  useEffect(() => {
    loadPairConfig();
  }, [loadPairConfig]);

  useEffect(() => {
    setActiveTab(initialTab);
  }, [initialTab]);

  const handleNumberChange = (key: string, value: number | "") => {
    setSettingsDraft((prev: any) => (prev ? { ...prev, [key]: value } : prev));
  };

  const handleSymbolChange = (key: "SYMBOL_LIGHTER" | "SYMBOL_EXTENDED", value: string) => {
    setSettingsDraft((prev: any) => (prev ? { ...prev, [key]: value } : prev));
  };

  const handleToggle = (key: string, next: boolean) => {
    setSettingsDraft((prev: any) => (prev ? { ...prev, [key]: next } : prev));
  };

  const handleReset = () => {
    if (settingsOriginal) {
      setSettingsDraft(settingsOriginal);
      setSettingsError('');
    }
  };

  const handleSave = async () => {
    if (!settingsDraft || !symbolL || !symbolE) {
      setSettingsError('No settings to save');
      return false;
    }
    setSettingsError('');

    const normalizedDraft: any = { ...settingsDraft };
    NUMBER_FIELDS.forEach((field) => {
      if (field in normalizedDraft) {
        const val = normalizedDraft[field];
        normalizedDraft[field] = val === "" || val === null ? null : Number(val);
      }
    });
    BOOL_FIELDS.forEach((field) => {
      if (field in normalizedDraft) {
        normalizedDraft[field] = Boolean(normalizedDraft[field]);
      }
    });

    const updatedSymbols = configSymbols.map((sym) =>
      sym.SYMBOL_LIGHTER === symbolL && sym.SYMBOL_EXTENDED === symbolE
        ? { ...sym, ...normalizedDraft }
        : sym
    );

    try {
      const res = await fetch(`${apiBase}/api/config`, {
        method: 'PUT',
        headers: { ...authHeaders, 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbols: updatedSymbols }),
      });
      if (!res.ok) {
        const text = await res.text();
        throw new Error(text || 'Failed to save config');
      }
      setConfigSymbols(updatedSymbols);
      setSettingsOriginal(normalizedDraft);
      setSettingsDraft(normalizedDraft);
      localStorage.setItem("selectedPair", JSON.stringify({ L: normalizedDraft.SYMBOL_LIGHTER, E: normalizedDraft.SYMBOL_EXTENDED }));
      localStorage.setItem("selectedTab", "settings");
      if (onSettingsSaved) {
        onSettingsSaved(normalizedDraft);
      }
      return true;
    } catch (err) {
      setSettingsError(err instanceof Error ? err.message : 'Failed to save settings');
      return false;
    }
  };

  const tabs = [
    { id: 'decisions' as DetailTab, label: 'Decisions', icon: ListChecks },
    { id: 'trades' as DetailTab, label: 'Trades', icon: TrendingUp },
    { id: 'settings' as DetailTab, label: 'Bot Settings', icon: Settings },
  ];

  return (
    <div>
      {/* Bot status card */}
      <div className="bg-gradient-to-br from-slate-900 to-slate-900/50 border border-slate-800/50 rounded-xl p-6 mb-6 shadow-xl">
        <div className="flex items-center justify-between">
            <div className="flex items-center gap-4">
              {onBack && (
                <button
                  onClick={onBack}
                  className="p-2 rounded-lg border border-slate-800/60 bg-slate-900/60 text-slate-300 hover:text-white hover:border-slate-700/80 transition"
                >
                  <span className="text-xs">Back</span>
                </button>
              )}
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
          <div className="flex items-center gap-6 flex-wrap justify-end">
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
                onClick={() => {
                  setActiveTab(tab.id);
                  onTabChange?.(tab.id);
                }}
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
        {activeTab === 'decisions' && (
          <DecisionsTable botId={bot.id} apiBase={apiBase} authHeaders={authHeaders} mode={mode} onModeChange={onModeChange} />
        )}
        {activeTab === 'trades' && (
          <TradesTable botId={bot.id} botName={bot.name} apiBase={apiBase} authHeaders={authHeaders} mode={mode} onModeChange={onModeChange} />
        )}
        {activeTab === 'settings' && (
            <div className="space-y-3">
              {settingsError && (
                <div className="px-4 py-3 rounded-lg border border-red-500/40 bg-red-500/10 text-red-300 text-sm">
                  {settingsError}
                </div>
              )}
              {settingsLoading && (
                <div className="px-4 py-6 text-slate-400 text-sm">Loading config for {pairLabel}...</div>
              )}
              {!settingsLoading && settingsDraft && (
                <BotSettings
                  pairLabel={pairLabel}
                  draft={settingsDraft}
                  numberFields={NUMBER_FIELDS}
                  boolFields={BOOL_FIELDS}
                  onNumberChange={handleNumberChange}
                  onSymbolChange={handleSymbolChange}
                  onToggle={handleToggle}
                  onReset={handleReset}
                  onSave={handleSave}
                />
              )}
            {!settingsLoading && !settingsDraft && !settingsError && (
              <div className="px-4 py-6 text-slate-400 text-sm">
                No config found for this pair. Please add it to config.json.
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
