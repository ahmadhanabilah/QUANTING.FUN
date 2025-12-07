import { useMemo, useState } from "react";
import { AlertCircle, RefreshCcw, Save } from "lucide-react";

type SymbolCfg = Record<string, any> & {
  SYMBOL_LIGHTER: string;
  SYMBOL_EXTENDED: string;
};

type BotSettingsProps = {
  pairLabel: string;
  draft: SymbolCfg;
  numberFields: string[];
  boolFields: string[];
  onSymbolChange?: (key: "SYMBOL_LIGHTER" | "SYMBOL_EXTENDED", value: string) => void;
  onNumberChange: (key: string, value: number | "") => void;
  onToggle: (key: string, next: boolean) => void;
  onReset: () => void;
  onSave: () => Promise<boolean> | boolean;
};

const descriptions: Record<string, string> = {
  MIN_SPREAD: "Minimum profitable spread before taking a trade",
  SPREAD_TP: "Target spread to capture before exiting",
  REPRICE_TICK: "Differences (Ticks) for process Top Orderbook",
  MAX_TRADE_VALUE: "Maximum notional per trade (per leg)",
  MAX_OF_OB: "Max fraction of top-of-book depth to take (0-1)",
  MAX_POSITION_VALUE: "Maximum exposure allowed for this pair",
  MAX_TRADES: "Optional cap on concurrent trades, make it blank for Unlimited",
  MIN_HITS: "Minimum Consecutive Spread Condition Hits",
  TEST_MODE: "Run in paper mode without real orders",
  DEDUP_OB: "If On, identical top-of-book snapshots won't be reprocessed",
  WARM_UP_ORDERS: "Send a tiny hedged TT once books are live to warm connections",
};

const tips = [
  "Account for venue fees when tuning MIN_SPREAD and SPREAD_TP.",
  "Keep REPRICE_TICK low for fast venues, higher for noisy books.",
  "Set MAX_POSITION_VALUE to guard against runaway exposure.",
  "Use TEST_MODE to validate new pairs before going live.",
];

