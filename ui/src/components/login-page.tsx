import React from "react";
import { Activity, ArrowRight, Lock, Shield, TrendingUp, Zap } from "lucide-react";

type Props = {
  user: string;
  pass: string;
  loading: boolean;
  msg: string;
  onUserChange: (val: string) => void;
  onPassChange: (val: string) => void;
  onSubmit: () => void;
  onClearMsg?: () => void;
};

export function LoginPage({ user, pass, loading, msg, onUserChange, onPassChange, onSubmit, onClearMsg }: Props) {
  const statBadges = [
    { label: "Live bots", value: "24", icon: Activity },
    { label: "Avg latency", value: "< 80ms", icon: Zap },
    { label: "Success rate", value: "99.9%", icon: TrendingUp },
  ];

  return (
    <div className="relative min-h-screen bg-gradient-to-br from-slate-950 via-slate-900 to-slate-950 flex items-center justify-center p-4 md:p-6 text-white overflow-hidden">
      <div className="pointer-events-none absolute inset-0 overflow-hidden">
        <div className="absolute -top-24 -left-10 h-80 w-80 rounded-full bg-blue-500/10 blur-3xl animate-pulse" />
        <div className="absolute bottom-0 right-0 h-96 w-96 rounded-full bg-purple-500/10 blur-3xl animate-pulse" style={{ animationDelay: "1s" }} />
        <div className="absolute top-1/2 left-1/4 h-60 w-60 rounded-full bg-cyan-500/5 blur-3xl" />
        <div className="absolute inset-0 bg-[linear-gradient(to_right,#1e293b15_1px,transparent_1px),linear-gradient(to_bottom,#1e293b15_1px,transparent_1px)] bg-[size:4rem_4rem]" />
      </div>

      <div className="relative z-10 w-full max-w-4xl space-y-8">
        <div className="text-center space-y-4">
          <div className="inline-flex items-center gap-3 px-4 py-2 bg-gradient-to-r from-blue-500/10 to-purple-500/10 border border-blue-500/30 rounded-full shadow-lg shadow-blue-500/10 backdrop-blur-sm">
            <TrendingUp className="w-5 h-5 text-blue-400" />
            <span className="text-blue-300 text-sm font-medium tracking-wide">Trading Bot Command Center</span>
          </div>
          <div className="space-y-3">
            <h1 className="text-4xl md:text-5xl font-bold leading-tight">
              <span className="bg-gradient-to-r from-blue-400 via-cyan-400 to-purple-400 bg-clip-text text-transparent">
                Arbitrage Stack
              </span>
            </h1>
            <p className="text-slate-300 text-base md:text-lg leading-relaxed">
              Securely manage your trading bots with live telemetry
            </p>
          </div>
        </div>

        <div className="grid grid-cols-3 gap-3">
          {statBadges.map((stat, idx) => {
            const Icon = stat.icon;
            return (
              <div
                key={stat.label}
                className="group rounded-xl border border-slate-800/50 bg-slate-900/60 backdrop-blur-sm px-3 py-4 shadow-inner hover:shadow-lg hover:border-slate-700/60 transition-all duration-300"
                style={{ animationDelay: `${idx * 100}ms` }}
              >
                <Icon className="w-4 h-4 text-slate-500 mb-2 group-hover:text-blue-400 transition-colors" />
                <p className="text-slate-400 text-xs mb-1">{stat.label}</p>
                <p className="text-white text-lg font-bold">{stat.value}</p>
              </div>
            );
          })}
        </div>

        <div className="relative overflow-hidden rounded-2xl border border-slate-800/60 bg-slate-900/80 backdrop-blur-xl p-6 md:p-8 shadow-2xl shadow-blue-900/20 max-w-xl mx-auto">
          <div className="pointer-events-none absolute inset-0 bg-gradient-to-br from-blue-500/5 via-transparent to-purple-500/5" />
          <div className="pointer-events-none absolute -top-24 -right-24 h-48 w-48 rounded-full bg-blue-500/10 blur-3xl" />
          <div className="relative space-y-6">
            <div className="text-center space-y-2">
              <h2 className="text-white text-2xl md:text-3xl font-bold">Welcome back</h2>
              <p className="text-slate-400 text-sm">Authenticate to manage your bots</p>
            </div>

            {msg && (
              <div className="p-4 bg-gradient-to-r from-red-500/10 to-red-600/10 border border-red-500/30 rounded-xl shadow-lg shadow-red-500/10 animate-in fade-in slide-in-from-top-2 duration-300 text-center text-red-300 text-sm font-medium">
                {msg}
              </div>
            )}

            <form
              className="space-y-4"
              onSubmit={(e) => {
                e.preventDefault();
                if (onClearMsg) onClearMsg();
                if (!loading) onSubmit();
              }}
            >
              <div className="grid gap-4">
                <div>
                  <label className="text-xs text-slate-400">User</label>
                  <div className="mt-1 relative">
                    <input
                      className="w-full px-3 py-3 rounded-lg bg-slate-900/70 border border-slate-700 text-sm text-white focus:outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-400/40 transition"
                      placeholder="User"
                      value={user}
                      onChange={(e) => {
                        onUserChange(e.target.value);
                        if (onClearMsg) onClearMsg();
                      }}
                    />
                    <Shield className="w-4 h-4 text-slate-500 absolute right-3 top-3.5" />
                  </div>
                </div>
                <div>
                  <label className="text-xs text-slate-400">Password</label>
                  <div className="mt-1 relative">
                    <input
                      className="w-full px-3 py-3 rounded-lg bg-slate-900/70 border border-slate-700 text-sm text-white focus:outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-400/40 transition"
                      placeholder="Password"
                      type="password"
                      value={pass}
                      onChange={(e) => {
                        onPassChange(e.target.value);
                        if (onClearMsg) onClearMsg();
                      }}
                    />
                    <Lock className="w-4 h-4 text-slate-500 absolute right-3 top-3.5" />
                  </div>
                </div>
              </div>

              <button
                type="submit"
                disabled={loading}
                className="group relative w-full py-4 bg-gradient-to-r from-blue-500 via-blue-600 to-purple-600 hover:from-blue-600 hover:via-blue-600 hover:to-purple-700 disabled:from-slate-700 disabled:via-slate-700 disabled:to-slate-700 text-white rounded-xl transition-all duration-300 shadow-lg shadow-blue-500/30 hover:shadow-blue-500/50 disabled:shadow-none font-semibold text-base overflow-hidden"
              >
                <div className="absolute inset-0 bg-gradient-to-r from-white/0 via-white/10 to-white/0 translate-x-[-100%] group-hover:translate-x-[100%] transition-transform duration-1000" />
                {loading ? (
                  <div className="flex items-center justify-center gap-3">
                    <div className="w-5 h-5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                    <span>Checking...</span>
                  </div>
                ) : (
                  <div className="flex items-center justify-center gap-2">
                    <ArrowRight className="w-5 h-5 group-hover:translate-x-1 transition-transform" />
                    <span>Login</span>
                  </div>
                )}
              </button>
            </form>

            <p className="text-center text-slate-500 text-xs pt-2">Use your control-plane credentials to continue</p>
          </div>
        </div>
      </div>
    </div>
  );
}
