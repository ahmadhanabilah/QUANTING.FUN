import React, { useEffect, useMemo, useState } from "react";
import { Activity, ArrowLeft, ChevronDown, LogOut, Plus, Search, Settings as SettingsIcon, TrendingUp } from "lucide-react";
import "./index.css";
import { BotCard } from "./components/bot-card";
import { BotDetailView } from "./components/bot-detail-view";
import { SettingsPanel } from "./components/settings-panel";
import type { DetailTab } from "./components/bot-detail-view";
import { LoginPage } from "./components/login-page";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "./components/ui/dropdown-menu";
import { LatestTrades } from "./components/latest-trades";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
    DialogTrigger,
} from "./components/ui/dialog";

type SymbolCfg = Record<string, any> & {
    SYMBOL_LIGHTER: string;
    SYMBOL_EXTENDED: string;
};

type Tab = "dashboard" | "trades" | "settings";

type EnvLine =
    | { type: "pair"; key: string; value: string }
    | { type: "comment"; raw: string };

type BotCardModel = {
    id: string;
    name: string;
    pair: string;
    status: "running" | "stopped";
    profit24h: number;
    trades24h: number;
    cfg: SymbolCfg;
    L: string;
    E: string;
};

type ServerHealth = {
    cpu: { percent: number; per_core?: number[]; count: number };
    memory: { total: number; used: number; percent: number; available: number };
    swap: { total: number; used: number; percent: number };
    disk: { total: number; used: number; percent: number; path: string };
    load: number[];
    uptime: number;
    boot_time: number;
    process_count: number;
    net: { bytes_sent: number; bytes_recv: number };
    timestamp: number;
};

type CreateBotDraft = {
    SYMBOL_LIGHTER: string;
    SYMBOL_EXTENDED: string;
    MIN_SPREAD: number;
    SPREAD_TP: number;
    REPRICE_TICK: number;
    MAX_POSITION_VALUE: number | null;
    MAX_TRADE_VALUE: number;
    MAX_OF_OB: number;
    MAX_TRADES: number | null;
    MIN_HITS: number;
    TEST_MODE: boolean;
    DEDUP_OB: boolean;
    WARM_UP_ORDERS: boolean;
    ORDER_HEARTBEAT_ENABLED: boolean;
    ORDER_HEARTBEAT_INTERVAL: number;
    SLIPPAGE: number;
};

type BulkInputItem = {
    label: string;
    entry: Partial<CreateBotDraft>;
};

type BulkEntryPreview = {
    label: string;
    pairLabel: string;
    status: "ready" | "duplicate" | "existing" | "invalid";
    detail?: string;
};

type BulkPreviewResult = {
    readyEntries: SymbolCfg[];
    entryPreviews: BulkEntryPreview[];
    parseWarnings: string[];
    counts: {
        total: number;
        ready: number;
        duplicates: number;
        existing: number;
        invalid: number;
    };
};

const BULK_STATUS_STYLES: Record<BulkEntryPreview["status"], { label: string; className: string }> = {
    ready: {
        label: "Ready",
        className: "border-emerald-500/40 bg-emerald-500/15 text-emerald-300",
    },
    existing: {
        label: "Existing",
        className: "border-slate-500/40 bg-slate-500/15 text-slate-200",
    },
    duplicate: {
        label: "Duplicate",
        className: "border-amber-500/40 bg-amber-500/15 text-amber-300",
    },
    invalid: {
        label: "Invalid",
        className: "border-red-500/40 bg-red-500/15 text-red-300",
    },
};

const BASE_BOT_DRAFT: CreateBotDraft = {
    SYMBOL_LIGHTER: "NEW",
    SYMBOL_EXTENDED: "NEW-USD",
    MIN_SPREAD: 0.3,
    SPREAD_TP: 0.2,
    REPRICE_TICK: 0,
    MAX_POSITION_VALUE: 500,
    MAX_TRADE_VALUE: 25,
    MAX_OF_OB: 0.3,
    MAX_TRADES: null,
    MIN_HITS: 1,
    TEST_MODE: false,
    DEDUP_OB: true,
    WARM_UP_ORDERS: false,
    ORDER_HEARTBEAT_ENABLED: false,
    ORDER_HEARTBEAT_INTERVAL: 60,
    SLIPPAGE: 0.04,
};

const BULK_NUMERIC_COLS: Array<keyof CreateBotDraft> = [
    "MIN_SPREAD",
    "SPREAD_TP",
    "REPRICE_TICK",
    "MAX_POSITION_VALUE",
    "MAX_TRADE_VALUE",
    "MAX_OF_OB",
    "MAX_TRADES",
    "MIN_HITS",
    "SLIPPAGE",
    "ORDER_HEARTBEAT_INTERVAL",
];

const BULK_BOOL_COLS: Array<keyof CreateBotDraft> = [
    "TEST_MODE",
    "DEDUP_OB",
    "WARM_UP_ORDERS",
    "ORDER_HEARTBEAT_ENABLED",
];

const makeBotDraft = (overrides: Partial<CreateBotDraft> = {}): CreateBotDraft => ({
    ...BASE_BOT_DRAFT,
    ...overrides,
});

function normalizeBotEntry(entry: Partial<CreateBotDraft> | Record<string, any>): SymbolCfg | null {
    const merged: CreateBotDraft = {
        ...makeBotDraft(),
        ...(entry as Partial<CreateBotDraft>),
    };
    const clean = (val: any) => (typeof val === "string" ? val.trim() : val);
    const symL = clean(entry?.SYMBOL_LIGHTER ?? merged.SYMBOL_LIGHTER);
    const symE = clean(entry?.SYMBOL_EXTENDED ?? merged.SYMBOL_EXTENDED);
    if (!symL || !symE) {
        return null;
    }
    const toNumber = (raw: any, fallback: number) => {
        if (raw === "" || raw === undefined || raw === null) return fallback;
        const num = Number(raw);
        return Number.isFinite(num) ? num : fallback;
    };
    const toNullable = (raw: any, fallback: number | null) => {
        if (raw === "" || raw === undefined) return fallback;
        if (raw === null) return null;
        const num = Number(raw);
        if (!Number.isFinite(num)) {
            return fallback;
        }
        return num;
    };
    const toNullOrNumber = (raw: any, fallback: number | null) => {
        if (raw === "" || raw === undefined || raw === null) return null;
        const num = Number(raw);
        if (!Number.isFinite(num)) {
            return fallback;
        }
        return num;
    };
    return {
        SYMBOL_LIGHTER: String(symL).trim(),
        SYMBOL_EXTENDED: String(symE).trim(),
        MIN_SPREAD: toNumber(entry?.MIN_SPREAD ?? merged.MIN_SPREAD, merged.MIN_SPREAD),
        SPREAD_TP: toNumber(entry?.SPREAD_TP ?? merged.SPREAD_TP, merged.SPREAD_TP),
        REPRICE_TICK: toNumber(entry?.REPRICE_TICK ?? merged.REPRICE_TICK, merged.REPRICE_TICK),
        MAX_POSITION_VALUE: toNullOrNumber(entry?.MAX_POSITION_VALUE ?? merged.MAX_POSITION_VALUE, merged.MAX_POSITION_VALUE),
        MAX_TRADE_VALUE: toNumber(entry?.MAX_TRADE_VALUE ?? merged.MAX_TRADE_VALUE, merged.MAX_TRADE_VALUE),
        MAX_OF_OB: toNumber(entry?.MAX_OF_OB ?? merged.MAX_OF_OB, merged.MAX_OF_OB),
        MAX_TRADES: toNullOrNumber(entry?.MAX_TRADES ?? merged.MAX_TRADES, merged.MAX_TRADES),
        MIN_HITS: Math.max(1, toNumber(entry?.MIN_HITS ?? merged.MIN_HITS, merged.MIN_HITS)),
        TEST_MODE: Boolean(entry?.TEST_MODE ?? merged.TEST_MODE),
        DEDUP_OB: Boolean(entry?.DEDUP_OB ?? merged.DEDUP_OB),
        WARM_UP_ORDERS: Boolean(entry?.WARM_UP_ORDERS ?? merged.WARM_UP_ORDERS),
        ORDER_HEARTBEAT_ENABLED: Boolean(
            entry?.ORDER_HEARTBEAT_ENABLED ?? merged.ORDER_HEARTBEAT_ENABLED
        ),
        ORDER_HEARTBEAT_INTERVAL: toNumber(
            entry?.ORDER_HEARTBEAT_INTERVAL ?? merged.ORDER_HEARTBEAT_INTERVAL,
            merged.ORDER_HEARTBEAT_INTERVAL
        ),
        SLIPPAGE: toNumber(entry?.SLIPPAGE ?? merged.SLIPPAGE, merged.SLIPPAGE),
    };
}

