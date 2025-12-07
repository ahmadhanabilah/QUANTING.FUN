import { useEffect, useState } from "react";
import { AlertCircle, Eye, EyeOff, RotateCcw, Save } from "lucide-react";

type EnvLine =
  | { type: "pair"; key: string; value: string }
  | { type: "comment"; raw: string };

type SettingsPanelProps = {
  envLines: EnvLine[];
  onChange: (key: string, value: string) => void;
  onSave: () => Promise<boolean> | boolean;
  onReset: () => Promise<boolean> | boolean;
};

const isSecretKey = (key: string) => /(key|secret|pass|token)/i.test(key);

export function SettingsPanel({ envLines, onChange, onSave, onReset }: SettingsPanelProps) {
  const [visibleSecrets, setVisibleSecrets] = useState<Record<string, boolean>>({});
  const [showSuccess, setShowSuccess] = useState(false);
  const [saving, setSaving] = useState(false);
  const [resetting, setResetting] = useState(false);

  useEffect(() => {
    // Reset toggles whenever a fresh env file is loaded
    setVisibleSecrets({});
  }, [envLines]);

  const handleSave = async () => {
    setSaving(true);
    try {
      const ok = await onSave();
      if (ok) {
        setShowSuccess(true);
        setTimeout(() => setShowSuccess(false), 3000);
      }
    } finally {
      setSaving(false);
    }
  };

  const handleReset = async () => {
    setResetting(true);
    try {
      await onReset();
      setVisibleSecrets({});
    } finally {
      setResetting(false);
    }
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="text-white mb-1 text-lg">Environment Settings</h2>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={handleReset}
            disabled={resetting || saving}
            className="flex items-center gap-2 px-3 py-2 bg-slate-800/50 hover:bg-slate-700/50 disabled:opacity-60 text-white rounded-lg transition-all text-sm border border-slate-700/50"
          >
            <RotateCcw className={`w-4 h-4 ${resetting ? "animate-spin" : ""}`} />
            <span className="hidden sm:inline">Reset</span>
          </button>
          <button
            onClick={handleSave}
            disabled={saving || resetting}
            className="flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-blue-500 to-blue-600 hover:from-blue-600 hover:to-blue-700 disabled:opacity-60 text-white rounded-lg transition-all text-sm shadow-lg shadow-blue-500/25"
          >
            <Save className={`w-4 h-4 ${saving ? "animate-pulse" : ""}`} />
            <span className="hidden sm:inline">Save Changes</span>
          </button>
        </div>
      </div>

      {showSuccess && (
        <div className="mb-4 p-4 bg-gradient-to-r from-green-500/10 to-emerald-500/10 border border-green-500/30 rounded-xl flex items-center gap-3 shadow-lg shadow-green-500/10">
          <div className="w-2 h-2 rounded-full bg-green-400 animate-pulse" />
          <span className="text-green-400 text-sm font-medium">Settings saved successfully</span>
        </div>
      )}

      <div className="mb-6 p-4 bg-gradient-to-r from-yellow-500/10 to-orange-500/10 border border-yellow-500/30 rounded-xl flex items-start gap-3">
        <AlertCircle className="w-5 h-5 text-yellow-400 flex-shrink-0 mt-0.5" />
        <div>
          <p className="text-yellow-400 text-sm">
            <span className="font-medium">Warning:</span> Changes to these settings will require restarting your bots to take effect.
          </p>
        </div>
      </div>

      <div className="space-y-3 mb-6">
        {!envLines.length && (
          <div className="bg-slate-900/40 border border-slate-800/60 rounded-xl p-4 text-sm text-slate-400">
            No environment values loaded.
          </div>
        )}
        {envLines.map((env, idx) =>
          env.type === "comment" ? (
            <div key={`comment-${idx}`} className="text-slate-500 text-xs font-mono px-2">
              {env.raw}
            </div>
          ) : (
            <div
              key={env.key}
              className="bg-slate-950/50 backdrop-blur-sm border border-slate-800/50 rounded-xl p-5 hover:border-slate-700/50 transition-all"
            >
              <div className="flex items-start gap-4">
                <div className="flex-1">
                  <label className="block text-slate-200 text-sm mb-2 font-medium">
                    {env.key}
                    {isSecretKey(env.key) && (
                      <span className="ml-2 inline-flex items-center px-2.5 py-0.5 rounded-full text-xs bg-red-500/10 text-red-400 border border-red-500/30">
                        Secret
                      </span>
                    )}
                  </label>
                  <div className="relative">
                    <input
                      type={isSecretKey(env.key) && !visibleSecrets[env.key] ? "password" : "text"}
                      value={env.value}
                      onChange={(e) => onChange(env.key, e.target.value)}
                      className="w-full px-3 py-2.5 bg-slate-900/50 border border-slate-700/50 rounded-lg text-slate-300 text-sm font-mono focus:outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20 transition-all pr-10"
                    />
                    {isSecretKey(env.key) && (
                      <button
                        onClick={() => setVisibleSecrets((prev) => ({ ...prev, [env.key]: !prev[env.key] }))}
                        className="absolute right-2 top-1/2 -translate-y-1/2 p-1.5 text-slate-400 hover:text-slate-300 transition-colors rounded hover:bg-slate-800/50"
                      >
                        {visibleSecrets[env.key] ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                      </button>
                    )}
                  </div>
                </div>
              </div>
            </div>
          )
        )}
      </div>

    </div>
  );
}