export function BotSettings({ pairLabel, draft, numberFields, boolFields, onNumberChange, onToggle, onReset, onSave, onSymbolChange }: BotSettingsProps) {
  const [saving, setSaving] = useState(false);

  const toBool = (value: any) => value === true || value === "true" || value === 1 || value === "1";
  const toNumber = (value: any) => {
    if (value === "" || value === null || value === undefined) return "";
    const num = Number(value);
    return Number.isNaN(num) ? "" : num;
  };

  const numberSettings = useMemo(
    () =>
      numberFields.map((key) => ({
        key,
        label: key.replace(/_/g, " "),
        description: descriptions[key] || "Tune this parameter for the pair",
        value: toNumber((draft as any)[key]),
      })),
    [draft, numberFields]
  );

  const boolSettings = useMemo(
    () =>
      boolFields.map((key) => ({
        key,
        label: key.replace(/_/g, " "),
        description: descriptions[key] || "Toggle this behavior",
        enabled: toBool((draft as any)[key]),
      })),
    [draft, boolFields]
  );

  async function handleSave() {
    setSaving(true);
    await Promise.resolve(onSave());
    setSaving(false);
  }

  const combinedSettings = useMemo(() => {
    const symbolFields = [
      {
        type: "symbol" as const,
        key: "SYMBOL_LIGHTER",
        label: "SYMBOL LIGHTER",
        description: "Example : BTC",
        value: draft.SYMBOL_LIGHTER,
      },
      {
        type: "symbol" as const,
        key: "SYMBOL_EXTENDED",
        label: "SYMBOL EXTENDED",
        description: "Example : BTC-USD",
        value: draft.SYMBOL_EXTENDED,
      },
    ];
    const numFields = numberSettings.map((n) => ({ ...n, type: "number" as const }));
    return [...symbolFields, ...numFields];
  }, [draft.SYMBOL_EXTENDED, draft.SYMBOL_LIGHTER, numberSettings]);

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <div>
          <h3 className="text-white mb-1 text-lg">CONFIGS</h3>
        </div>
        <div className="flex items-center gap-3">
          <button
            className="flex items-center gap-2 px-3 py-2 bg-slate-800/50 hover:bg-slate-700/50 text-white rounded-lg transition-all text-sm border border-slate-700/50"
            onClick={onReset}
          >
            <RefreshCcw className="w-4 h-4" />
            <span className="hidden sm:inline">Reset</span>
          </button>
          <button
            className="flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-blue-500 to-blue-600 hover:from-blue-600 hover:to-blue-700 text-white rounded-lg transition-all text-sm shadow-lg shadow-blue-500/25 disabled:opacity-60"
            onClick={handleSave}
            disabled={saving}
          >
            <Save className="w-4 h-4" />
            <span className="hidden sm:inline">{saving ? "Saving..." : "Save Changes"}</span>
          </button>
        </div>
      </div>

      <div className="mb-6 p-4 bg-gradient-to-r from-yellow-500/10 to-orange-500/10 border border-yellow-500/30 rounded-xl flex items-start gap-3">
        <AlertCircle className="w-5 h-5 text-yellow-400 flex-shrink-0 mt-0.5" />
        <div>
          <p className="text-yellow-400 text-sm">
            <span className="font-medium">Warning:</span> Changes to these settings will require restarting your bots to take effect.
          </p>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
        {combinedSettings.map((setting) => (
          <div
            key={setting.key}
            className="bg-slate-950/50 backdrop-blur-sm border border-slate-800/50 rounded-xl p-5 hover:border-slate-700/50 transition-all"
          >
            <label className="block text-slate-200 text-sm mb-1 font-medium">{setting.label}</label>
            <p className="text-slate-500 text-xs mb-3">{setting.description}</p>
            <div className="relative">
              {setting.type === "symbol" ? (
                <input
                  type="text"
                  className="w-full px-3 py-2.5 bg-slate-900/50 border border-slate-700/50 rounded-lg text-slate-300 text-sm focus:outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20 transition-all"
                  value={setting.value ?? ""}
                  onChange={(e) => onSymbolChange?.(setting.key as "SYMBOL_LIGHTER" | "SYMBOL_EXTENDED", e.target.value)}
                  disabled={!onSymbolChange}
                />
              ) : (
                <input
                  type="number"
                  className="w-full px-3 py-2.5 bg-slate-900/50 border border-slate-700/50 rounded-lg text-slate-300 text-sm focus:outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20 transition-all"
                  value={setting.value ?? ""}
                  onChange={(e) => onNumberChange(setting.key, e.target.value === "" ? "" : Number(e.target.value))}
                />
              )}
            </div>
          </div>
        ))}
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
        {boolSettings.map((setting) => (
          <div
            key={setting.key}
            className="bg-slate-950/50 backdrop-blur-sm border border-slate-800/50 rounded-xl p-5 hover:border-slate-700/50 transition-all flex items-center justify-between"
          >
            <div>
              <p className="text-slate-200 text-sm font-medium">{setting.label}</p>
              <p className="text-slate-500 text-xs mt-1">{setting.description}</p>
            </div>
            <button
              onClick={() => onToggle(setting.key, !setting.enabled)}
              aria-pressed={setting.enabled}
              className={`inline-flex items-center gap-2 px-4 py-1.5 rounded-full text-xs font-semibold border transition-all ${
                setting.enabled
                  ? "bg-blue-600/20 border-blue-500/60 text-blue-200"
                  : "bg-slate-800/60 border-slate-600 text-slate-200"
              }`}
            >
              <span
                className={`h-2.5 w-2.5 rounded-full ${
                  setting.enabled ? "bg-blue-400 shadow-blue-400/50 shadow" : "bg-slate-500"
                }`}
              />
              {setting.enabled ? "On" : "Off"}
            </button>
          </div>
        ))}
      </div>

      <div className="p-5 bg-gradient-to-br from-blue-500/5 to-purple-500/5 border border-blue-500/20 rounded-xl">
        <h4 className="text-slate-200 text-sm mb-3 font-medium flex items-center gap-2">
          <span className="w-1 h-4 bg-gradient-to-b from-blue-500 to-purple-500 rounded-full" />
          Strategy Tips - Arbitrage
        </h4>
        <ul className="space-y-2 text-slate-400 text-sm">
          {tips.map((tip, idx) => (
            <li key={idx} className="flex items-start gap-2">
              <span className="text-blue-400 mt-0.5">•</span>
              <span>{tip}</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