const API_BASE =
    (import.meta.env.VITE_API_BASE as string | undefined)?.replace(/\/$/, "") ||
    `${window.location.protocol}//${window.location.hostname}:5001`;
function buildAuth(user: string, pass: string) {
    const token = btoa(unescape(encodeURIComponent(`${user}:${pass}`)));
    return { Authorization: `Basic ${token}` };
}

function parseEnv(text: string): EnvLine[] {
    return text.split(/\r?\n/).map((line) => {
        if (!line.trim() || line.trim().startsWith("#") || !line.includes("=")) {
            return { type: "comment", raw: line };
        }
        const [k, ...rest] = line.split("=");
        return { type: "pair", key: k.trim(), value: rest.join("=").trim() };
    });
}

function formatEnv(lines: EnvLine[]) {
    return lines
        .map((line) => {
            if (line.type === "comment") return line.raw;
            return `${line.key}=${line.value}`;
        })
        .join("\n");
}

function formatBytesShort(bytes: number) {
    if (!Number.isFinite(bytes)) return "—";
    const units = ["B", "KB", "MB", "GB", "TB"];
    let value = bytes;
    let idx = 0;
    while (value >= 1024 && idx < units.length - 1) {
        value /= 1024;
        idx += 1;
    }
    const precision = value >= 100 || idx === 0 ? 0 : value >= 10 ? 1 : 2;
    return `${value.toFixed(precision)} ${units[idx]}`;
}

