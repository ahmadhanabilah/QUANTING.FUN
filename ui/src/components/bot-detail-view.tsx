import { useCallback, useEffect, useMemo, useState } from 'react';
import { TrendingUp, Settings, ListChecks, LayoutDashboard, Power, Trash2 } from 'lucide-react';
import { DecisionsTable } from './decisions-table';
import { TradesTable } from './trades-table';
import { BotSettings } from './bot-settings';

const NUMBER_FIELDS = ['MIN_SPREAD', 'SPREAD_TP', 'REPRICE_TICK', 'MAX_POSITION_VALUE', 'MAX_TRADE_VALUE', 'MAX_OF_OB', 'MAX_TRADES', 'MIN_HITS'];
const BOOL_FIELDS = ['TEST_MODE', 'DEDUP_OB', 'WARM_UP_ORDERS'];

export type DetailTab = 'dashboard' | 'decisions' | 'trades' | 'settings';

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
  onBack?: () => void;
  initialTab?: DetailTab;
  onTabChange?: (tab: DetailTab) => void;
  onClose?: () => void;
  onModeChange?: (mode: 'live' | 'test') => void;
  onSettingsSaved?: (draft: any) => void;
  onToggle?: () => void;
  onDelete?: () => void;
}

export function BotDetailView({
  bot,
  apiBase,
  authHeaders,
  mode,
  onBack,
  initialTab = 'dashboard',
  onTabChange,
  onClose,
  onModeChange,
  onSettingsSaved,
  onToggle,
  onDelete,
}: BotDetailViewProps) {
  const [activeTab, setActiveTab] = useState<DetailTab>(initialTab);
  const [configSymbols, setConfigSymbols] = useState<any[]>([]);
  const [settingsDraft, setSettingsDraft] = useState<any | null>(null);
  const [settingsOriginal, setSettingsOriginal] = useState<any | null>(null);
  const [settingsError, setSettingsError] = useState('');
  const [settingsLoading, setSettingsLoading] = useState(false);
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
    { id: 'dashboard' as DetailTab, label: 'Dashboard', icon: LayoutDashboard },
    { id: 'decisions' as DetailTab, label: 'Decisions', icon: ListChecks },
    { id: 'trades' as DetailTab, label: 'Trades', icon: TrendingUp },
    { id: 'settings' as DetailTab, label: 'Bot Settings', icon: Settings },
  ];

  const statusStyles =
    bot.status === 'running'
      ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300'
      : 'border-rose-500/30 bg-rose-500/10 text-rose-200';

  const formattedProfit = (bot.profit24h ?? 0).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  const formattedTrades = (bot.trades24h ?? 0).toLocaleString();

  return (
    <div className="flex flex-col min-h-[calc(100vh-200px)] min-h-0">
      {/* Tabs */}
      <div className="bg-slate-900/30 backdrop-blur-sm border border-slate-800/50 rounded-t-xl overflow-hidden">
        <nav className="flex flex-wrap gap-1 px-2 pt-2 w-full justify-between sm:justify-start">
          {tabs.map(tab => {
            const Icon = tab.icon;
            return (
              <button
                key={tab.id}
                onClick={(e) => {
                  e.currentTarget.blur();
                  setActiveTab(tab.id);
                  onTabChange?.(tab.id);
                }}
                className={`outline-none focus:outline-none flex items-center gap-2 px-4 py-3 rounded-t-xl transition-all w-full flex-1 justify-center sm:w-auto sm:flex-none sm:justify-start ${
                  activeTab === tab.id
                    ? 'bg-slate-900 text-white shadow-lg'
                    : 'text-slate-400 hover:text-slate-300 hover:bg-slate-800/30'
                }`}
              >
                <Icon className="w-4 h-4" />
                <span className="hidden sm:inline">{tab.label}</span>
              </button>
            );
          })}
        </nav>
      </div>

      {/* Tab content */}
      <div className="bg-gradient-to-br from-slate-900 to-slate-900/50 border border-slate-800/50 border-t-0 rounded-b-xl p-6 shadow-xl flex-1 flex flex-col">
        {activeTab === 'dashboard' && (
          <div className="flex flex-1 md:items-center md:justify-center">
            <div className="space-y-4 md:max-w-6xl md:mx-auto w-full">
            <div className="grid grid-cols-2 gap-4">
              <div className="border border-slate-800/70 rounded-xl p-8 bg-slate-950/60 flex flex-col gap-4 min-h-[180px] shadow-lg shadow-black/30 justify-center">
                <p className="text-xs uppercase tracking-wide text-slate-400">Start / Stop</p>
                <button
                  type="button"
                  onClick={() => onToggle?.()}
                  className={`flex items-center justify-center gap-2 px-3 py-2 rounded-lg border text-sm font-semibold transition-all ${
                    bot.status === 'running'
                      ? 'border-rose-500/60 text-rose-200 hover:bg-rose-500/20'
                      : 'border-emerald-500/60 text-emerald-200 hover:bg-emerald-500/20'
                  }`}
                >
                  <Power className="w-4 h-4" />
                  {bot.status === 'running' ? 'Stop Bot' : 'Start Bot'}
                </button>
              </div>
              <div className="border border-slate-800/70 rounded-xl p-8 bg-slate-950/60 flex flex-col gap-4 min-h-[180px] shadow-lg shadow-black/30 justify-center">
                <p className="text-xs uppercase tracking-wide text-slate-400">Delete Bot</p>
                <button
                  type="button"
                  onClick={() => onDelete?.()}
                  className="flex items-center justify-center gap-2 px-3 py-2 rounded-lg border border-red-500/60 text-red-200 hover:bg-red-500/20 text-sm font-semibold transition-all"
                >
                  <Trash2 className="w-4 h-4" />
                  Delete
                </button>
              </div>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
              <div className="border border-slate-800/70 rounded-xl p-8 bg-slate-950/60 flex flex-col justify-center min-h-[180px] gap-4 shadow-lg shadow-black/30">
                <p className="text-xs uppercase tracking-wide text-slate-400 mb-2">Status</p>
                <span className={`inline-flex items-center justify-center px-3 py-1 rounded-full border text-sm font-medium ${statusStyles}`}>
                  {bot.status === 'running' ? 'Running' : 'Stopped'}
                </span>
              </div>
              <div className="border border-slate-800/70 rounded-xl p-8 bg-slate-950/60 min-h-[180px] flex flex-col justify-center gap-2 shadow-lg shadow-black/30">
                <p className="text-xs uppercase tracking-wide text-slate-400 mb-1">24h Profit</p>
                <p className="text-2xl font-semibold text-white">{formattedProfit}</p>
              </div>
              <div className="border border-slate-800/70 rounded-xl p-8 bg-slate-950/60 min-h-[180px] flex flex-col justify-center gap-2 shadow-lg shadow-black/30">
                <p className="text-xs uppercase tracking-wide text-slate-400 mb-1">24h Trades</p>
                <p className="text-2xl font-semibold text-white">{formattedTrades}</p>
              </div>
            </div>
          </div>
          </div>
        )}
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
