import React, { useEffect, useMemo, useState } from "react";
import { Activity, ArrowLeft, LogOut, Plus, Search, Settings as SettingsIcon, TrendingUp } from "lucide-react";
import "./index.css";
import { BotCard } from "./components/bot-card";
import { BotDetailView } from "./components/bot-detail-view";
import { SettingsPanel } from "./components/settings-panel";
import type { DetailTab } from "./components/bot-detail-view";
import { LoginPage } from "./components/login-page";

type SymbolCfg = Record<string, any> & {
  SYMBOL_LIGHTER: string;
  SYMBOL_EXTENDED: string;
};

type Tab = "dashboard" | "settings";

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

const API_BASE = import.meta.env.VITE_API_BASE || `${window.location.protocol}//${window.location.hostname}:5001`;
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

export default function App() {
  const [user, setUser] = useState(localStorage.getItem("u") || "");
  const [pass, setPass] = useState(localStorage.getItem("p") || "");
  const [authed, setAuthed] = useState(false);
  const [loading, setLoading] = useState(false);
  const [symbols, setSymbols] = useState<SymbolCfg[]>([]);
  const [running, setRunning] = useState<string[]>([]);
  const [activeTab, setActiveTab] = useState<Tab>("dashboard");
  const [selectedPair, setSelectedPair] = useState<{ L: string; E: string } | null>(null);
  const [envLines, setEnvLines] = useState<EnvLine[]>([]);
  const [msg, setMsg] = useState("");
  const [autoAuthAttempted, setAutoAuthAttempted] = useState(false);
  const [createDraft, setCreateDraft] = useState({
    SYMBOL_LIGHTER: "NEW",
    SYMBOL_EXTENDED: "NEW-USD",
    MIN_SPREAD: 0.3,
    SPREAD_TP: 0.2,
    REPRICE_TICK: 0,
    MAX_POSITION_VALUE: 500,
    MAX_TRADE_VALUE: 25,
    MAX_OF_OB: 0.3,
    MAX_TRADES: null as number | null,
    MIN_HITS: 3,
    TEST_MODE: false,
    DEDUP_OB: true,
    WARM_UP_ORDERS: false,
  });
  const [createError, setCreateError] = useState("");
  const [createSaving, setCreateSaving] = useState(false);
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

  const authHeaders = useMemo(() => buildAuth(user, pass), [user, pass]);

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
  const totalProfit = bots.reduce((sum, b) => sum + (b.profit24h || 0), 0);

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
      }
      if (runRes.ok) {
        const r = await runRes.json();
        setRunning(r.running || []);
      }
    } catch {
      setMsg("Failed to load config");
    }
  }

  async function startStop(pair: SymbolCfg, action: "start" | "stop") {
    const url = `${API_BASE}/api/${action}?symbolL=${pair.SYMBOL_LIGHTER}&symbolE=${pair.SYMBOL_EXTENDED}`;
    try {
      await fetch(url, { method: "POST", headers: authHeaders });
      await loadConfig();
      setMsg(action === "start" ? "Bot started" : "Bot stopped");
    } catch {
      setMsg(`Failed to ${action}`);
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
    if (!authed || selectionRestored) return;
    try {
      const storedPair = localStorage.getItem("selectedPair");
      const storedTab = localStorage.getItem("selectedTab") as DetailTab | null;
      if (storedPair) {
        const parsed = JSON.parse(storedPair);
        if (parsed?.L && parsed?.E) {
          setSelectedPair({ L: parsed.L, E: parsed.E });
          if (storedTab && ["logs", "trades", "settings"].includes(storedTab)) {
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
    setCreateDraft({
      SYMBOL_LIGHTER: "NEW",
      SYMBOL_EXTENDED: "NEW-USD",
      MIN_SPREAD: 0.3,
      SPREAD_TP: 0.2,
      REPRICE_TICK: 0,
      MAX_POSITION_VALUE: 500,
      MAX_TRADE_VALUE: 25,
      MAX_OF_OB: 0.3,
      MAX_TRADES: null,
      MIN_HITS: 3,
      TEST_MODE: false,
      DEDUP_OB: true,
      WARM_UP_ORDERS: false,
    });
    setCreateError("");
  }

  async function handleCreateBot() {
    if (!createDraft.SYMBOL_LIGHTER.trim() || !createDraft.SYMBOL_EXTENDED.trim()) {
      setCreateError("Symbol fields are required");
      return;
    }
    if (symbols.some((s) => s.SYMBOL_LIGHTER === createDraft.SYMBOL_LIGHTER.trim() && s.SYMBOL_EXTENDED === createDraft.SYMBOL_EXTENDED.trim())) {
      setCreateError("Pair already exists");
      return;
    }
    setCreateSaving(true);
    setCreateError("");
    try {
      const newEntry = {
        SYMBOL_LIGHTER: createDraft.SYMBOL_LIGHTER.trim(),
        SYMBOL_EXTENDED: createDraft.SYMBOL_EXTENDED.trim(),
        MIN_SPREAD: Number(createDraft.MIN_SPREAD),
        SPREAD_TP: Number(createDraft.SPREAD_TP),
        REPRICE_TICK: Number(createDraft.REPRICE_TICK),
        MAX_POSITION_VALUE: createDraft.MAX_POSITION_VALUE === null ? null : Number(createDraft.MAX_POSITION_VALUE),
        MAX_TRADE_VALUE: Number(createDraft.MAX_TRADE_VALUE),
        MAX_OF_OB: Number(createDraft.MAX_OF_OB),
        MAX_TRADES: createDraft.MAX_TRADES === null ? null : Number(createDraft.MAX_TRADES),
        MIN_HITS: Number(createDraft.MIN_HITS),
        TEST_MODE: Boolean(createDraft.TEST_MODE),
        DEDUP_OB: Boolean(createDraft.DEDUP_OB),
        WARM_UP_ORDERS: Boolean(createDraft.WARM_UP_ORDERS),
      };
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

  const selected = selectedPair ? bots.find((p) => p.L === selectedPair.L && p.E === selectedPair.E) || null : null;
  const headerTitle = selected ? selected.L || selected.name : "QUANTING.FUN";
  const headerSubtitle = selected ? selected.E || selected.pair : "Manage and monitor your arbitrage bots";

  return (
    <>
    <div className="min-h-screen bg-gradient-to-br from-slate-950 via-slate-900 to-slate-950 text-white">
      <header className="bg-slate-900/80 backdrop-blur-xl border-b border-slate-800/50 sticky top-0 z-10">
        <div className="px-4 sm:px-6 py-4 flex flex-col md:flex-row md:items-center md:justify-between gap-3">
          <div className="flex items-center gap-3">
            {selected && (
              <button
                onClick={() => {
                  setSelectedPair(null);
                }}
                className="p-2 hover:bg-slate-800/50 rounded-lg transition-all text-slate-400 hover:text-white"
              >
                <ArrowLeft className="w-5 h-5" />
              </button>
            )}
              <div>
                <h1 className="text-white text-xl font-semibold">
                {headerTitle}
                </h1>
                <p className="text-slate-400 text-sm mt-1">
                {headerSubtitle}
                </p>
              </div>
            </div>
          <div className="grid grid-cols-2 sm:flex sm:flex-wrap items-center gap-3 w-full md:w-auto">
            <div className="bg-slate-800/50 backdrop-blur-sm border border-slate-700/50 rounded-xl px-4 py-3 text-center sm:text-left">
              <p className="text-slate-400 text-xs mb-0.5">Active Bots</p>
              <p className="text-white text-xl">
                {activeBots}
                <span className="text-slate-500">/{bots.length}</span>
              </p>
            </div>
            <div className="bg-gradient-to-br from-green-500/10 to-emerald-500/10 border border-green-500/20 rounded-xl px-4 py-3 text-center sm:text-left">
              <p className="text-slate-400 text-xs mb-0.5">24h Profit</p>
              <p className={`text-xl ${totalProfit >= 0 ? "text-green-400" : "text-red-400"}`}>
                {totalProfit >= 0 ? "+" : ""}
                {totalProfit.toFixed(2)}%
              </p>
            </div>
            <div className="flex justify-end sm:justify-start md:justify-end col-span-2 sm:col-auto">
              <button
                onClick={logout}
                className="flex items-center gap-2 px-3 py-2 w-full sm:w-auto justify-center text-slate-200 hover:text-white rounded-lg border border-slate-800/60 bg-slate-900/50 hover:bg-slate-800/50 transition-all text-sm"
              >
                <LogOut className="w-4 h-4" />
                Logout
              </button>
            </div>
          </div>
        </div>
      </header>

      {!selected && (
        <div className="bg-slate-900/30 backdrop-blur-sm border-b border-slate-800/50">
          <div className="px-4 sm:px-6">
            <nav className="flex flex-wrap gap-2 items-center">
              <button
                onClick={() => setActiveTab("dashboard")}
                className={`flex items-center gap-2 px-3 sm:px-4 py-2 sm:py-3 rounded-lg border-2 transition-all ${
                  activeTab === "dashboard"
                    ? "border-blue-500 text-white bg-blue-500/10"
                    : "border-transparent text-slate-400 hover:text-slate-300 hover:bg-slate-800/30"
                }`}
              >
                <Activity className="w-4 h-4" />
                Dashboard
              </button>
              <button
                onClick={() => setActiveTab("settings")}
                className={`flex items-center gap-2 px-3 sm:px-4 py-2 sm:py-3 rounded-lg border-2 transition-all ${
                  activeTab === "settings"
                    ? "border-blue-500 text-white bg-blue-500/10"
                    : "border-transparent text-slate-400 hover:text-slate-300 hover:bg-slate-800/30"
                }`}
              >
                <SettingsIcon className="w-4 h-4" />
                Settings
              </button>
              <div className="flex-1" />
            </nav>
          </div>
        </div>
      )}

      <main className="p-6">
        <div className="max-w-7xl mx-auto space-y-6">
          {!selected && activeTab === "dashboard" && (
            <div>
              <div className="mb-6 space-y-3">
                <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
                  <div>
                    <h2 className="text-white mb-1 text-lg">Active Bots</h2>
                    <p className="text-slate-400 text-sm">Pin, start/stop, and monitor your running pairs</p>
                  </div>
                  <div className="flex flex-col sm:flex-row sm:items-center gap-2 w-full sm:w-auto">
                    <div className="relative w-full sm:w-64">
                      <Search className="w-4 h-4 text-slate-500 absolute left-3 top-1/2 -translate-y-1/2" />
                      <input
                        value={searchTerm}
                        onChange={(e) => setSearchTerm(e.target.value)}
                        placeholder="Search bots..."
                        className="w-full pl-9 pr-3 py-2 rounded-lg bg-slate-900/70 border border-slate-700 text-sm text-white focus:outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-400/40 transition"
                      />
                    </div>
                    <button
                      type="button"
                      onClick={handleCreateBot}
                      disabled={createSaving}
                      className="flex items-center justify-center gap-2 px-3 py-2 rounded-lg bg-blue-600 text-white text-sm hover:bg-blue-500 transition shadow-lg shadow-blue-500/30 disabled:opacity-60 w-full sm:w-auto"
                    >
                      <Plus className="w-4 h-4" />
                      {createSaving ? "Creating..." : "New bot"}
                    </button>
                  </div>
                </div>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {pinnedList.length > 0 && (
                  <div className="md:col-span-2 text-xs uppercase tracking-wide text-slate-400 flex items-center gap-2">
                    <span className="h-px w-6 bg-slate-700" />
                    Pinned
                    <span className="h-px flex-1 bg-slate-700" />
                  </div>
                )}
                {pinnedList.map((bot) => (
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
                      localStorage.setItem("selectedTab", "decisions");
                      setInitialDetailTab("decisions");
                    }}
                  />
                ))}
                {pinnedList.length > 0 && <div className="md:col-span-2 h-px bg-slate-800/60" />}
                {pinnedList.length > 0 && unpinnedList.length > 0 && (
                  <div className="md:col-span-2 text-xs uppercase tracking-wide text-slate-400 flex items-center gap-2">
                    <span className="h-px w-6 bg-slate-700" />
                    Other Bots
                    <span className="h-px flex-1 bg-slate-700" />
                  </div>
                )}
                {unpinnedList.map((bot) => (
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
                      localStorage.setItem("selectedTab", "decisions");
                      setInitialDetailTab("decisions");
                    }}
                  />
                ))}
              </div>
              {filteredBots.length === 0 && (
                <div className="text-center text-slate-400 py-10 border border-dashed border-slate-800 rounded-xl bg-slate-900/40">
                  No bots found. Adjust your search.
                </div>
              )}
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
        initialTab={initialDetailTab || "decisions"}
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