function formatDurationShort(seconds: number) {
    if (!Number.isFinite(seconds) || seconds <= 0) return "0s";
    const days = Math.floor(seconds / 86400);
    const hours = Math.floor((seconds % 86400) / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    if (days > 0) {
        return `${days}d ${hours}h`;
    }
    if (hours > 0) {
        return `${hours}h ${minutes}m`;
    }
    if (minutes > 0) {
        return `${minutes}m`;
    }
    return `${Math.floor(seconds)}s`;
}

function parseBulkInput(raw: string): { items: BulkInputItem[]; errors: string[] } {
    const trimmed = raw.trim();
    if (!trimmed) {
        return { items: [], errors: [] };
    }
    try {
        const parsed = JSON.parse(trimmed);
        if (Array.isArray(parsed)) {
            return {
                items: parsed.map((entry, idx) => ({
                    entry: entry as Partial<CreateBotDraft>,
                    label: `Item ${idx + 1}`,
                })),
                errors: [],
            };
        }
        return { items: [], errors: ["JSON input must be an array of configs"] };
    } catch {
        const items: BulkInputItem[] = [];
        const errors: string[] = [];
        const lines = trimmed.split(/\r?\n/);
        lines.forEach((line, idx) => {
            const clean = line.trim();
            if (!clean) return;
            const parts = clean.split(/[,\s]+/).filter(Boolean);
            if (parts.length < 2) {
                errors.push(`Line ${idx + 1}: expected SYMBOL_LIGHTER and SYMBOL_EXTENDED`);
                return;
            }
            const entry: Partial<CreateBotDraft> = {
                SYMBOL_LIGHTER: parts[0],
                SYMBOL_EXTENDED: parts[1],
            };
            if (parts[2]) entry.MIN_SPREAD = Number(parts[2]);
            if (parts[3]) entry.SPREAD_TP = Number(parts[3]);
            if (parts[4]) entry.MIN_HITS = Number(parts[4]);
            items.push({ entry, label: `Line ${idx + 1}` });
        });
        return { items, errors };
    }
}

function buildBulkPreview(raw: string, existing: SymbolCfg[]): BulkPreviewResult {
    const { items, errors } = parseBulkInput(raw);
    const existingKeys = new Set(existing.map((s) => `${s.SYMBOL_LIGHTER}:${s.SYMBOL_EXTENDED}`));
    const seen = new Set<string>();
    const readyEntries: SymbolCfg[] = [];
    const entryPreviews: BulkEntryPreview[] = [];
    let ready = 0;
    let duplicates = 0;
    let existingCount = 0;
    let invalid = 0;
    items.forEach((item) => {
        const normalized = normalizeBotEntry(item.entry);
        if (!normalized) {
            invalid += 1;
            entryPreviews.push({
                label: item.label,
                pairLabel: "—",
                status: "invalid",
                detail: "Missing pair information",
            });
            return;
        }
        const key = `${normalized.SYMBOL_LIGHTER}:${normalized.SYMBOL_EXTENDED}`;
        const pairLabel = `${normalized.SYMBOL_LIGHTER}/${normalized.SYMBOL_EXTENDED}`;
        if (seen.has(key)) {
            duplicates += 1;
            entryPreviews.push({
                label: item.label,
                pairLabel,
                status: "duplicate",
                detail: "Duplicate within this input",
            });
            return;
        }
        seen.add(key);
        if (existingKeys.has(key)) {
            existingCount += 1;
            entryPreviews.push({
                label: item.label,
                pairLabel,
                status: "existing",
                detail: "Already present in dashboard",
            });
            return;
        }
        readyEntries.push(normalized);
        ready += 1;
        entryPreviews.push({
            label: item.label,
            pairLabel,
            status: "ready",
            detail: "Ready to create",
        });
    });
    return {
        readyEntries,
        entryPreviews,
        parseWarnings: errors,
        counts: {
            total: items.length,
            ready,
            duplicates,
            existing: existingCount,
            invalid,
        },
    };
}

export default function App() {
    const [user, setUser] = useState(localStorage.getItem("u") || "");
    const [pass, setPass] = useState(localStorage.getItem("p") || "");
    const [authed, setAuthed] = useState(false);
    const [loading, setLoading] = useState(false);
  const [symbols, setSymbols] = useState<SymbolCfg[]>([]);
  const [configData, setConfigData] = useState<Record<string, any> | null>(null);
    const [running, setRunning] = useState<string[]>([]);
    const [activeTab, setActiveTab] = useState<Tab>(() => {
        try {
            const stored = localStorage.getItem("activeTab") as Tab | null;
            if (stored === "dashboard" || stored === "settings" || stored === "trades") {
                return stored;
            }
        } catch {
            // ignore
        }
        return "dashboard";
    });
    const [selectedPair, setSelectedPair] = useState<{ L: string; E: string } | null>(null);
    const [envLines, setEnvLines] = useState<EnvLine[]>([]);
    const [msg, setMsg] = useState("");
    const [autoAuthAttempted, setAutoAuthAttempted] = useState(false);
    const [createDraft, setCreateDraft] = useState<CreateBotDraft>(() => makeBotDraft());
    const [createError, setCreateError] = useState("");
    const [createSaving, setCreateSaving] = useState(false);
    const [bulkOpen, setBulkOpen] = useState(false);
    const [bulkInput, setBulkInput] = useState("");
    const [bulkError, setBulkError] = useState("");
    const [bulkSummary, setBulkSummary] = useState("");
  const [bulkSaving, setBulkSaving] = useState(false);
  const [bulkEditOpen, setBulkEditOpen] = useState(false);
  const [bulkEditRows, setBulkEditRows] = useState<SymbolCfg[]>([]);
  const [bulkEditError, setBulkEditError] = useState("");
  const [bulkEditSaving, setBulkEditSaving] = useState(false);
    const [initialDetailTab, setInitialDetailTab] = useState<DetailTab | undefined>(undefined);
    const [selectionRestored, setSelectionRestored] = useState(false);
    const [dataMode, setDataMode] = useState<"live" | "test">(() =>
        localStorage.getItem("dataMode") === "test" ? "test" : "live"
    );
    const [pinnedBots, setPinnedBots] = useState<Record<string, number>>(() => {
        try {
            return JSON.parse(localStorage.getItem("pinnedBots") || "{}");
        } catch {
            return {};
        }
    });
    const [searchTerm, setSearchTerm] = useState("");
    const [serverStatus, setServerStatus] = useState<ServerHealth | null>(null);
    const [serverStatusError, setServerStatusError] = useState("");
    const [botFilter, setBotFilter] = useState<"all" | "pinned" | "other">("all");
    const [startAllLoading, setStartAllLoading] = useState(false);
    const [stopAllLoading, setStopAllLoading] = useState(false);

    const authHeaders = useMemo(() => buildAuth(user, pass), [user, pass]);
    const bulkPreview = useMemo(() => buildBulkPreview(bulkInput, symbols), [bulkInput, symbols]);
    const bulkStatChips = [
        { label: "Ready", value: bulkPreview.counts.ready, className: "border-emerald-500/40 bg-emerald-500/10 text-emerald-300" },
        { label: "Existing", value: bulkPreview.counts.existing, className: "border-slate-500/40 bg-slate-500/10 text-slate-200" },
        { label: "Duplicates", value: bulkPreview.counts.duplicates, className: "border-amber-500/40 bg-amber-500/10 text-amber-300" },
        { label: "Invalid", value: bulkPreview.counts.invalid, className: "border-red-500/40 bg-red-500/10 text-red-300" },
    ];
    const serverStatusSummary = useMemo(() => {
        if (!serverStatus) return null;
        const cpuCapacity = serverStatus.cpu?.count ? `${serverStatus.cpu.count} cores` : "—";
        return {
            cpu: { percent: Math.round(serverStatus.cpu.percent), capacity: cpuCapacity },
            memory: { percent: Math.round(serverStatus.memory.percent), capacity: formatBytesShort(serverStatus.memory.total) },
            disk: { percent: Math.round(serverStatus.disk.percent), capacity: formatBytesShort(serverStatus.disk.total) },
        };
    }, [serverStatus]);

    const bots: BotCardModel[] = symbols.map((s) => {
        const runKey = `${s.SYMBOL_LIGHTER}_${s.SYMBOL_EXTENDED}`;
        const runKeyAlt = `${s.SYMBOL_LIGHTER}:${s.SYMBOL_EXTENDED}`;
        const isRunning = running.includes(runKey) || running.includes(runKeyAlt);
        return {
            id: `${s.SYMBOL_LIGHTER}:${s.SYMBOL_EXTENDED}`,
            name: `${s.SYMBOL_LIGHTER}/${s.SYMBOL_EXTENDED}`,
            pair: `${s.SYMBOL_LIGHTER}/${s.SYMBOL_EXTENDED}`,
            status: isRunning ? "running" : "stopped",
            profit24h: 0,
            trades24h: 0,
            cfg: s,
            L: s.SYMBOL_LIGHTER,
            E: s.SYMBOL_EXTENDED,
        };
    });

    const activeBots = bots.filter((b) => b.status === "running").length;

    async function authCheck() {
        setLoading(true);
        try {
            const res = await fetch(`${API_BASE}/api/auth_check`, { headers: authHeaders });
            if (res.ok) {
                setAuthed(true);
                localStorage.setItem("u", user);
                localStorage.setItem("p", pass);
                await loadConfig();
                await fetchEnv();
            } else {
                setAuthed(false);
                setMsg("Auth failed");
            }
        } catch {
            setMsg("Auth error");
            setAuthed(false);
        } finally {
            setLoading(false);
        }
    }

    async function loadConfig() {
        try {
      const [cfgRes, runRes] = await Promise.all([
        fetch(`${API_BASE}/api/config`, { headers: authHeaders }),
        fetch(`${API_BASE}/api/symbols`, { headers: authHeaders }),
      ]);
      if (cfgRes.ok) {
        const cfg = await cfgRes.json();
        setSymbols(cfg.symbols || []);
        setConfigData(cfg);
      }
            if (runRes.ok) {
                const r = await runRes.json();
                setRunning(r.running || []);
            }
        } catch {
            setMsg("Failed to load config");
        }
    }

    async function startStop(
        pair: SymbolCfg,
        action: "start" | "stop",
        opts?: { skipReload?: boolean; silentMsg?: boolean }
    ) {
        const url = `${API_BASE}/api/${action}?symbolL=${pair.SYMBOL_LIGHTER}&symbolE=${pair.SYMBOL_EXTENDED}`;
        try {
            await fetch(url, { method: "POST", headers: authHeaders });
            if (!opts?.skipReload) {
                await loadConfig();
            }
            if (!opts?.silentMsg) {
                setMsg(action === "start" ? "Bot started" : "Bot stopped");
            }
        } catch {
            if (!opts?.silentMsg) {
                setMsg(`Failed to ${action}`);
            }
        }
    }

    async function removeBot(pair: SymbolCfg) {
        if (!window.confirm(`Delete ${pair.SYMBOL_LIGHTER}/${pair.SYMBOL_EXTENDED}?`)) {
            return;
        }
        try {
            const cfgRes = await fetch(`${API_BASE}/api/config`, { headers: authHeaders });
            if (!cfgRes.ok) {
                setMsg("Failed to load config");
                return;
            }
            const cfg = await cfgRes.json();
            const currentSymbols: SymbolCfg[] = Array.isArray(cfg?.symbols) ? cfg.symbols : [];
            const nextSymbols = currentSymbols.filter(
                (sym) =>
                    !(
                        sym.SYMBOL_LIGHTER === pair.SYMBOL_LIGHTER && sym.SYMBOL_EXTENDED === pair.SYMBOL_EXTENDED
                    )
            );
            if (nextSymbols.length === currentSymbols.length) {
                setMsg("Bot not found in config");
                return;
            }
            const updatedConfig = { ...cfg, symbols: nextSymbols };
            const res = await fetch(`${API_BASE}/api/config`, {
                method: "PUT",
                headers: { ...authHeaders, "Content-Type": "application/json" },
                body: JSON.stringify(updatedConfig),
            });
            if (!res.ok) {
                setMsg("Failed to delete bot");
                return;
            }
            setSymbols(nextSymbols);
            setPinnedBots((prev) => {
                const next = { ...prev };
                delete next[`${pair.SYMBOL_LIGHTER}:${pair.SYMBOL_EXTENDED}`];
                return next;
            });
            if (selectedPair && selectedPair.L === pair.SYMBOL_LIGHTER && selectedPair.E === pair.SYMBOL_EXTENDED) {
                setSelectedPair(null);
                localStorage.removeItem("selectedPair");
                localStorage.removeItem("selectedTab");
            }
            await loadConfig();
            setMsg("Bot deleted");
        } catch (err) {
            setMsg(err instanceof Error ? err.message : "Failed to delete bot");
        }
    }

    async function startVisibleBots() {
        const targets =
            botFilter === "pinned" ? pinnedList : botFilter === "other" ? unpinnedList : filteredBots;
        const toStart = targets.filter((b) => b.status !== "running");
        if (toStart.length === 0) {
            setMsg("No stopped bots in this view");
            return;
        }
        setStartAllLoading(true);
        setMsg(`Starting ${toStart.length} bot${toStart.length > 1 ? "s" : ""}...`);
        try {
            for (let idx = 0; idx < toStart.length; idx++) {
                const bot = toStart[idx];
                await startStop(bot.cfg, "start", { skipReload: true, silentMsg: true });
                if (idx < toStart.length - 1) {
                    await new Promise((resolve) => setTimeout(resolve, 1000));
                }
            }
            await loadConfig();
            setMsg(`Started ${toStart.length} bot${toStart.length > 1 ? "s" : ""}`);
        } finally {
            setStartAllLoading(false);
        }
    }

    async function stopVisibleBots() {
        const targets =
            botFilter === "pinned" ? pinnedList : botFilter === "other" ? unpinnedList : filteredBots;
        const toStop = targets.filter((b) => b.status === "running");
        if (toStop.length === 0) {
            setMsg("No running bots in this view");
            return;
        }
        setStopAllLoading(true);
        setMsg(`Stopping ${toStop.length} bot${toStop.length > 1 ? "s" : ""}...`);
        try {
            for (const bot of toStop) {
                await startStop(bot.cfg, "stop", { skipReload: true, silentMsg: true });
            }
            await loadConfig();
            setMsg(`Stopped ${toStop.length} bot${toStop.length > 1 ? "s" : ""}`);
        } finally {
            setStopAllLoading(false);
        }
    }

    async function fetchEnv() {
        try {
            const res = await fetch(`${API_BASE}/api/env`, { headers: authHeaders });
            if (!res.ok) {
                setMsg("Failed to load .env");
                return false;
            }
            setEnvLines(parseEnv(await res.text()));
            return true;
        } catch {
            setMsg("Failed to load .env");
            return false;
        }
    }

    async function saveEnv() {
        try {
            const res = await fetch(`${API_BASE}/api/env`, {
                method: "PUT",
                headers: { ...authHeaders, "Content-Type": "application/json" },
                body: JSON.stringify({ text: formatEnv(envLines) }),
            });
            if (!res.ok) {
                setMsg("Failed to save .env");
                return false;
            }
            setMsg(".env saved");
            return true;
        } catch {
            setMsg("Failed to save .env");
            return false;
        }
    }

    useEffect(() => {
        if (authed) {
            loadConfig();
            fetchEnv();
        }
    }, [authed]);

    useEffect(() => {
        if (autoAuthAttempted || authed) return;
        if (user && pass) {
            authCheck();
            setAutoAuthAttempted(true);
        } else {
            setAutoAuthAttempted(true);
        }
    }, [autoAuthAttempted, authed, user, pass]);

    useEffect(() => {
        localStorage.setItem("dataMode", dataMode);
    }, [dataMode]);

    useEffect(() => {
        localStorage.setItem("pinnedBots", JSON.stringify(pinnedBots));
    }, [pinnedBots]);

    useEffect(() => {
        try {
            localStorage.setItem("activeTab", activeTab);
        } catch {
            // ignore storage issues
        }
    }, [activeTab]);

    useEffect(() => {
        if (!authed) {
            setServerStatus(null);
            setServerStatusError("");
            return;
        }
        let cancelled = false;
        const fetchStatus = async () => {
            try {
                const res = await fetch(`${API_BASE}/api/server/health`, { headers: authHeaders });
                if (!res.ok) {
                    throw new Error(await res.text());
                }
                const data = await res.json();
                if (!cancelled) {
                    setServerStatus(data);
                    setServerStatusError("");
                }
            } catch (err: any) {
                if (!cancelled) {
                    setServerStatusError(err?.message || "Failed to load");
                }
            }
        };
        fetchStatus();
        const interval = setInterval(fetchStatus, 10000);
        return () => {
            cancelled = true;
            clearInterval(interval);
        };
    }, [authed, authHeaders]);

    useEffect(() => {
        if (!authed || selectionRestored) return;
        try {
            const storedPair = localStorage.getItem("selectedPair");
            const storedTab = localStorage.getItem("selectedTab") as DetailTab | null;
            if (storedPair) {
                const parsed = JSON.parse(storedPair);
                if (parsed?.L && parsed?.E) {
                    setSelectedPair({ L: parsed.L, E: parsed.E });
                    const allowedDetailTabs: DetailTab[] = ["dashboard", "decisions", "trades", "settings"];
                    if (storedTab && allowedDetailTabs.includes(storedTab)) {
                        setInitialDetailTab(storedTab);
                    }
                }
            }
        } catch {
            // ignore parse errors
        } finally {
            setSelectionRestored(true);
        }
    }, [authed, selectionRestored]);

    function resetCreateDraft() {
        setCreateDraft(makeBotDraft());
        setCreateError("");
    }

    async function handleCreateBot() {
        const newEntry = normalizeBotEntry(createDraft);
        if (!newEntry?.SYMBOL_LIGHTER || !newEntry?.SYMBOL_EXTENDED) {
            setCreateError("Symbol fields are required");
            return;
        }
        if (symbols.some((s) => s.SYMBOL_LIGHTER === newEntry.SYMBOL_LIGHTER && s.SYMBOL_EXTENDED === newEntry.SYMBOL_EXTENDED)) {
            setCreateError("Pair already exists");
            return;
        }
        setCreateSaving(true);
        setCreateError("");
        try {
            const postRes = await fetch(`${API_BASE}/api/symbols`, {
                method: "POST",
                headers: { ...authHeaders, "Content-Type": "application/json" },
                body: JSON.stringify(newEntry),
            });
            if (!postRes.ok) {
                const text = await postRes.text();
                throw new Error(text || "Failed to save new bot");
            }
            const data = await postRes.json();
            const updatedSymbols = Array.isArray(data?.symbols) ? data.symbols : [...symbols, newEntry];
            setSymbols(updatedSymbols);
            setMsg("Bot created");
            resetCreateDraft();
        } catch (err: any) {
            setCreateError(err?.message || "Failed to create bot");
        } finally {
            setCreateSaving(false);
        }
    }

    function parseBulkInput(raw: string) {
        const trimmed = raw.trim();
        if (!trimmed) {
            return { entries: [] as Partial<CreateBotDraft>[], errors: [] as string[] };
        }
        try {
            const parsed = JSON.parse(trimmed);
            if (Array.isArray(parsed)) {
                return { entries: parsed as Partial<CreateBotDraft>[], errors: [] as string[] };
            }
            return { entries: [] as Partial<CreateBotDraft>[], errors: ["JSON input must be an array of configs"] };
        } catch {
            const entries: Partial<CreateBotDraft>[] = [];
            const errors: string[] = [];
            const lines = trimmed.split(/\r?\n/);
            lines.forEach((line, idx) => {
                const clean = line.trim();
                if (!clean) return;
                const parts = clean.split(/[,\s]+/).filter(Boolean);
                if (parts.length < 2) {
                    errors.push(`Line ${idx + 1}: expected SYMBOL_LIGHTER and SYMBOL_EXTENDED`);
                    return;
                }
                const entry: Partial<CreateBotDraft> = {
                    SYMBOL_LIGHTER: parts[0],
                    SYMBOL_EXTENDED: parts[1],
                };
                if (parts[2]) entry.MIN_SPREAD = Number(parts[2]);
                if (parts[3]) entry.SPREAD_TP = Number(parts[3]);
                if (parts[4]) entry.MIN_HITS = Number(parts[4]);
                entries.push(entry);
            });
            return { entries, errors };
        }
    }

    async function handleBulkCreate() {
        setBulkError("");
        setBulkSummary("");
        if (!bulkPreview.readyEntries.length) {
            if (!bulkInput.trim()) {
                setBulkError("Provide at least one pair");
            } else if (!bulkPreview.counts.total && bulkPreview.parseWarnings.length) {
                setBulkError(bulkPreview.parseWarnings[0]);
            } else {
                setBulkError("No eligible entries to create");
            }
            return;
        }
        setBulkSaving(true);
        const currentKeys = new Set(symbols.map((s) => `${s.SYMBOL_LIGHTER}:${s.SYMBOL_EXTENDED}`));
        const created: SymbolCfg[] = [];
        const skippedExisting: string[] = [];
        const failed: string[] = [];
        for (const entry of bulkPreview.readyEntries) {
            const key = `${entry.SYMBOL_LIGHTER}:${entry.SYMBOL_EXTENDED}`;
            if (currentKeys.has(key)) {
                skippedExisting.push(key);
                continue;
            }
            try {
                const postRes = await fetch(`${API_BASE}/api/symbols`, {
                    method: "POST",
                    headers: { ...authHeaders, "Content-Type": "application/json" },
                    body: JSON.stringify(entry),
                });
                if (!postRes.ok) {
                    const text = await postRes.text();
                    throw new Error(text || "Failed to save bot");
                }
                created.push(entry);
                currentKeys.add(key);
            } catch (err: any) {
                failed.push(`${key}${err?.message ? ` (${err.message})` : ""}`);
            }
        }
        if (created.length) {
            setSymbols((prev) => [...prev, ...created]);
        }
        const summaryParts = [];
        if (created.length) summaryParts.push(`Created ${created.length}`);
        if (skippedExisting.length) summaryParts.push(`Skipped existing ${skippedExisting.length}`);
        if (bulkPreview.counts.existing) summaryParts.push(`Existing in input ${bulkPreview.counts.existing}`);
        if (bulkPreview.counts.duplicates) summaryParts.push(`Duplicates in input ${bulkPreview.counts.duplicates}`);
        if (bulkPreview.counts.invalid || bulkPreview.parseWarnings.length) summaryParts.push("Some inputs ignored");
        if (failed.length) summaryParts.push("Some entries failed");
        if (summaryParts.length) {
            setMsg(`Bulk add: ${summaryParts.join(" · ")}`);
        }
        const lines: string[] = [];
        if (created.length) {
            lines.push(`Created: ${created.map((c) => `${c.SYMBOL_LIGHTER}/${c.SYMBOL_EXTENDED}`).join(", ")}`);
        }
        const previewExisting = Array.from(
            new Set(
                bulkPreview.entryPreviews
                    .filter((entry) => entry.status === "existing" && entry.pairLabel !== "—")
                    .map((entry) => entry.pairLabel)
            )
        );
        if (skippedExisting.length) {
            lines.push(`Already present (during save): ${skippedExisting.join(", ")}`);
        } else if (previewExisting.length) {
            lines.push(`Already present (from input): ${previewExisting.join(", ")}`);
        }
        const previewDuplicates = Array.from(
            new Set(
                bulkPreview.entryPreviews
                    .filter((entry) => entry.status === "duplicate" && entry.pairLabel !== "—")
                    .map((entry) => entry.pairLabel)
            )
        );
        if (previewDuplicates.length) {
            lines.push(`Duplicates in input: ${previewDuplicates.join(", ")}`);
        }
        const invalidEntries = bulkPreview.entryPreviews.filter((entry) => entry.status === "invalid");
        if (invalidEntries.length) {
            lines.push(`Invalid entries: ${invalidEntries.map((entry) => entry.label).join(", ")}`);
        }
        if (bulkPreview.parseWarnings.length) {
            lines.push(`Parse warnings: ${bulkPreview.parseWarnings.join("; ")}`);
        }
        if (failed.length) {
            lines.push(`Failed: ${failed.join("; ")}`);
        }
        setBulkSummary(lines.join("\n"));
        if (
            !failed.length &&
            !skippedExisting.length &&
            !bulkPreview.parseWarnings.length &&
            bulkPreview.counts.duplicates === 0 &&
            bulkPreview.counts.existing === 0 &&
            bulkPreview.counts.invalid === 0
        ) {
            setBulkInput("");
        }
        setBulkSaving(false);
    }

  function handleBulkDialogChange(open: boolean) {
    setBulkOpen(open);
    if (!open) {
      setBulkError("");
      setBulkSummary("");
    }
  }

  function handleBulkEditDialogChange(open: boolean) {
    setBulkEditOpen(open);
    if (open) {
      setBulkEditRows(symbols.map((s) => ({ ...s })));
      setBulkEditError("");
    } else {
      setBulkEditError("");
      setBulkEditSaving(false);
    }
  }

  function updateBulkEditRow(idx: number, key: keyof SymbolCfg, value: any) {
    setBulkEditRows((rows) =>
      rows.map((row, i) => {
        if (i !== idx) return row;
        return { ...row, [key]: value };
      })
    );
  }

  async function saveBulkEdit() {
    setBulkEditError("");
    setBulkEditSaving(true);
    try {
      const numericKeys = BULK_NUMERIC_COLS;
      const boolKeys = BULK_BOOL_COLS;
      const cleaned = bulkEditRows.map((row) => {
        const next: any = { ...row };
        numericKeys.forEach((k) => {
          const raw: any = (row as any)[k];
          if (raw === "" || raw === null || raw === undefined) {
            next[k] = k === "MAX_TRADES" || k === "MAX_POSITION_VALUE" ? null : raw;
          } else {
            const num = Number(raw);
            next[k] = Number.isFinite(num) ? num : (row as any)[k];
          }
        });
        boolKeys.forEach((k) => {
          next[k] = Boolean((row as any)[k]);
        });
        return next as SymbolCfg;
      });
      const cfgPayload = { ...(configData || {}), symbols: cleaned };
      const res = await fetch(`${API_BASE}/api/config`, {
        method: "PUT",
        headers: { ...authHeaders, "Content-Type": "application/json" },
        body: JSON.stringify(cfgPayload),
      });
      if (!res.ok) {
        throw new Error("Failed to save config");
      }
      setSymbols(cleaned);
      setConfigData(cfgPayload);
      setMsg("Config updated");
      setBulkEditOpen(false);
    } catch (err: any) {
      setBulkEditError(err?.message || "Failed to save");
    } finally {
      setBulkEditSaving(false);
    }
  }

    function updateEnvValue(key: string, value: string) {
        setEnvLines((lines) =>
            lines.map((line) => {
                if (line.type === "pair" && line.key === key) {
                    return { ...line, value };
                }
                return line;
            })
        );
    }

    function logout() {
        setAuthed(false);
        setUser("");
        setPass("");
        setSelectedPair(null);
        setSymbols([]);
        setRunning([]);
        setEnvLines([]);
        localStorage.removeItem("u");
        localStorage.removeItem("p");
        localStorage.removeItem("selectedPair");
        localStorage.removeItem("selectedTab");
        localStorage.removeItem("dataMode");
    }

    if (!authed) {
        return (
            <LoginPage
                user={user}
                pass={pass}
                loading={loading}
                msg={msg}
                onUserChange={setUser}
                onPassChange={setPass}
                onSubmit={authCheck}
                onClearMsg={() => setMsg("")}
            />
        );
    }

    function togglePin(botId: string) {
        setPinnedBots((prev) => {
            const next = { ...prev };
            if (next[botId]) {
                delete next[botId];
            } else {
                next[botId] = Date.now();
            }
            return next;
        });
    }

    const filteredBots = bots.filter((b) => {
        if (!searchTerm.trim()) return true;
        const haystack = `${b.name} ${b.pair} ${b.L} ${b.E}`.toLowerCase();
        return haystack.includes(searchTerm.toLowerCase());
    });
    const pinnedList = filteredBots
        .filter((b) => pinnedBots[b.id])
        .sort((a, b) => (pinnedBots[a.id] || 0) - (pinnedBots[b.id] || 0));
    const unpinnedList = filteredBots
        .filter((b) => !pinnedBots[b.id])
        .sort((a, b) => {
            if (a.status !== b.status) {
                return a.status === "running" ? -1 : 1;
            }
            return a.name.localeCompare(b.name);
        });
    const showPinned = botFilter !== "other";
    const showOther = botFilter !== "pinned";
    const visibleCount = (showPinned ? pinnedList.length : 0) + (showOther ? unpinnedList.length : 0);

    const selected = selectedPair ? bots.find((p) => p.L === selectedPair.L && p.E === selectedPair.E) || null : null;
    const headerTitle = selected ? selected.L || selected.name : "QUANTING.FUN";
    const headerSubtitle = selected ? selected.E || selected.pair : "";

    return (
        <>
            <div className="min-h-screen bg-gradient-to-br from-slate-950 via-slate-900 to-slate-950 text-white">
                <header className="bg-slate-900/80 backdrop-blur-xl border-b border-slate-800/50 sticky top-0 z-10">
                    <div className="px-4 sm:px-6 py-4 flex flex-col md:flex-row md:items-center md:justify-between gap-3 relative">
                        <div className="flex items-center gap-3">
                            {selected && (
                                <button
                                    onClick={() => {
                                        setSelectedPair(null);
                                        localStorage.removeItem("selectedPair");
                                        localStorage.removeItem("selectedTab");
                                    }}
                                    className="p-2 hover:bg-slate-800/50 rounded-lg transition-all text-slate-400 hover:text-white"
                                >
                                    <ArrowLeft className="w-5 h-5" />
                                </button>
                            )}
                            <div>
                                <h1 className="text-white text-2xl font-semibold">{headerTitle}</h1>
                                {headerSubtitle && <p className="text-slate-400 text-sm mt-1">{headerSubtitle}</p>}
                            </div>
                        </div>
                        {!selected && (
                            <div className="flex flex-col gap-3 w-full">
                                <div className="md:flex md:items-center md:gap-6">
                                    <div className="w-full md:flex-none md:order-2 md:ml-auto md:w-auto">
                                        <div className="w-full md:w-auto md:min-w-[360px]">
                                            <div className="grid grid-cols-4 gap-1.5 w-full">
                                                <div className="bg-slate-800/50 backdrop-blur-sm border border-slate-700/50 rounded-xl px-2 py-1 text-center sm:text-left h-[70px] min-h-[70px] md:h-[90px] md:min-h-[90px] flex flex-col justify-center min-w-0">
                                                    <p className="text-slate-400 text-[9px] md:text-xs mb-0.5">Active Bots</p>
                                                    <p className="text-white text-sm md:text-base">
                                                        {activeBots}
                                                        <span className="text-slate-500">/{bots.length}</span>
                                                    </p>
                                                </div>
                                                {(["cpu", "memory", "disk"] as Array<keyof typeof serverMetricLabels>).map((metric) => {
                                                    const summary = serverStatusSummary?.[metric];
                                                    return (
                                                        <div
                                                            key={metric}
                                                            className="bg-slate-800/50 backdrop-blur-sm border border-slate-700/50 rounded-xl px-2 py-1 text-center sm:text-left h-[70px] min-h-[70px] md:h-[90px] md:min-h-[90px] flex flex-col justify-center min-w-0"
                                                        >
                                                            <p className="text-slate-400 text-[9px] md:text-xs mb-0.5 text-center sm:text-left">{serverMetricLabels[metric]}</p>
                                                            {summary ? (
                                                                <>
                                                                    <p className="text-white text-sm md:text-base text-center sm:text-left">{summary.percent}%</p>
                                                                    <p className="text-slate-500 text-[9px] md:text-[11px] text-center sm:text-left">of {summary.capacity}</p>
                                                                </>
                                                            ) : (
                                                                <p className="text-slate-500 text-[11px] text-center sm:text-left">{serverStatusError || "Loading..."}</p>
                                                            )}
                                                        </div>
                                                    );
                                                })}
                                            </div>
                                        </div>
                                    </div>
                                    <div className="flex flex-wrap gap-2 w-full md:w-auto md:flex-1 md:order-1 md:justify-center md:ml-0 mt-4 md:mt-0 md:absolute md:left-1/2 md:-translate-x-1/2 md:top-1/2 md:-translate-y-1/2 md:w-auto">
                                        <button
                                            onClick={() => setActiveTab("dashboard")}
                                            className={`flex-1 sm:flex-none flex items-center justify-center gap-2 px-4 py-3 text-sm uppercase tracking-wide rounded-lg border transition-all ${activeTab === "dashboard"
                                                    ? "border-blue-500 text-white bg-blue-600/40"
                                                    : "border-slate-700 text-slate-300 bg-slate-900/40 hover:text-white hover:bg-slate-800/50"
                                                }`}
                                        >
                                            <Activity className="w-4 h-4" />
                                            <span className="hidden xl:inline">Dashboard</span>
                                        </button>
                                        <button
                                            onClick={() => setActiveTab("trades")}
                                            className={`flex-1 sm:flex-none flex items-center justify-center gap-2 px-4 py-3 text-sm uppercase tracking-wide rounded-lg border transition-all ${activeTab === "trades"
                                                    ? "border-blue-500 text-white bg-blue-600/40"
                                                    : "border-slate-700 text-slate-300 bg-slate-900/40 hover:text-white hover:bg-slate-800/50"
                                                }`}
                                        >
                                            <TrendingUp className="w-4 h-4" />
                                            <span className="hidden xl:inline">Trades</span>
                                        </button>
                                        <button
                                            onClick={() => setActiveTab("settings")}
                                            className={`flex-1 sm:flex-none flex items-center justify-center gap-2 px-4 py-3 text-sm uppercase tracking-wide rounded-lg border transition-all ${activeTab === "settings"
                                                    ? "border-blue-500 text-white bg-blue-600/40"
                                                    : "border-slate-700 text-slate-300 bg-slate-900/40 hover:text-white hover:bg-slate-800/50"
                                                }`}
                                        >
                                            <SettingsIcon className="w-4 h-4" />
                                            <span className="hidden xl:inline">Settings</span>
                                        </button>
                                        <button
                                            onClick={logout}
                                            className="flex-1 sm:flex-none flex items-center justify-center gap-2 px-4 py-3 text-sm uppercase tracking-wide rounded-lg border border-slate-700 text-slate-200 bg-slate-900/50 hover:bg-slate-800/50 transition-all"
                                        >
                                            <LogOut className="w-4 h-4" />
                                            <span className="hidden xl:inline">Logout</span>
                                        </button>
                                    </div>
                                </div>
                            </div>
                        )}
                    </div>
                </header>

                <main className="p-6">
                    <div className="max-w-7xl mx-auto space-y-6">
                        {!selected && activeTab === "dashboard" && (
                            <div>
                                <div className="mb-6 space-y-3">
                                    <div className="flex flex-wrap items-center justify-between gap-3">
                                        <div className="flex-none">
                                            <h2 className="text-white mb-1 text-lg">Active Bots</h2>
                                        </div>
                                        <div className="flex items-center gap-2">
                                            <div className="inline-flex rounded-lg border border-slate-700 bg-slate-900/60 p-1">
                                                {([
                                                    { key: "all", label: "All" },
                                                    { key: "pinned", label: "Pinned" },
                                                    { key: "other", label: "Other" },
                                                ] as const).map((opt) => (
                                                    <button
                                                        key={opt.key}
                                                        type="button"
                                                        onClick={() => setBotFilter(opt.key)}
                                                        className={`px-3 py-1.5 text-xs rounded-md transition-colors ${botFilter === opt.key
                                                                ? "bg-blue-600 text-white shadow-sm"
                                                                : "text-slate-300 hover:text-white hover:bg-slate-800/70"
                                                            }`}
                                                    >
                                                        {opt.label}
                                                    </button>
                                                ))}
                                            </div>
                                            <DropdownMenu>
                                                <DropdownMenuTrigger asChild>
                                                    <button
                                                        type="button"
                                                        disabled={startAllLoading || stopAllLoading}
                                                        className="flex items-center justify-center px-2 py-2 rounded-lg border border-slate-700 text-slate-200 hover:text-white hover:bg-slate-800/70 transition disabled:opacity-60"
                                                        aria-label="Bulk actions"
                                                    >
                                                        <ChevronDown className="w-4 h-4" />
                                                    </button>
                                                </DropdownMenuTrigger>
                                                <DropdownMenuContent
                                                    align="end"
                                                    className="bg-slate-900 border border-slate-700 text-slate-100 min-w-0 w-auto"
                                                >
                                                    <DropdownMenuItem
                                                        className="text-sm cursor-pointer hover:bg-slate-800/80 hover:text-white transition-colors"
                                                        onSelect={() => startVisibleBots()}
                                                        disabled={startAllLoading || stopAllLoading}
                                                    >
                                                        Start all
                                                    </DropdownMenuItem>
                                                    <DropdownMenuItem
                                                        className="text-sm cursor-pointer hover:bg-slate-800/80 hover:text-white transition-colors"
                                                        onSelect={() => stopVisibleBots()}
                                                        disabled={stopAllLoading || startAllLoading}
                                                    >
                                                        Stop all
                                                    </DropdownMenuItem>
                                                    <DropdownMenuItem
                                                        className="text-sm cursor-pointer hover:bg-slate-800/80 hover:text-white transition-colors"
                                                        onSelect={() => {
                                                            handleCreateBot();
                                                        }}
                                                    >
                                                        Single add
                                                    </DropdownMenuItem>
                                                    <DropdownMenuItem
                                                        className="text-sm cursor-pointer hover:bg-slate-800/80 hover:text-white transition-colors"
                                                        onSelect={() => {
                                                            setBulkOpen(true);
                                                        }}
                                                    >
                                                        Bulk add
                                                    </DropdownMenuItem>
                                                    <DropdownMenuItem
                                                        className="text-sm cursor-pointer hover:bg-slate-800/80 hover:text-white transition-colors"
                                                        onSelect={() => {
                                                            handleBulkEditDialogChange(true);
                                                        }}
                                                    >
                                                        Bulk edit
                                                    </DropdownMenuItem>
                                                </DropdownMenuContent>
                                            </DropdownMenu>
                                            {startAllLoading && (
                                                <div className="flex items-center gap-2 text-xs text-slate-200 ml-2">
                                                    <span className="h-2.5 w-2.5 rounded-full border border-white/60 border-r-transparent animate-spin" />
                                                    Starting all bots…
                                                </div>
                                            )}
                                        </div>
                                    </div>
                                    <div className="relative w-full">
                                        <Search className="w-4 h-4 text-slate-500 absolute left-3 top-1/2 -translate-y-1/2" />
                                        <input
                                            value={searchTerm}
                                            onChange={(e) => setSearchTerm(e.target.value)}
                                            placeholder="Search bots..."
                                            className="w-full pl-9 pr-3 py-2 rounded-lg bg-slate-900/70 border border-slate-700 text-sm text-white focus:outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-400/40 transition"
                                        />
                                    </div>
                                    <Dialog open={bulkOpen} onOpenChange={handleBulkDialogChange}>
                                        <DialogContent className="max-w-3xl">
                                            <DialogHeader>
                                                <DialogTitle>Bulk add bots</DialogTitle>
                                                <DialogDescription>
                                                    Paste JSON (array of configs) or write one pair per line as
                                                    <code className="px-1">SYMBOL_LIGHTER SYMBOL_EXTENDED [MIN_SPREAD] [SPREAD_TP] [MIN_HITS]</code>. The preview
                                                    highlights duplicates, conflicts, and invalid rows before you submit.
                                                </DialogDescription>
                                            </DialogHeader>
                                            <div className="space-y-4">
                                                <div className="space-y-3">
                                                    <textarea
                                                        value={bulkInput}
                                                        onChange={(e) => setBulkInput(e.target.value)}
                                                        rows={10}
                                                        className="w-full rounded-lg border border-slate-800 bg-slate-900 p-3 text-sm text-white focus:outline-none focus:ring-2 focus:ring-blue-500 min-h-[240px]"
                                                        placeholder={`Paste JSON array or one pair per line:\nBTC BTC-USD 0.3 0.2 1\nETH ETH-USD`}
                                                    />
                                                    {bulkError && <p className="text-sm text-red-400">{bulkError}</p>}
                                                    {bulkSummary && (
                                                        <pre className="whitespace-pre-wrap rounded-md bg-slate-800/60 p-3 text-xs text-slate-200 border border-slate-700">
                                                            {bulkSummary}
                                                        </pre>
                                                    )}
                                                </div>
                                                <div className="space-y-3">
                                                    <div className="rounded-lg border border-slate-800 bg-slate-900 p-3">
                                                        <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                                                            <div>
                                                                <p className="text-sm text-white">Live preview</p>
                                                                <p className="text-xs text-slate-400">Entries ready to import or needing attention.</p>
                                                            </div>
                                                            <div className="flex flex-wrap gap-2">
                                                                {bulkStatChips.map((chip) => (
                                                                    <span
                                                                        key={chip.label}
                                                                        className={`text-xs px-2 py-1 rounded-full border ${chip.className} ${chip.value ? "opacity-100" : "opacity-50"
                                                                            }`}
                                                                    >
                                                                        {chip.label}: {chip.value}
                                                                    </span>
                                                                ))}
                                                            </div>
                                                        </div>
                                                        <div className="mt-3 max-h-64 overflow-y-auto rounded-lg border border-slate-800 bg-slate-950 divide-y divide-slate-800/70">
                                                            {bulkPreview.entryPreviews.length ? (
                                                                bulkPreview.entryPreviews.map((entry) => (
                                                                    <div key={`${entry.label}-${entry.pairLabel}`} className="flex flex-col gap-1 p-3 sm:flex-row sm:items-center sm:justify-between">
                                                                        <div>
                                                                            <p className="text-sm text-white">
                                                                                <span className="font-mono text-xs text-slate-400 mr-2">{entry.label}</span>
                                                                                {entry.pairLabel}
                                                                            </p>
                                                                            {entry.detail && <p className="text-xs text-slate-400">{entry.detail}</p>}
                                                                        </div>
                                                                        <span
                                                                            className={`text-xs px-2 py-1 rounded-full border ${BULK_STATUS_STYLES[entry.status].className}`}
                                                                        >
                                                                            {BULK_STATUS_STYLES[entry.status].label}
                                                                        </span>
                                                                    </div>
                                                                ))
                                                            ) : (
                                                                <div className="p-4 text-sm text-slate-400">Paste entries to see them analyzed here.</div>
                                                            )}
                                                        </div>
                                                    </div>
                                                    {bulkPreview.parseWarnings.length > 0 && (
                                                        <div className="rounded-lg border border-amber-500/40 bg-slate-900 p-3">
                                                            <p className="text-sm font-semibold text-amber-100">Parse warnings</p>
                                                            <ul className="mt-2 list-disc space-y-1 pl-4 text-xs text-amber-100">
                                                                {bulkPreview.parseWarnings.map((warning, idx) => (
                                                                    <li key={`${warning}-${idx}`}>{warning}</li>
                                                                ))}
                                                            </ul>
                                                        </div>
                                                    )}
                                                </div>
                                            </div>
                                            <DialogFooter>
                                                <button
                                                    type="button"
                                                    onClick={() => handleBulkDialogChange(false)}
                                                    className="px-4 py-2 rounded-lg border border-slate-700 text-slate-200 hover:text-white hover:bg-slate-800/60 transition text-sm"
                                                >
                                                    Cancel
                                                </button>
                                                <button
                                                    type="button"
                                                    onClick={handleBulkCreate}
                                                    disabled={bulkSaving}
                                                    className="flex items-center gap-2 px-4 py-2 rounded-lg bg-blue-600 text-white text-sm hover:bg-blue-500 transition disabled:opacity-60"
                                                >
                                                    {bulkSaving ? "Adding..." : "Add bots"}
                                                </button>
                                            </DialogFooter>
                                        </DialogContent>
                                    </Dialog>
                                    <Dialog open={bulkEditOpen} onOpenChange={handleBulkEditDialogChange}>
                                        <DialogContent className="max-w-5xl">
                                            <DialogHeader>
                                                <DialogTitle>Bulk edit bots</DialogTitle>
                                                <DialogDescription>Edit existing bot settings in a table, then save to update config.json.</DialogDescription>
                                            </DialogHeader>
                                            <div className="overflow-x-auto rounded-lg border border-slate-800 bg-slate-950 relative">
                                                <table className="w-full text-xs table-mono">
                                            <thead className="bg-slate-900">
                                                <tr>
                                                    {["L", "E", ...BULK_NUMERIC_COLS, ...BULK_BOOL_COLS].map((col) => {
                                                        const sticky = col === "L" ? "sticky left-0 bg-slate-900/95 z-10 min-w-[120px]" : "";
                                                        const extraClass = col === "E" ? "min-w-[150px]" : "";
                                                        return (
                                                            <th
                                                                key={col}
                                                                className={`px-2 py-2 text-left text-slate-300 whitespace-nowrap ${sticky} ${extraClass}`}
                                                            >
                                                                {col.replace("_", " ")}
                                                            </th>
                                                        );
                                                    })}
                                                </tr>
                                            </thead>
                                            <tbody>
                                                {bulkEditRows.map((row, idx) => (
                                                    <tr key={`${row.SYMBOL_LIGHTER}-${row.SYMBOL_EXTENDED}-${idx}`} className="border-t border-slate-800">
                                                        <td className="px-2 py-1 sticky left-0 bg-slate-950 border-r border-slate-800 z-20">
                                                                    <input
                                                                value={row.SYMBOL_LIGHTER}
                                                                onChange={(e) => updateBulkEditRow(idx, "SYMBOL_LIGHTER", e.target.value)}
                                                                className="w-full px-2 py-1 rounded bg-slate-900 border border-slate-700 text-white text-xs"
                                                            />
                                                        </td>
                                                        <td className="px-2 py-1">
                                                            <input
                                                                value={row.SYMBOL_EXTENDED}
                                                                onChange={(e) => updateBulkEditRow(idx, "SYMBOL_EXTENDED", e.target.value)}
                                                                className="w-full px-2 py-1 rounded bg-slate-900 border border-slate-700 text-white text-xs"
                                                            />
                                                        </td>
                                                        {BULK_NUMERIC_COLS.map((key) => (
                                                            <td key={String(key)} className="px-2 py-1">
                                                                <input
                                                                    value={(row as any)[key] ?? ""}
                                                                    onChange={(e) => updateBulkEditRow(idx, key as keyof SymbolCfg, e.target.value)}
                                                                    className="w-full px-2 py-1 rounded bg-slate-900 border border-slate-700 text-white text-xs"
                                                                />
                                                            </td>
                                                        ))}
                                                        {BULK_BOOL_COLS.map((key) => (
                                                            <td key={key} className="px-2 py-1 text-center">
                                                                <input
                                                                    type="checkbox"
                                                                    checked={Boolean((row as any)[key])}
                                                                    onChange={(e) => updateBulkEditRow(idx, key as keyof SymbolCfg, e.target.checked)}
                                                                        />
                                                                    </td>
                                                                ))}
                                                            </tr>
                                                        ))}
                                                    </tbody>
                                                </table>
                                            </div>
                                            {bulkEditError && <p className="text-sm text-red-400">{bulkEditError}</p>}
                                            <DialogFooter>
                                                <button
                                                    type="button"
                                                    onClick={() => handleBulkEditDialogChange(false)}
                                                    className="px-4 py-2 rounded-lg border border-slate-700 text-slate-200 hover:text-white hover:bg-slate-800/60 transition text-sm"
                                                >
                                                    Cancel
                                                </button>
                                                <button
                                                    type="button"
                                                    onClick={saveBulkEdit}
                                                    disabled={bulkEditSaving}
                                                    className="flex items-center gap-2 px-4 py-2 rounded-lg bg-blue-600 text-white text-sm hover:bg-blue-500 transition disabled:opacity-60"
                                                >
                                                    {bulkEditSaving ? "Saving..." : "Save changes"}
                                                </button>
                                            </DialogFooter>
                                        </DialogContent>
                                    </Dialog>
                                </div>
                                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
                                    {showPinned && pinnedList.length > 0 && (
                                        <div className="col-span-full text-xs uppercase tracking-wide text-slate-400 flex items-center gap-2">
                                            <span className="h-px w-6 bg-slate-700" />
                                            Pinned
                                            <span className="h-px flex-1 bg-slate-700" />
                                        </div>
                                    )}
                                    {showPinned &&
                                        pinnedList.map((bot) => (
                                            <BotCard
                                                key={bot.id}
                                                bot={{
                                                    id: bot.id,
                                                    name: bot.name,
                                                    status: bot.status,
                                                    pair: bot.pair,
                                                    L: bot.L,
                                                    E: bot.E,
                                                    profit24h: bot.profit24h,
                                                    trades24h: bot.trades24h,
                                                }}
                                                pinned
                                                onPin={() => togglePin(bot.id)}
                                                onToggle={() => startStop(bot.cfg, bot.status === "running" ? "stop" : "start")}
                                                onView={() => {
                                                    setSelectedPair({ L: bot.L, E: bot.E });
                                                    localStorage.setItem("selectedPair", JSON.stringify({ L: bot.L, E: bot.E }));
                                                    localStorage.setItem("selectedTab", "dashboard");
                                                    setInitialDetailTab("dashboard");
                                                }}
                                            />
                                        ))}
                                    {showPinned && showOther && pinnedList.length > 0 && <div className="col-span-full h-px bg-slate-800/60" />}
                                    {showOther && (showPinned ? pinnedList.length > 0 && unpinnedList.length > 0 : unpinnedList.length > 0) && (
                                        <div className="col-span-full text-xs uppercase tracking-wide text-slate-400 flex items-center gap-2">
                                            <span className="h-px w-6 bg-slate-700" />
                                            Other Bots
                                            <span className="h-px flex-1 bg-slate-700" />
                                        </div>
                                    )}
                                    {showOther &&
                                        unpinnedList.map((bot) => (
                                            <BotCard
                                                key={bot.id}
                                                bot={{
                                                    id: bot.id,
                                                    name: bot.name,
                                                    status: bot.status,
                                                    pair: bot.pair,
                                                    L: bot.L,
                                                    E: bot.E,
                                                    profit24h: bot.profit24h,
                                                    trades24h: bot.trades24h,
                                                }}
                                                pinned={false}
                                                onPin={() => togglePin(bot.id)}
                                                onToggle={() => startStop(bot.cfg, bot.status === "running" ? "stop" : "start")}
                                                onView={() => {
                                                    setSelectedPair({ L: bot.L, E: bot.E });
                                                    localStorage.setItem("selectedPair", JSON.stringify({ L: bot.L, E: bot.E }));
                                                    localStorage.setItem("selectedTab", "dashboard");
                                                    setInitialDetailTab("dashboard");
                                                }}
                                            />
                                        ))}
                                </div>
                                {visibleCount === 0 && (
                                    <div className="text-center text-slate-400 py-10 border border-dashed border-slate-800 rounded-xl bg-slate-900/40">
                                        No bots found. Adjust your search.
                                    </div>
                                )}
                            </div>
                        )}

                        {!selected && activeTab === "trades" && (
                            <div className="bg-gradient-to-br from-slate-900 to-slate-900/50 border border-slate-800/50 rounded-xl p-6 shadow-xl">
                                <div className="flex items-center justify-between mb-4">
                                    <div>
                                        <h2 className="text-white text-lg">Latest Trades (all bots)</h2>
                                        <p className="text-slate-400 text-sm">Aggregated recent trades across every configured pair.</p>
                                    </div>
                                </div>
              <LatestTrades apiBase={API_BASE} authHeaders={authHeaders} mode={dataMode} onModeChange={setDataMode} />
            </div>
          )}

                        {!selected && activeTab === "settings" && (
                            <div className="bg-gradient-to-br from-slate-900 to-slate-900/50 border border-slate-800/50 rounded-xl p-6 shadow-xl">
                                <SettingsPanel envLines={envLines} onChange={updateEnvValue} onSave={saveEnv} onReset={fetchEnv} />
                            </div>
                        )}

                        {selected && (
                            <BotDetailView
                                bot={selected}
                                apiBase={API_BASE}
                                authHeaders={authHeaders}
                                mode={dataMode}
                                onModeChange={setDataMode}
                                onToggle={() => startStop(selected.cfg, selected.status === "running" ? "stop" : "start")}
                                onDelete={() => removeBot(selected.cfg)}
                                initialTab={initialDetailTab || "dashboard"}
                                onTabChange={(tab) => {
                                    localStorage.setItem("selectedTab", tab);
                                }}
                                onSettingsSaved={(draft) => {
                                    // refresh config so header/cards reflect the new symbols
                                    setSymbols((prev) =>
                                        prev.map((s) =>
                                            s.SYMBOL_LIGHTER === selected?.L && s.SYMBOL_EXTENDED === selected?.E
                                                ? { ...s, ...draft }
                                                : s
                                        )
                                    );
                                    loadConfig();
                                    if (draft?.SYMBOL_LIGHTER && draft?.SYMBOL_EXTENDED) {
                                        setSelectedPair({ L: draft.SYMBOL_LIGHTER, E: draft.SYMBOL_EXTENDED });
                                        localStorage.setItem("selectedPair", JSON.stringify({ L: draft.SYMBOL_LIGHTER, E: draft.SYMBOL_EXTENDED }));
                                        // immediately update header title/subtitle without waiting for reload
                                        setSelectionRestored(false);
                                    }
                                }}
                                onClose={() => {
                                    localStorage.removeItem("selectedPair");
                                    localStorage.removeItem("selectedTab");
                                }}
                                onBack={() => {
                                    setSelectedPair(null);
                                    localStorage.removeItem("selectedPair");
                                    localStorage.removeItem("selectedTab");
                                }}
                            />
                        )}

                    </div>
                </main>

            </div>

        </>
    );
}
const serverMetricLabels = { cpu: "CPU", memory: "Memory", disk: "Disk" } as const;
