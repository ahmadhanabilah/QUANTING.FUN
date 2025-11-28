import { useState } from 'react';
import { Save, Eye, EyeOff, RotateCcw, AlertCircle } from 'lucide-react';

const defaultEnvVars = [
  { key: 'EXCHANGE_API_KEY', value: 'pk_live_abc123xyz789', type: 'secret' },
  { key: 'EXCHANGE_API_SECRET', value: 'sk_live_def456uvw012', type: 'secret' },
  { key: 'TELEGRAM_BOT_TOKEN', value: '1234567890:ABCdefGHIjklMNOpqrsTUVwxyz', type: 'secret' },
  { key: 'TELEGRAM_CHAT_ID', value: '-1001234567890', type: 'public' },
  { key: 'MAX_POSITION_SIZE', value: '0.05', type: 'public' },
  { key: 'STOP_LOSS_PERCENTAGE', value: '2', type: 'public' },
  { key: 'TAKE_PROFIT_PERCENTAGE', value: '5', type: 'public' },
  { key: 'TRADING_ENABLED', value: 'true', type: 'public' },
  { key: 'LOG_LEVEL', value: 'info', type: 'public' },
];

export function SettingsPanel() {
  const [envVars, setEnvVars] = useState(defaultEnvVars);
  const [visibleSecrets, setVisibleSecrets] = useState<Record<string, boolean>>({});
  const [showSuccess, setShowSuccess] = useState(false);

  const toggleVisibility = (key: string) => {
    setVisibleSecrets(prev => ({ ...prev, [key]: !prev[key] }));
  };

  const updateValue = (key: string, value: string) => {
    setEnvVars(envVars.map(env => env.key === key ? { ...env, value } : env));
  };

  const handleSave = () => {
    setShowSuccess(true);
    setTimeout(() => setShowSuccess(false), 3000);
  };

  const handleReset = () => {
    setEnvVars(defaultEnvVars);
    setVisibleSecrets({});
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="text-white mb-1 text-lg">Environment Settings</h2>
          <p className="text-slate-400 text-sm">Configure your bot environment variables</p>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={handleReset}
            className="flex items-center gap-2 px-3 py-2 bg-slate-800/50 hover:bg-slate-700/50 text-white rounded-lg transition-all text-sm border border-slate-700/50"
          >
            <RotateCcw className="w-4 h-4" />
            Reset
          </button>
          <button
            onClick={handleSave}
            className="flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-blue-500 to-blue-600 hover:from-blue-600 hover:to-blue-700 text-white rounded-lg transition-all text-sm shadow-lg shadow-blue-500/25"
          >
            <Save className="w-4 h-4" />
            Save Changes
          </button>
        </div>
      </div>

      {/* Success message */}
      {showSuccess && (
        <div className="mb-4 p-4 bg-gradient-to-r from-green-500/10 to-emerald-500/10 border border-green-500/30 rounded-xl flex items-center gap-3 shadow-lg shadow-green-500/10">
          <div className="w-2 h-2 rounded-full bg-green-400 animate-pulse" />
          <span className="text-green-400 text-sm font-medium">Settings saved successfully</span>
        </div>
      )}

      {/* Warning notice */}
      <div className="mb-6 p-4 bg-gradient-to-r from-yellow-500/10 to-orange-500/10 border border-yellow-500/30 rounded-xl flex items-start gap-3">
        <AlertCircle className="w-5 h-5 text-yellow-400 flex-shrink-0 mt-0.5" />
        <div>
          <p className="text-yellow-400 text-sm">
            <span className="font-medium">Warning:</span> Changes to these settings will require restarting your bots to take effect.
          </p>
        </div>
      </div>

      {/* Settings grid */}
      <div className="space-y-3 mb-6">
        {envVars.map(env => (
          <div key={env.key} className="bg-slate-950/50 backdrop-blur-sm border border-slate-800/50 rounded-xl p-5 hover:border-slate-700/50 transition-all">
            <div className="flex items-start gap-4">
              <div className="flex-1">
                <label className="block text-slate-200 text-sm mb-2 font-medium">
                  {env.key}
                  {env.type === 'secret' && (
                    <span className="ml-2 inline-flex items-center px-2.5 py-0.5 rounded-full text-xs bg-red-500/10 text-red-400 border border-red-500/30">
                      Secret
                    </span>
                  )}
                </label>
                <div className="relative">
                  <input
                    type={env.type === 'secret' && !visibleSecrets[env.key] ? 'password' : 'text'}
                    value={env.value}
                    onChange={(e) => updateValue(env.key, e.target.value)}
                    className="w-full px-3 py-2.5 bg-slate-900/50 border border-slate-700/50 rounded-lg text-slate-300 text-sm font-mono focus:outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20 transition-all pr-10"
                  />
                  {env.type === 'secret' && (
                    <button
                      onClick={() => toggleVisibility(env.key)}
                      className="absolute right-2 top-1/2 -translate-y-1/2 p-1.5 text-slate-400 hover:text-slate-300 transition-colors rounded hover:bg-slate-800/50"
                    >
                      {visibleSecrets[env.key] ? (
                        <EyeOff className="w-4 h-4" />
                      ) : (
                        <Eye className="w-4 h-4" />
                      )}
                    </button>
                  )}
                </div>
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* Add new variable */}
      <div className="mb-6">
        <button className="w-full p-5 bg-slate-900/30 border border-slate-800/50 border-dashed rounded-xl text-slate-400 hover:text-slate-300 hover:border-slate-700/50 hover:bg-slate-800/30 transition-all text-sm">
          + Add New Variable
        </button>
      </div>

      {/* Info section */}
      <div className="p-5 bg-gradient-to-br from-blue-500/5 to-purple-500/5 border border-blue-500/20 rounded-xl">
        <h3 className="text-slate-200 text-sm mb-3 font-medium flex items-center gap-2">
          <span className="w-1 h-4 bg-gradient-to-b from-blue-500 to-purple-500 rounded-full" />
          Configuration Tips
        </h3>
        <ul className="space-y-2 text-slate-400 text-sm">
          <li className="flex items-start gap-2">
            <span className="text-blue-400 mt-0.5">•</span>
            <span>Store sensitive API keys and secrets securely</span>
          </li>
          <li className="flex items-start gap-2">
            <span className="text-blue-400 mt-0.5">•</span>
            <span>Test configuration changes with a single bot first</span>
          </li>
          <li className="flex items-start gap-2">
            <span className="text-blue-400 mt-0.5">•</span>
            <span>Keep percentage values reasonable to limit risk</span>
          </li>
          <li className="flex items-start gap-2">
            <span className="text-blue-400 mt-0.5">•</span>
            <span>Enable Telegram notifications for important events</span>
          </li>
        </ul>
      </div>
    </div>
  );
}