import { useState } from 'react';
import { Save, RotateCcw, AlertCircle } from 'lucide-react';

interface BotSettingsProps {
  botId: string;
  botName: string;
}

// Define different settings based on bot type
const getBotTypeSettings = (botName: string) => {
  if (botName.includes('Scalper')) {
    return {
      type: 'Scalper',
      settings: [
        { key: 'position_size', label: 'Position Size', value: '0.05', unit: 'BTC', description: 'Amount per trade' },
        { key: 'take_profit', label: 'Take Profit', value: '0.5', unit: '%', description: 'Target profit percentage' },
        { key: 'stop_loss', label: 'Stop Loss', value: '0.3', unit: '%', description: 'Maximum loss before exit' },
        { key: 'timeframe', label: 'Timeframe', value: '1m', type: 'select', options: ['1m', '5m', '15m', '30m'], description: 'Candle timeframe' },
        { key: 'rsi_period', label: 'RSI Period', value: '14', unit: '', description: 'RSI indicator period' },
        { key: 'rsi_oversold', label: 'RSI Oversold', value: '30', unit: '', description: 'Oversold threshold' },
        { key: 'rsi_overbought', label: 'RSI Overbought', value: '70', unit: '', description: 'Overbought threshold' },
        { key: 'max_trades', label: 'Max Concurrent Trades', value: '3', unit: '', description: 'Maximum simultaneous positions' },
      ]
    };
  } else if (botName.includes('Grid')) {
    return {
      type: 'Grid Bot',
      settings: [
        { key: 'investment_amount', label: 'Investment Amount', value: '1000', unit: 'USDT', description: 'Total grid investment' },
        { key: 'grid_levels', label: 'Grid Levels', value: '10', unit: '', description: 'Number of grid orders' },
        { key: 'upper_price', label: 'Upper Price', value: '2500', unit: 'USDT', description: 'Highest grid level' },
        { key: 'lower_price', label: 'Lower Price', value: '2000', unit: 'USDT', description: 'Lowest grid level' },
        { key: 'profit_per_grid', label: 'Profit Per Grid', value: '0.5', unit: '%', description: 'Target profit each level' },
        { key: 'stop_loss_enabled', label: 'Stop Loss', value: 'false', type: 'toggle', description: 'Enable stop loss protection' },
        { key: 'trailing_enabled', label: 'Trailing', value: 'false', type: 'toggle', description: 'Enable trailing grid' },
      ]
    };
  } else if (botName.includes('DCA')) {
    return {
      type: 'DCA (Dollar Cost Average)',
      settings: [
        { key: 'base_order', label: 'Base Order Size', value: '100', unit: 'USDT', description: 'Initial order amount' },
        { key: 'safety_orders', label: 'Safety Orders', value: '5', unit: '', description: 'Number of DCA orders' },
        { key: 'price_deviation', label: 'Price Deviation', value: '2.5', unit: '%', description: 'Price drop to trigger DCA' },
        { key: 'safety_order_size', label: 'Safety Order Size', value: '150', unit: 'USDT', description: 'Amount for each DCA order' },
        { key: 'safety_order_step', label: 'Safety Order Step Scale', value: '1.5', unit: 'x', description: 'Multiplier for each step' },
        { key: 'take_profit', label: 'Take Profit Target', value: '3.0', unit: '%', description: 'Total profit target' },
        { key: 'max_active_deals', label: 'Max Active Deals', value: '3', unit: '', description: 'Concurrent DCA positions' },
        { key: 'cooldown', label: 'Cooldown Period', value: '60', unit: 'min', description: 'Time between new deals' },
      ]
    };
  } else if (botName.includes('Arbitrage')) {
    return {
      type: 'Arbitrage',
      settings: [
        { key: 'min_spread', label: 'Minimum Spread', value: '0.5', unit: '%', description: 'Minimum profitable spread' },
        { key: 'max_slippage', label: 'Max Slippage', value: '0.2', unit: '%', description: 'Maximum acceptable slippage' },
        { key: 'order_size', label: 'Order Size', value: '500', unit: 'USDT', description: 'Amount per arbitrage trade' },
        { key: 'exchange_a', label: 'Primary Exchange', value: 'Binance', type: 'select', options: ['Binance', 'Coinbase', 'Kraken', 'KuCoin'], description: 'First exchange' },
        { key: 'exchange_b', label: 'Secondary Exchange', value: 'Coinbase', type: 'select', options: ['Binance', 'Coinbase', 'Kraken', 'KuCoin'], description: 'Second exchange' },
        { key: 'check_interval', label: 'Check Interval', value: '5', unit: 'sec', description: 'Frequency to check spreads' },
        { key: 'max_exposure', label: 'Max Exposure', value: '5000', unit: 'USDT', description: 'Total arbitrage capital' },
      ]
    };
  }
  
  return {
    type: 'Custom',
    settings: [
      { key: 'position_size', label: 'Position Size', value: '0.05', unit: 'BTC', description: 'Amount per trade' },
      { key: 'take_profit', label: 'Take Profit', value: '2.0', unit: '%', description: 'Target profit percentage' },
      { key: 'stop_loss', label: 'Stop Loss', value: '1.0', unit: '%', description: 'Maximum loss before exit' },
    ]
  };
};

