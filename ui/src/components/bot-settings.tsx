import { useMemo, useState } from "react";
import { AlertCircle, RefreshCcw, Save } from "lucide-react";

type SymbolCfg = Record<string, any> & {
  name?: string;
  id?: string;
  SYM_VENUE1: string;
  SYM_VENUE2: string;
  VENUE1?: string;
  VENUE2?: string;
  ACC_V1?: string;
  ACC_V2?: string;
  INV_LEVEL_TO_MULT?: number;
  SPREAD_MULTIPLIER?: number;
};

type BotSettingsProps = {
  pairLabel: string;
  draft: SymbolCfg;
  numberFields: string[];
  boolFields: string[];
  accountOptions: Array<{ name: string; type: string }>;
  onSymbolChange?: (key: "SYM_VENUE1" | "SYM_VENUE2" | "ACC_V1" | "ACC_V2" | "name", value: string) => void;
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
  SLIPPAGE: "Slippage applied to market TOTAL legs (as a decimal, e.g. 0.04 = 4%)",
  ORDER_HEARTBEAT_ENABLED: "Enable the 80% below bid send_market heartbeat",
  ORDER_HEARTBEAT_INTERVAL: "Interval (seconds) between heartbeat orders",
  INV_LEVEL_TO_MULT: "Levels to scale through before hitting the ultimate spread cap",
  SPREAD_MULTIPLIER: "Factor to grow MIN_SPREAD by at each inventory level",
};

const formatDecimal = (value: number) => {
  if (!Number.isFinite(value)) return "—";
  const abs = Math.abs(value);
  const precision = abs >= 1000 ? 0 : abs >= 100 ? 1 : abs >= 10 ? 2 : 4;
  const formatted = value.toFixed(precision);
  return precision > 0 ? formatted.replace(/\.?0+$/, "") : formatted;
};

const buildDcaTips = (draft: SymbolCfg) => {
  const minSpread = Number(draft.MIN_SPREAD ?? 0);
  const maxPosition = Number(draft.MAX_POSITION_VALUE ?? 0);
  const rawLevels = Number(draft.INV_LEVEL_TO_MULT ?? 0);
  const multiplier = Number(draft.SPREAD_MULTIPLIER ?? 1) || 1;
  const levelCount = Math.max(0, Math.floor(rawLevels));

  if (!levelCount || maxPosition <= 0) {
    return [
      {
        range: `$0 - $${formatDecimal(maxPosition)}`,
        spread: formatDecimal(minSpread),
      },
    ];
  }

  const step = maxPosition / levelCount;
  if (!(step > 0) || !Number.isFinite(step)) {
    return [
      {
        range: `$0 - $${formatDecimal(maxPosition)}`,
        spread: formatDecimal(minSpread),
      },
    ];
  }

  const tips: { range: string; spread: string }[] = [];
  for (let level = 0; level < levelCount; level += 1) {
    const lower = level * step;
    const upper = Math.min((level + 1) * step, maxPosition);
    const lowerLabel = formatDecimal(level === 0 ? 0 : lower);
    const upperLabel = formatDecimal(upper);
    const spreadValue = minSpread * Math.pow(multiplier, level);
    tips.push({
      range: `$${lowerLabel} - $${upperLabel}`,
      spread: formatDecimal(spreadValue),
    });
  }

  return tips;
};

export function BotSettings({ pairLabel, draft, numberFields, boolFields, accountOptions, onNumberChange, onToggle, onReset, onSave, onSymbolChange }: BotSettingsProps) {
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

  const dcaTips = useMemo(
    () =>
      buildDcaTips(draft),
    [draft.INV_LEVEL_TO_MULT, draft.MAX_POSITION_VALUE, draft.MIN_SPREAD, draft.SPREAD_MULTIPLIER]
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
        key: "name",
        label: "BOT NAME",
        description: "Display name for this bot",
        value: draft.name || "",
      },
      {
        type: "account" as const,
        key: "ACC_V1",
        label: "VENUE 1 ACCOUNT",
        description: "Select the account for Venue 1",
        value: draft.ACC_V1 || "",
      },
      {
        type: "symbol" as const,
        key: "SYM_VENUE1",
        label: "VENUE 1 SYMBOL",
        description: "Example: BTC",
        value: draft.SYM_VENUE1,
      },
      {
        type: "account" as const,
        key: "ACC_V2",
        label: "VENUE 2 ACCOUNT",
        description: "Select the account for Venue 2",
        value: draft.ACC_V2 || "",
      },
      {
        type: "symbol" as const,
        key: "SYM_VENUE2",
        label: "VENUE 2 SYMBOL",
        description: "Example: BTC-USD",
        value: draft.SYM_VENUE2,
      },
    ];
    const numFields = numberSettings.map((n) => ({ ...n, type: "number" as const }));
    return [...symbolFields, ...numFields];
  }, [draft.SYM_VENUE2, draft.SYM_VENUE1, draft.ACC_V1, draft.ACC_V2, numberSettings]);

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
                  onChange={(e) => onSymbolChange?.(setting.key as "SYM_VENUE1" | "SYM_VENUE2", e.target.value)}
                  disabled={!onSymbolChange}
                />
              ) : setting.type === "account" ? (
                <select
                  className="w-full px-3 py-2.5 bg-slate-900/70 border border-slate-700 text-slate-200 text-sm rounded-lg focus:outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-400/30 transition-all"
                  value={setting.value ?? ""}
                  onChange={(e) => onSymbolChange?.(setting.key as "ACC_V1" | "ACC_V2", e.target.value)}
                  disabled={!onSymbolChange}
                >
                  <option value="" disabled hidden>
                    Select account
                  </option>
                  {accountOptions.map((opt) => (
                    <option key={`${opt.name}-${opt.type}`} value={opt.name}>
                      {opt.name} ({opt.type})
                    </option>
                  ))}
                </select>
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
          DCA MIN_SPREAD Preview
        </h4>
        <div className="overflow-x-auto">
          <table className="w-full text-slate-400 text-sm table-fixed min-w-[360px] border border-slate-800 rounded-lg">
            <thead className="bg-slate-950 text-xs uppercase text-slate-400">
              <tr>
                <th className="px-3 py-2 text-left font-semibold border-b border-slate-800">Range</th>
                <th className="px-3 py-2 text-left font-semibold border-b border-slate-800">MIN_SPREAD</th>
              </tr>
            </thead>
            <tbody>
              {dcaTips.map((tip, idx) => {
                return (
                  <tr key={idx} className={idx % 2 === 0 ? "bg-slate-950/40" : ""}>
                    <td className="px-3 py-2 text-xs font-medium text-white">{tip.range}</td>
                    <td className="px-3 py-2 text-xs">{tip.spread}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
