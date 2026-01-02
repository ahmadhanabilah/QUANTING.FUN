import { useEffect, useMemo, useState } from "react";
import { AccountSnapshot, AccountPosition } from "../hooks/use-accounts-stream";

type AccountsOverviewProps = {
  accounts: AccountSnapshot[];
  configSymbols: Array<{
    SYM_VENUE1?: string;
    SYM_VENUE2?: string;
    ACC_V1?: string;
    ACC_V2?: string;
    name?: string;
    id?: string;
  }>;
  botMetaByPair: Record<string, { name?: string; id?: string; status?: string }>;
  loading?: boolean;
  error?: string;
  apiBase?: string;
  authHeaders?: Record<string, string>;
  onSelectBot?: (symbolL: string, symbolE: string) => void;
};

const fmtNumber = (value?: number | null, digits = 4) => {
  if (value === null || value === undefined) {
    return "—";
  }
  if (!Number.isFinite(value)) {
    return String(value);
  }
  const str = value.toFixed(digits);
  return str.replace(/\.?0+$/, (match) => (match.includes(".") ? "" : match));
};

const formatMoney = (value?: number | null) => {
  if (value === null || value === undefined) {
    return "—";
  }
  if (!Number.isFinite(value)) {
    return String(value);
  }
  return `$${value.toFixed(2)}`;
};

const stripSymbol = (value?: string) => {
  if (!value) {
    return "";
  }
  const idx = value.indexOf(":");
  return idx >= 0 ? value.slice(idx + 1) : value;
};

const normalizePosition = (pos: AccountPosition) => {
  const qty = typeof pos.qty === "number" ? pos.qty : Number(pos.qty ?? 0);
  const absQty = Math.abs(qty);
  const entryRaw = typeof pos.entry === "number" ? pos.entry : Number(pos.entry ?? 0);
  const entry = Number.isFinite(entryRaw) ? entryRaw : 0;
  const notionalRaw =
    typeof pos.notional === "number" ? pos.notional : Number(pos.notional ?? 0);
  let notional = Number.isFinite(notionalRaw) ? notionalRaw : 0;
  let entryResolved = entry;
  if (!entryResolved && absQty && notional) {
    entryResolved = notional / absQty;
  }
  if (!notional && absQty && entryResolved) {
    notional = absQty * entryResolved;
  }
  return { qty, entry: entryResolved, notional };
};

const calcAvgEntry = (agg: { notional: number; weight: number }) => {
  if (!agg.weight) return null;
  return agg.notional / agg.weight;
};

const calcDeltaSpread = (v1?: { qty: number; entry: number | null }, v2?: { qty: number; entry: number | null }) => {
  if (!v1 || !v2 || v1.entry == null || v2.entry == null) return null;
  if (v1.qty > 0 && v2.qty < 0 && v1.entry) return ((v2.entry - v1.entry) / v1.entry) * 100;
  if (v1.qty < 0 && v2.qty > 0 && v2.entry) return ((v1.entry - v2.entry) / v2.entry) * 100;
  return null;
};

const isImbalanced = (v1Qty: number, v2Qty: number) => {
  const abs1 = Math.abs(v1Qty);
  const abs2 = Math.abs(v2Qty);
  const diff = Math.abs(abs1 - abs2);
  return diff > 0;
};