export function BotSettings({ botId, botName }: BotSettingsProps) {
  const botConfig = getBotTypeSettings(botName);
  const [settings, setSettings] = useState(botConfig.settings);
  const [showSuccess, setShowSuccess] = useState(false);

  const updateSetting = (key: string, value: string) => {
    setSettings(settings.map(s => s.key === key ? { ...s, value } : s));
  };

  const handleSave = () => {
    setShowSuccess(true);
    setTimeout(() => setShowSuccess(false), 3000);
  };

  const handleReset = () => {
    setSettings(getBotTypeSettings(botName).settings);
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <div>
          <h3 className="text-white mb-1 text-lg">{botConfig.type} Configuration</h3>
          <p className="text-slate-400 text-sm">Configure strategy-specific parameters</p>
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
            <span className="font-medium">Warning:</span>sdd Changing these settings will restart the bot and may affect open positions.
          </p>
        </div>
      </div>

      {/* Settings grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
        {settings.map(setting => (
          <div key={setting.key} className="bg-slate-950/50 backdrop-blur-sm border border-slate-800/50 rounded-xl p-5 hover:border-slate-700/50 transition-all">
            <label className="block text-slate-200 text-sm mb-1 font-medium">
              {setting.label}
            </label>
            <p className="text-slate-500 text-xs mb-3">{setting.description}</p>
            
            {setting.type === 'select' ? (
              <select
                value={setting.value}
                onChange={(e) => updateSetting(setting.key, e.target.value)}
                className="w-full px-3 py-2.5 bg-slate-900/50 border border-slate-700/50 rounded-lg text-slate-300 text-sm focus:outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20 transition-all"
              >
                {setting.options?.map(option => (
                  <option key={option} value={option}>{option}</option>
                ))}
              </select>
            ) : setting.type === 'toggle' ? (
              <button
                onClick={() => updateSetting(setting.key, setting.value === 'true' ? 'false' : 'true')}
                className={`relative inline-flex h-7 w-12 items-center rounded-full transition-all shadow-inner ${
                  setting.value === 'true' ? 'bg-gradient-to-r from-blue-500 to-blue-600' : 'bg-slate-700'
                }`}
              >
                <span
                  className={`inline-block h-5 w-5 transform rounded-full bg-white shadow-lg transition-transform ${
                    setting.value === 'true' ? 'translate-x-6' : 'translate-x-1'
                  }`}
                />
              </button>
            ) : (
              <div className="relative">
                <input
                  type="text"
                  value={setting.value}
                  onChange={(e) => updateSetting(setting.key, e.target.value)}
                  className="w-full px-3 py-2.5 bg-slate-900/50 border border-slate-700/50 rounded-lg text-slate-300 text-sm focus:outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20 transition-all pr-16"
                />
                {setting.unit && (
                  <span className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-500 text-sm font-medium">
                    {setting.unit}
                  </span>
                )}
              </div>
            )}
          </div>
        ))}
      </div>

      {/* Info section */}
      <div className="p-5 bg-gradient-to-br from-blue-500/5 to-purple-500/5 border border-blue-500/20 rounded-xl">
        <h4 className="text-slate-200 text-sm mb-3 font-medium flex items-center gap-2">
          <span className="w-1 h-4 bg-gradient-to-b from-blue-500 to-purple-500 rounded-full" />
          Strategy Tips - {botConfig.type}
        </h4>
        <ul className="space-y-2 text-slate-400 text-sm">
          {botConfig.type === 'Scalper' && (
            <>
              <li className="flex items-start gap-2">
                <span className="text-blue-400 mt-0.5">•</span>
                <span>Lower timeframes increase trade frequency but require faster execution</span>
              </li>
              <li className="flex items-start gap-2">
                <span className="text-blue-400 mt-0.5">•</span>
                <span>Keep take profit and stop loss tight for scalping strategy</span>
              </li>
              <li className="flex items-start gap-2">
                <span className="text-blue-400 mt-0.5">•</span>
                <span>Monitor RSI levels to avoid overtrading in ranging markets</span>
              </li>
              <li className="flex items-start gap-2">
                <span className="text-blue-400 mt-0.5">•</span>
                <span>Limit concurrent trades to manage risk exposure</span>
              </li>
            </>
          )}
          {botConfig.type === 'Grid Bot' && (
            <>
              <li className="flex items-start gap-2">
                <span className="text-blue-400 mt-0.5">•</span>
                <span>Works best in ranging/sideways markets</span>
              </li>
              <li className="flex items-start gap-2">
                <span className="text-blue-400 mt-0.5">•</span>
                <span>Set price range based on historical support and resistance</span>
              </li>
              <li className="flex items-start gap-2">
                <span className="text-blue-400 mt-0.5">•</span>
                <span>More grid levels = smaller profit per trade but more frequent trades</span>
              </li>
              <li className="flex items-start gap-2">
                <span className="text-blue-400 mt-0.5">•</span>
                <span>Consider stop loss to protect against strong breakouts</span>
              </li>
            </>
          )}
          {botConfig.type === 'DCA (Dollar Cost Average)' && (
            <>
              <li className="flex items-start gap-2">
                <span className="text-blue-400 mt-0.5">•</span>
                <span>Larger safety order sizes help average down effectively</span>
              </li>
              <li className="flex items-start gap-2">
                <span className="text-blue-400 mt-0.5">•</span>
                <span>Increase step scale to space out orders during deep dips</span>
              </li>
              <li className="flex items-start gap-2">
                <span className="text-blue-400 mt-0.5">•</span>
                <span>Set realistic take profit based on market volatility</span>
              </li>
              <li className="flex items-start gap-2">
                <span className="text-blue-400 mt-0.5">•</span>
                <span>Use cooldown to prevent multiple entries in choppy conditions</span>
              </li>
            </>
          )}
          {botConfig.type === 'Arbitrage' && (
            <>
              <li className="flex items-start gap-2">
                <span className="text-blue-400 mt-0.5">•</span>
                <span>Account for withdrawal fees between exchanges</span>
              </li>
              <li className="flex items-start gap-2">
                <span className="text-blue-400 mt-0.5">•</span>
                <span>Lower minimum spread may increase opportunities but reduce profit</span>
              </li>
              <li className="flex items-start gap-2">
                <span className="text-blue-400 mt-0.5">•</span>
                <span>Fast execution is critical - monitor check interval performance</span>
              </li>
              <li className="flex items-start gap-2">
                <span className="text-blue-400 mt-0.5">•</span>
                <span>Ensure sufficient balance on both exchanges</span>
              </li>
            </>
          )}
        </ul>
      </div>
    </div>
  );
}