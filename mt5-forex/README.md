# MT5 Forex Demo Module

A separate, demo-only starter module that adapts the repository's risk-first workflow to MetaTrader 5 forex. It does **not** change the existing US-equity skills and does not submit live orders.

## Safety contract

- Refuses to run unless the connected MT5 account is a demo account.
- Defaults to **signal / dry-run mode**. Order sending is disabled unless explicitly enabled in config, and the demo-account check remains mandatory.
- Uses MT5's symbol contract details and `order_calc_profit` to estimate stop-loss risk in the account currency.
- Sizes down to the broker's volume step and refuses the trade if minimum volume would exceed the risk budget.
- Requires a stop-loss and take-profit; checks quote freshness, spread, stop distance, daily loss guard and open-position limit.
- Calls `order_check` before `order_send`; logs the exact request and result without secrets.
- AI can be added only as an optional signal filter. The risk engine, not the AI, owns the order execution decision.

No software can guarantee fills at the stop price or profitability. Test on a broker demo account and review all settings before enabling demo order execution.

## Requirements

- Windows with MetaTrader 5 desktop installed and logged in to a broker-provided demo account
- Python 3.10+
- Python package: `MetaTrader5`

Install:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install MetaTrader5
```

## Quick start

1. Copy `config.example.json` to `config.json`.
2. Leave `enable_demo_orders` set to `false` for initial testing.
3. Open MT5 and sign into a demo account. Confirm the correct symbol in Market Watch (brokers may use `EURUSDm`, `EURUSD.a`, or another suffix).
4. Run:

```powershell
python -m mt5_forex --config mt5-forex/config.json
```

By default, the program prints a signal and a calculated order proposal only. It does not send an order.

The first CI job covers pure risk helpers only. It does not validate a broker's order execution, filling policy, or actual attached SL/TP. Keep demo orders disabled until you have reviewed the code and tested with your broker's demo terminal.

Demo order sending can be opted into by changing `enable_demo_orders` to `true` in `config.json`, but this remains experimental. The code still refuses non-demo accounts.

## Strategy

The included baseline is deliberately simple and transparent: completed M15 bars, EMA 20/50 trend direction, RSI 14 confirmation, and ATR-based stop distance. It is a **test strategy, not a profitable strategy claim**. The signal is calculated locally; no AI API is required to run it.

## Limitations / next steps

- The first iteration only supports one configured symbol and one new position at a time.
- A UTC day-start equity snapshot is persisted per account; protect the state file from deletion or tampering. It must be reviewed before long-running use.
- The bot is not an AI trader yet. The `require_ai_confirmation` flag is reserved and does not currently call an AI model.
- The bot is not a tick-accurate backtester. Before considering live trading, add realistic spread, commission, swaps, slippage, out-of-sample tests, and walk-forward validation.
- Keep API keys out of configuration files and GitHub. If an AI filter is added, place the key in an environment variable and fail closed on API errors.
- This add-on is independent of the project's existing stock-specific Yahoo Finance/Alpaca modules.
