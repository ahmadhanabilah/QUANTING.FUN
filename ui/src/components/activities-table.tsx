import { type ReactNode, type UIEvent, useCallback, useEffect, useMemo, useState } from "react";

export type ActivityRow = {
  bot_id?: string;
  trace?: string;
  bot_configs?: Record<string, any>;
  decision_data?: Record<string, any>;
  decision_ob_v1?: Record<string, any>;
  decision_ob_v2?: Record<string, any>;
  trade_v1?: Record<string, any>;
  trade_v2?: Record<string, any>;
  fill_v1?: Record<string, any>;
  fill_v2?: Record<string, any>;
};

type ActivitiesTableProps = {
  activities: ActivityRow[];
  sortFn?: (a: ActivityRow, b: ActivityRow) => number;
  hasMore?: boolean;
  isLoadingMore?: boolean;
  onLoadMore?: () => void;
  onScrollPositionChange?: (atTop: boolean) => void;
};

const defaultSort = (a: ActivityRow, b: ActivityRow) => {
  const aTs = Number(a?.decision_data?.ts ?? 0);
  const bTs = Number(b?.decision_data?.ts ?? 0);
  return bTs - aTs;
};

export function ActivitiesTable({ activities, sortFn, hasMore, isLoadingMore, onLoadMore, onScrollPositionChange }: ActivitiesTableProps) {
  const [expandedTrades, setExpandedTrades] = useState<Record<string, boolean>>({});
  const [atTop, setAtTop] = useState(true);

  const formatTs = (value?: number | string | null) => {
    if (value === null || value === undefined) {
      return "—";
    }
    const num = typeof value === "string" ? Number(value) : value;
    if (!Number.isFinite(num)) {
      return String(value);
    }
    const date = new Date(num * 1000);
    const ms = String(date.getMilliseconds()).padStart(3, "0");
    const base = date.toLocaleString();
    const replaced = base.replace(/(:\d{2})(\s*[AP]M)$/, `$1.${ms}$2`);
    return replaced !== base ? replaced : `${base}.${ms}`;
  };

  const handleScroll = useCallback(
    (event: UIEvent<HTMLDivElement>) => {
      const target = event.currentTarget;
      const newAtTop = target.scrollTop <= 8;
      if (onScrollPositionChange && newAtTop !== atTop) {
        setAtTop(newAtTop);
        onScrollPositionChange(newAtTop);
      }
      const canLoadMore = Boolean(onLoadMore && (hasMore ?? true) && !isLoadingMore);
      if (canLoadMore && target.scrollHeight - target.scrollTop - target.clientHeight < 40) {
        onLoadMore?.();
      }
    },
    [hasMore, isLoadingMore, onLoadMore, onScrollPositionChange, atTop],
  );

  useEffect(() => {
    if (onScrollPositionChange) {
      onScrollPositionChange(true);
    }
  }, [onScrollPositionChange]);

  const parsedActivities = useMemo(() => {
    const sorted = [...activities];
    if (sortFn) {
      sorted.sort(sortFn);
    } else {
      sorted.sort(defaultSort);
    }
    return sorted;
  }, [activities, sortFn]);

  const renderKeyValues = (items: Array<{ label: string; value?: ReactNode }>) => (
    <div className="space-y-1 text-xs">
      {items.map((item) => (
        <p key={item.label} className="text-slate-300">
          <span className="text-slate-400">{item.label}:</span> {item.value ?? "—"}
        </p>
      ))}
    </div>
  );

  const toggleTradeExpansion = (key: string) => {
    setExpandedTrades((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  const formatPayload = (value: any): string | null => {
    if (value === null || value === undefined) {
      return null;
    }
    if (typeof value === "string") {
      return value;
    }
    try {
      return JSON.stringify(value);
    } catch {
      return String(value);
    }
  };

  const renderTrades = (label: string, data: Record<string, any> | null | undefined, keyPrefix: string) => {
    if (!data) {
      return null;
    }
    const payloadStr = formatPayload(data.payload);
    const respStr = formatPayload(data.resp);
    const expanded = expandedTrades[keyPrefix];
    const valueBlock = (value: string | null) => (
      <p className="text-[11px] text-slate-100 font-mono break-words">{value}</p>
    );
    const showToggle = Boolean(payloadStr || respStr);
    return (
      <div key={label} className="rounded-lg bg-slate-900/50 border border-slate-800/60 p-3 mb-2">
        <div className="space-y-1 text-xs">
          <p className="text-slate-300">
            <span className="text-slate-400">Start:</span> {formatTs(data.ts_start)}
          </p>
          <p className="text-slate-300">
            <span className="text-slate-400">End:</span> {formatTs(data.ts_end)}
          </p>
          <p className="text-slate-300">
            <span className="text-slate-400">Size:</span> {data.size ?? "—"}
          </p>
          <p className="text-slate-300">
            <span className="text-slate-400">Status:</span> {data.status ?? "—"}
          </p>
          <p className="text-slate-300">
            <span className="text-slate-400">Reason:</span> {data.reason ?? "—"}
          </p>
          <p className="text-slate-300">
            <span className="text-slate-400">Direction:</span> {data.direction ?? "—"}
          </p>
          <p className="text-slate-300">
            <span className="text-slate-400">Latency:</span>{" "}
            {data.lat ? `${data.lat.toFixed ? data.lat.toFixed(2) : data.lat} ms` : "—"}
          </p>
          {expanded && payloadStr && (
            <div>
              <p className="text-[11px] text-slate-400 uppercase tracking-wide mb-1">Payload</p>
              {valueBlock(payloadStr)}
            </div>
          )}
          {expanded && respStr && (
            <div>
              <p className="text-[11px] text-slate-400 uppercase tracking-wide mb-1">Response</p>
              {valueBlock(respStr)}
            </div>
          )}
        </div>
        {showToggle && (
          <button
            type="button"
            onClick={() => toggleTradeExpansion(keyPrefix)}
            className="mt-2 text-[11px] text-blue-300 hover:text-blue-400 focus:outline-none"
          >
            {expanded ? "Hide" : "Show Details"}
          </button>
        )}
      </div>
    );
  };

  const parseNumber = (value: any) => {
    if (value === null || value === undefined) {
      return null;
    }
    const num = typeof value === "string" ? Number(value) : value;
    return Number.isFinite(num) ? num : null;
  };

  const computeSpreadInv = (snap: Record<string, any>) => {
    const qtyV1 = parseNumber(snap.qty_v1);
    const qtyV2 = parseNumber(snap.qty_v2);
    const priceV1 = parseNumber(snap.price_v1);
    const priceV2 = parseNumber(snap.price_v2);
    if (priceV1 !== null && priceV2 !== null && qtyV1 !== null && qtyV2 !== null) {
      if (qtyV1 > 0 && qtyV2 < 0 && priceV1) {
        return ((priceV2 - priceV1) / priceV1) * 100;
      }
      if (qtyV1 < 0 && qtyV2 > 0 && priceV2) {
        return ((priceV1 - priceV2) / priceV2) * 100;
      }
    }
    return null;
  };

  const formatDecimal = (value: number, digits = 4) => {
    const str = value.toFixed(digits);
    return str.replace(/\.?0+$/, (match) => (match.includes(".") ? "" : match));
  };

  const parseInventorySnapshot = (value: any) => {
    if (value === null || value === undefined) {
      return null;
    }
    if (typeof value === "string") {
      try {
        return JSON.parse(value);
      } catch {
        return null;
      }
    }
    if (typeof value === "object") {
      return value;
    }
    return null;
  };

  const formatInventoryLine = (label: string, qty: any, price: any) => {
    const qtyNum = parseNumber(qty);
    const priceNum = parseNumber(price);
    const qtyDisplay = qtyNum !== null ? qtyNum : qty ?? "—";
    const priceDisplay =
      priceNum !== null ? formatDecimal(priceNum) : price ?? "—";
    const parts = [`${label} : ${qtyDisplay} @ ${priceDisplay}`];
    if (qtyNum !== null && priceNum !== null) {
      const total = Math.abs(qtyNum) * priceNum;
      parts.push(`| $${total.toFixed(2)}`);
    }
    return parts.join(" ");
  };

  const renderInventorySection = (title: string, value: any) => {
    const snap = parseInventorySnapshot(value);
    if (!snap) {
      return (
        <div className="space-y-1">
          <p className="text-[11px] text-slate-400 uppercase tracking-wide">{title}:</p>
          <p className="text-[11px] text-slate-300">—</p>
        </div>
      );
    }
    const delta = computeSpreadInv(snap);
    return (
      <div className="space-y-1">
        <p className="text-[11px] text-slate-400 uppercase tracking-wide">{title}:</p>
        <p className="text-[11px] text-slate-300">{formatInventoryLine("L", snap.qty_v1, snap.price_v1)}</p>
        <p className="text-[11px] text-slate-300">{formatInventoryLine("E", snap.qty_v2, snap.price_v2)}</p>
        <p className="text-[11px] text-slate-300">Δ : {delta !== null ? `${delta.toFixed(2)}%` : "—"}</p>
      </div>
    );
  };

  const formatSlippage = (fill: Record<string, any> | null | undefined, ob: Record<string, any> | null | undefined) => {
    const fillPrice = parseNumber(fill?.fill_price);
    const size = parseNumber(fill?.size);
    if (!ob || fillPrice === null || size === null || size === 0) {
      return "—";
    }
    const obPrice = size > 0 ? parseNumber(ob.askPrice) : parseNumber(ob.bidPrice);
    if (obPrice === null || obPrice === 0) {
      return "—";
    }
    const directionDiff = size > 0 ? fillPrice - obPrice : obPrice - fillPrice;
    const percent = (directionDiff / obPrice) * 100;
    return `${percent.toFixed(2)}%`;
  };

  const renderFills = (
    label: string,
    data: Record<string, any> | null | undefined,
    decisionTs?: number | null,
    ob?: Record<string, any> | null | undefined,
  ) => {
    if (!data) {
      return null;
    }
    const fillTs = typeof data.ts === "string" ? Number(data.ts) : data.ts;
    let latencyValue = "—";
    if (decisionTs !== undefined && decisionTs !== null && fillTs !== undefined && fillTs !== null) {
      const delta = (fillTs - decisionTs) * 1000;
      if (Number.isFinite(delta)) {
        latencyValue = `${delta.toFixed(2)} ms`;
      }
    }
    const slippageValue = formatSlippage(data, ob);
    const rows = [
      { label: "Time", value: formatTs(data.ts) },
      { label: "Size", value: data.size ?? "—" },
      { label: "Fill Price", value: data.fill_price ?? "—" },
      { label: "Slippage", value: slippageValue },
      { label: "Latency", value: latencyValue },
    ];
    return (
      <div key={label} className="rounded-lg bg-slate-900/50 border border-slate-800/60 p-3 mb-2">
        {renderKeyValues(rows)}
      </div>
    );
  };

  if (!parsedActivities.length) {
    return null;
  }

  return (
    <div className="w-full overflow-hidden rounded-2xl border border-slate-800/70 bg-slate-950/40 shadow-inner">
      <div className="max-h-[70vh] overflow-auto relative" onScroll={handleScroll}>
        <table className="table-fixed table-mono w-full min-w-[1100px]">
          <thead className="bg-slate-900/80 border-b border-slate-800/50 sticky top-0 z-10">
            <tr>
            {[
                { label: "Key", width: "16%" },
                { label: "Decision", width: "18%" },
                { label: "Inventory", width: "12%" },
                { label: "Trades V1", width: "16%" },
                { label: "Trades V2", width: "16%" },
                { label: "Fills V1", width: "11%" },
                { label: "Fills V2", width: "11%" },
              ].map((col) => (
                <th
                  key={col.label}
                  className="px-4 py-3 text-left text-slate-400 text-[11px] uppercase tracking-wider whitespace-nowrap"
                  style={{ width: col.width }}
                >
                  {col.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {parsedActivities.map((activity) => {
              const decision = activity.decision_data;
              const tradesV1 = activity.trade_v1;
              const tradesV2 = activity.trade_v2;
              const fillsV1 = activity.fill_v1;
              const fillsV2 = activity.fill_v2;
              const obV1 = activity.decision_ob_v1;
              const obV2 = activity.decision_ob_v2;
              const formatOb = (label: string, ob: Record<string, any> | null | undefined) => {
                const tsVal = ob?.timestamp ?? ob?.ts;
                return {
                  label,
                  value: ob
                    ? `${formatTs(tsVal)} |  ${ob.bidPrice ?? "—"} : ${ob.bidSize ?? "—"} / ${ob.askPrice ?? "—"} : ${ob.askSize ?? "—"}`
                    : "—",
                };
              };
              const decisionRows = [
                { label: "Time", value: formatTs(decision?.ts) },
                { label: "Reason", value: decision?.reason ?? "—" },
                { label: "Direction", value: decision?.direction ?? "—" },
                { label: "Size", value: decision?.size ?? "—" },
                { label: "Spread Signal", value: decision?.spread_signal ?? "—" },
                formatOb("OB V1", obV1),
                formatOb("OB V2", obV2),
              ];
              const cfg = activity.bot_configs;
              const symbolLine =
                cfg?.SYM_VENUE1 && cfg?.SYM_VENUE2 ? `${cfg.SYM_VENUE1}/${cfg.SYM_VENUE2}` : null;
              const accountLine = [cfg?.ACC_V1, cfg?.ACC_V2].filter(Boolean).join(" / ");
              const configDetails = [
                { label: "Bot ID", value: cfg?.botId ?? activity.bot_id ?? "—" },
                { label: "Bot Name", value: cfg?.botName ?? "—" },
                { label: "Venue 1", value: cfg?.venue1 ?? cfg?.VENUE1 ?? "—" },
                { label: "Venue 2", value: cfg?.venue2 ?? cfg?.VENUE2 ?? "—" },
                { label: "Account V1", value: cfg?.account_v1 ?? cfg?.ACC_V1 ?? "—" },
                { label: "Account V2", value: cfg?.account_v2 ?? cfg?.ACC_V2 ?? "—" },
              ];
              return (
                <tr key={`${activity.bot_id ?? "bot"}-${activity.trace}`} className="border-t border-slate-800">
                  <td className="px-4 py-4 align-top w-[220px] max-w-[220px]">
                    <p className="font-mono text-sm text-white break-all">{activity.trace || "—"}</p>
                    {configDetails.map((item) => (
                      <p key={item.label} className="text-[11px] text-slate-400 mt-1">
                        {item.label}: {item.value}
                      </p>
                    ))}
                    {symbolLine && (
                      <p className="text-[11px] text-slate-400 mt-1">Symbol: {symbolLine}</p>
                    )}
                    {accountLine && (
                      <p className="text-[11px] text-slate-400 mt-1">Accounts: {accountLine}</p>
                    )}
                  </td>
                  <td className="px-4 py-4 align-top w-[260px]">
                    {decision ? renderKeyValues(decisionRows) : <p className="text-xs text-slate-500">—</p>}
                  </td>
                  <td className="px-4 py-4 align-top w-[200px]">
                    {decision ? (
                      <div className="rounded-lg bg-slate-900/50 border border-slate-800/60 p-3 space-y-3">
                        {renderInventorySection("Before", decision?.inv_before)}
                        {renderInventorySection("After", decision?.inv_after)}
                      </div>
                    ) : (
                      <p className="text-xs text-slate-500">—</p>
                    )}
                  </td>
                  <td className="px-4 py-4 align-top w-[260px] max-w-[320px]">
                    {renderTrades("Venue 1", tradesV1, `${activity.bot_id}-${activity.trace}-v1`) || (
                      <p className="text-xs text-slate-500">—</p>
                    )}
                  </td>
                  <td className="px-4 py-4 align-top w-[260px] max-w-[320px]">
                    {renderTrades("Venue 2", tradesV2, `${activity.bot_id}-${activity.trace}-v2`) || (
                      <p className="text-xs text-slate-500">—</p>
                    )}
                  </td>
                  <td className="px-4 py-4 align-top w-[220px]">
                    {renderFills("Venue 1", fillsV1, decision?.ts, obV1) || (
                      <p className="text-xs text-slate-500">—</p>
                    )}
                  </td>
                  <td className="px-4 py-4 align-top w-[220px]">
                    {renderFills("Venue 2", fillsV2, decision?.ts, obV2) || (
                      <p className="text-xs text-slate-500">—</p>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {isLoadingMore && (
          <div className="px-4 py-3 text-[11px] text-slate-400 text-center">Loading more activities…</div>
        )}
      </div>
    </div>
  );
}