export function AccountsOverview({
  accounts,
  configSymbols,
  botMetaByPair,
  loading,
  error,
  apiBase,
  authHeaders,
  onSelectBot,
}: AccountsOverviewProps) {
  const [pnlStart, setPnlStart] = useState("");
  const [pnlEnd, setPnlEnd] = useState("");
  const [pnlMsg, setPnlMsg] = useState("");

  useEffect(() => {
    if (!apiBase || !authHeaders) {
      return;
    }
    const loadRange = async () => {
      try {
        const res = await fetch(`${apiBase}/api/accounts/pnl_range`, { headers: authHeaders });
        if (!res.ok) {
          return;
        }
        const data = await res.json();
        if (typeof data?.start_ts === "number") {
          setPnlStart(new Date(data.start_ts * 1000).toISOString().slice(0, 16));
        }
        if (typeof data?.end_ts === "number") {
          setPnlEnd(new Date(data.end_ts * 1000).toISOString().slice(0, 16));
        }
      } catch {
        // ignore
      }
    };
    loadRange();
  }, [apiBase, authHeaders]);

  const submitPnlRange = async (startValue: string, endValue: string, clear = false) => {
    if (!apiBase || !authHeaders) {
      setPnlMsg("Missing API config.");
      return;
    }
    const payload = {
      start_ts: clear || !startValue ? null : Math.floor(new Date(startValue).getTime() / 1000),
      end_ts: clear || !endValue ? null : Math.floor(new Date(endValue).getTime() / 1000),
    };
    try {
      const res = await fetch(`${apiBase}/api/accounts/pnl_range`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const text = await res.text();
        setPnlMsg(text || "Failed to update range.");
        return;
      }
      setPnlMsg(clear ? "PnL range cleared." : "PnL range updated.");
    } catch (err) {
      setPnlMsg(err instanceof Error ? err.message : "Failed to update range.");
    }
  };

  const applyPreset = (days: number | null) => {
    const now = new Date();
    if (days === null) {
      setPnlStart("");
      setPnlEnd("");
      submitPnlRange("", "", true);
      return;
    }
    const start = new Date(now.getTime() - days * 24 * 60 * 60 * 1000);
    const startStr = start.toISOString().slice(0, 16);
    const endStr = now.toISOString().slice(0, 16);
    setPnlStart(startStr);
    setPnlEnd(endStr);
    submitPnlRange(startStr, endStr);
  };

  const positionsByAccountAll = useMemo(() => {
    const map: Record<string, AccountPosition[]> = {};
    accounts.forEach((acc) => {
      const raw = acc.positions || [];
      const filtered = raw.filter((pos) => {
        const qty = typeof pos.qty === "number" ? pos.qty : Number(pos.qty ?? 0);
        return Number.isFinite(qty) && qty !== 0;
      });
      map[acc.name] = filtered;
    });
    return map;
  }, [accounts]);

  const positionsByAccount = useMemo(() => {
    const map: Record<string, AccountPosition[]> = {};
    accounts.forEach((acc) => {
      const raw = acc.positions || [];
      const isLighter = (acc.type || "").toUpperCase().startsWith("LIGHT");
      const filtered = raw.filter((pos) => {
        const qty = typeof pos.qty === "number" ? pos.qty : Number(pos.qty ?? 0);
        if (!Number.isFinite(qty)) {
          return false;
        }
        if (isLighter) {
          return qty > 0;
        }
        return qty !== 0;
      });
      map[acc.name] = filtered;
    });
    return map;
  }, [accounts]);

  const configSymbolSet = useMemo(() => {
    const set = new Set<string>();
    configSymbols.forEach((cfg) => {
      const sym1 = stripSymbol(cfg.SYM_VENUE1).toUpperCase();
      const sym2 = stripSymbol(cfg.SYM_VENUE2).toUpperCase();
      if (sym1) set.add(sym1);
      if (sym2) set.add(sym2);
    });
    return set;
  }, [configSymbols]);

  const aggregatedPairs = useMemo(() => {
    type Agg = { qty: number; notional: number; weight: number };
    const pairMap = new Map<
      string,
      {
        pairId: string;
        pair: string;
        sym1: string;
        sym2: string;
        v1: Agg;
        v2: Agg;
        seen: Set<string>;
        botName: string;
        botId: string;
        status?: string;
      }
    >();

    const addAgg = (agg: Agg, pos: AccountPosition) => {
      const norm = normalizePosition(pos);
      if (!norm.qty) {
        return;
      }
      const weight = Math.abs(norm.qty);
      agg.qty += norm.qty;
      if (norm.entry && weight) {
        agg.notional += norm.entry * weight;
        agg.weight += weight;
      }
    };

    const addFromAccount = (pairKey: string, sideKey: "v1" | "v2", accountName?: string, symbol?: string) => {
      if (!accountName || !symbol) {
        return;
      }
      const entry = pairMap.get(pairKey);
      if (!entry) {
        return;
      }
      const uniqueKey = `${sideKey}:${accountName}:${symbol}`;
      if (entry.seen.has(uniqueKey)) {
        return;
      }
      entry.seen.add(uniqueKey);
      const positions = positionsByAccountAll[accountName] || [];
      const target = symbol.toUpperCase();
      positions.forEach((pos) => {
        if ((pos.symbol || "").toUpperCase() === target) {
          addAgg(entry[sideKey], pos);
        }
      });
    };

    configSymbols.forEach((cfg) => {
      const sym1 = stripSymbol(cfg.SYM_VENUE1);
      const sym2 = stripSymbol(cfg.SYM_VENUE2);
      if (!sym1 || !sym2) {
        return;
      }
      const pairKey = `${sym1}/${sym2}`;
      const pairId = `${cfg.SYM_VENUE1 || sym1}:${cfg.SYM_VENUE2 || sym2}`;
      if (!pairMap.has(pairKey)) {
        const meta = botMetaByPair[pairId] || {};
        pairMap.set(pairKey, {
          pairId,
          pair: pairKey,
          sym1,
          sym2,
          v1: { qty: 0, notional: 0, weight: 0 },
          v2: { qty: 0, notional: 0, weight: 0 },
          seen: new Set(),
          botName: cfg.name || meta.name || sym1,
          botId: cfg.id || meta.id || "—",
          status: meta.status,
        });
      }
      addFromAccount(pairKey, "v1", cfg.ACC_V1, sym1);
      addFromAccount(pairKey, "v2", cfg.ACC_V2, sym2);
    });

    const pairs = Array.from(pairMap.values()).filter(
      (entry) =>
        Math.abs(entry.v1.qty) > 0 ||
        Math.abs(entry.v2.qty) > 0 ||
        entry.v1.weight > 0 ||
        entry.v2.weight > 0
    );
    pairs.sort((a, b) => a.botName.localeCompare(b.botName, undefined, { sensitivity: "base" }));
    return pairs;
  }, [configSymbols, positionsByAccountAll]);

  const restPositions = useMemo(() => {
    return accounts
      .map((acc) => {
        const positions = (positionsByAccount[acc.name] || []).filter((pos) => {
          const symbol = (pos.symbol || "").toUpperCase();
          return symbol && !configSymbolSet.has(symbol);
        });
        return { account: acc, positions };
      })
      .filter((entry) => entry.positions.length > 0);
  }, [accounts, configSymbolSet, positionsByAccount]);

  const netPnl = useMemo(() => {
    let total = 0;
    let seen = false;
    accounts.forEach((acc) => {
      const val = acc.pnl?.total;
      if (typeof val === "number" && Number.isFinite(val)) {
        total += val;
        seen = true;
      }
    });
    return seen ? total : null;
  }, [accounts]);

  const totalBalance = useMemo(() => {
    let total = 0;
    let seen = false;
    accounts.forEach((acc) => {
      const val = acc.balance?.total;
      if (typeof val === "number" && Number.isFinite(val)) {
        total += val;
        seen = true;
      }
    });
    return seen ? total : null;
  }, [accounts]);

  const formatAgg = (agg: { qty: number; notional: number; weight: number }, reduceQty?: number | null) => {
    if (!agg.weight && !agg.qty) {
      return "—";
    }
    const avgEntry = agg.weight ? agg.notional / agg.weight : null;
    const entryLabel = avgEntry ? fmtNumber(avgEntry, 6) : "—";
    const notionalLabel = agg.notional ? formatMoney(agg.notional) : "—";
    const base = `${fmtNumber(agg.qty, 6)} @ ${entryLabel} | ${notionalLabel}`;
    if (!reduceQty) {
      return base;
    }
    return `${base} | Reduce ${fmtNumber(reduceQty, 6)}`;
  };

  return (
    <div className="space-y-8">
      <div>
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-white text-lg">Portfolio</h2>
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className="rounded-md border border-slate-800 bg-slate-900/60 px-2 py-1 text-slate-200">
              {formatMoney(totalBalance)}
            </span>
            <span
              className={`rounded-md border px-2 py-1 ${
                netPnl == null
                  ? "border-slate-800 bg-slate-900/60 text-slate-400"
                  : netPnl >= 0
                    ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-300"
                    : "border-rose-500/30 bg-rose-500/10 text-rose-300"
              }`}
            >
              {netPnl == null
                ? "—"
                : `${netPnl >= 0 ? "+" : "-"}$${Math.abs(netPnl).toFixed(2)}`}
            </span>
          </div>
        </div>
        <div className="mt-3 flex flex-wrap items-end gap-3 text-xs text-slate-300">
          <div className="flex flex-wrap items-center gap-2">
            {[
              { label: "24H", days: 1 },
              { label: "1W", days: 7 },
              { label: "1M", days: 30 },
              { label: "2M", days: 60 },
              { label: "3M", days: 90 },
              { label: "1Y", days: 365 },
              { label: "All Time", days: null },
            ].map((preset) => (
              <button
                key={preset.label}
                type="button"
                className="rounded-md border border-slate-800 bg-slate-900/50 px-2 py-1 text-[11px] text-slate-300 hover:text-white hover:border-slate-500"
                onClick={() => applyPreset(preset.days)}
              >
                {preset.label}
              </button>
            ))}
          </div>
          <div>
            <label className="block text-[11px] text-slate-500">PnL start</label>
            <input
              type="datetime-local"
              value={pnlStart}
              onChange={(e) => setPnlStart(e.target.value)}
              className="mt-1 rounded-md border border-slate-800 bg-slate-950/60 px-2 py-1 text-xs text-slate-200"
            />
          </div>
          <div>
            <label className="block text-[11px] text-slate-500">PnL end</label>
            <input
              type="datetime-local"
              value={pnlEnd}
              onChange={(e) => setPnlEnd(e.target.value)}
              className="mt-1 rounded-md border border-slate-800 bg-slate-950/60 px-2 py-1 text-xs text-slate-200"
            />
          </div>
          <button
            type="button"
            className="rounded-md border border-slate-700 bg-slate-900/80 px-3 py-2 text-xs text-slate-200 hover:border-slate-500"
            onClick={() => submitPnlRange(pnlStart, pnlEnd)}
          >
            Apply
          </button>
          <button
            type="button"
            className="rounded-md border border-slate-800 bg-transparent px-3 py-2 text-xs text-slate-400 hover:text-slate-200"
            onClick={() => {
              setPnlStart("");
              setPnlEnd("");
              submitPnlRange("", "", true);
            }}
          >
            Clear
          </button>
          {pnlMsg && <span className="text-[11px] text-slate-500">{pnlMsg}</span>}
        </div>
      </div>
      {error && <p className="text-sm text-red-400">{error}</p>}
      {loading && <p className="text-sm text-slate-400">Connecting...</p>}
      {!accounts.length && !loading && (
        <div className="text-sm text-slate-400 border border-dashed border-slate-800 rounded-xl p-6 bg-slate-900/40">
          No account data yet.
        </div>
      )}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {accounts.map((acc) => (
          <div
            key={`${acc.type}-${acc.name}`}
            className="rounded-xl border border-slate-800/60 bg-slate-950/50 p-4 space-y-3"
          >
            <div className="flex items-center justify-between gap-3">
              <div>
                <p className="text-white text-sm">{acc.name}</p>
                <p className="text-xs text-slate-400">{acc.type}</p>
              </div>
              <div className="text-right">
                <p className="text-xs text-slate-400">Total</p>
                <p className="text-white text-sm">{formatMoney(acc.balance?.total ?? null)}</p>
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-4 text-xs text-slate-300">
              <span>Available: {formatMoney(acc.balance?.available ?? null)}</span>
              <span>
                PnL:{" "}
                <span
                  className={
                    acc.pnl?.total == null
                      ? "text-slate-400"
                      : acc.pnl.total >= 0
                        ? "text-emerald-300"
                        : "text-rose-300"
                  }
                >
                  {formatMoney(acc.pnl?.total ?? null)}
                </span>
              </span>
              {acc.error && <span className="text-red-400">Error: {acc.error}</span>}
            </div>
          </div>
        ))}
      </div>

      <div className="space-y-3">
        <div>
          <h2 className="text-white text-lg mb-1">Aggregated Positions</h2>
          <p className="text-xs text-slate-400">Grouped by configured bot symbols.</p>
        </div>
        {!aggregatedPairs.length ? (
          <div className="text-sm text-slate-400 border border-dashed border-slate-800 rounded-xl p-6 bg-slate-900/40">
            No aggregated positions yet.
          </div>
        ) : (
          <div className="rounded-xl border border-slate-800/60 bg-slate-950/50 overflow-x-auto">
            <table className="w-full min-w-[720px] text-xs table-mono">
              <thead className="bg-slate-900/80">
                <tr>
                  <th className="px-4 py-2 text-left text-slate-400 uppercase tracking-wide">Bot</th>
                  <th className="px-4 py-2 text-left text-slate-400 uppercase tracking-wide">Status</th>
                  <th className="px-4 py-2 text-left text-slate-400 uppercase tracking-wide">Venue 1</th>
                  <th className="px-4 py-2 text-left text-slate-400 uppercase tracking-wide">Venue 2</th>
                  <th className="px-4 py-2 text-left text-slate-400 uppercase tracking-wide">Delta Spread</th>
                </tr>
              </thead>
              <tbody>
                {aggregatedPairs.map((entry) => {
                  const v1Entry = calcAvgEntry(entry.v1);
                  const v2Entry = calcAvgEntry(entry.v2);
                  const deltaSpread = calcDeltaSpread(
                    { qty: entry.v1.qty, entry: v1Entry },
                    { qty: entry.v2.qty, entry: v2Entry }
                  );
                  const imbalance = isImbalanced(entry.v1.qty, entry.v2.qty);
                  const diffQty = Math.abs(entry.v1.qty) - Math.abs(entry.v2.qty);
                  const reduceV1 = imbalance && diffQty > 0 ? Math.abs(diffQty) : null;
                  const reduceV2 = imbalance && diffQty < 0 ? Math.abs(diffQty) : null;
                  const venueClass = imbalance ? "text-red-300" : "text-slate-300";
                  return (
                    <tr
                      key={entry.pair}
                      className={`border-t border-slate-800 ${onSelectBot ? "cursor-pointer hover:bg-slate-900/60" : ""}`}
                      onClick={() => onSelectBot?.(entry.sym1, entry.sym2)}
                    >
                      <td className="px-4 py-2 text-slate-200">
                        <div className="text-slate-200">{entry.botName}</div>
                        <div className="text-[11px] text-slate-400">{entry.botId}</div>
                      </td>
                      <td className="px-4 py-2 text-slate-300">
                        <span className={entry.status === "running" ? "text-emerald-300" : "text-slate-400"}>
                          {entry.status === "running" ? "On" : "Off"}
                        </span>
                      </td>
                      <td className={`px-4 py-2 ${venueClass}`}>{formatAgg(entry.v1, reduceV1)}</td>
                      <td className={`px-4 py-2 ${venueClass}`}>{formatAgg(entry.v2, reduceV2)}</td>
                      <td className="px-4 py-2 text-slate-300">
                        {deltaSpread == null ? "—" : `${fmtNumber(deltaSpread, 2)}%`}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="space-y-3">
        <div>
          <h2 className="text-white text-lg mb-1">Rest Positions</h2>
          <p className="text-xs text-slate-400">Positions non-Bots.</p>
        </div>
        {!restPositions.length ? (
          <div className="text-sm text-slate-400 border border-dashed border-slate-800 rounded-xl p-6 bg-slate-900/40">
            No extra positions.
          </div>
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {restPositions.map((entry) => (
              <div
                key={`${entry.account.type}-${entry.account.name}`}
                className="rounded-xl border border-slate-800/60 bg-slate-950/50 p-4 space-y-3"
              >
                <div>
                  <p className="text-white text-sm">{entry.account.name}</p>
                  <p className="text-xs text-slate-400">{entry.account.type}</p>
                </div>
                <div className="space-y-1">
                  {entry.positions.map((pos, idx) => {
                    const norm = normalizePosition(pos);
                    return (
                      <div key={`${entry.account.name}-${pos.symbol}-${idx}`} className="text-xs text-slate-300">
                        <span className="text-slate-200">{pos.symbol || "—"}</span>{" "}
                        <span>{fmtNumber(norm.qty, 6)}</span> @{" "}
                        <span>{fmtNumber(norm.entry, 6)}</span>{" "}
                        <span className="text-slate-500">| {formatMoney(norm.notional)}</span>
                      </div>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

    </div>
  );
}
