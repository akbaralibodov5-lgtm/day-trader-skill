"""Demo-only MT5 forex bot. Defaults to dry run and refuses live accounts."""
from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import MetaTrader5 as mt5

from .risk import RiskQuote, VolumeSpec, calculate_volume, within_daily_loss_limit
from .strategy import calculate_signal

TIMEFRAMES = {
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
}


def load_config(path: str) -> dict:
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    defaults = {
        "symbol": "EURUSD", "timeframe": "M15", "risk_percent": 0.5,
        "reward_risk": 1.5, "atr_stop_multiple": 1.5,
        "max_daily_loss_percent": 2.0, "max_spread_points": 25,
        "max_open_positions": 1, "max_quote_age_seconds": 30,
        "deviation_points": 10, "magic_number": 27102701,
        "enable_demo_orders": False, "require_ai_confirmation": False,
        "log_file": "mt5-forex/logs/mt5-forex.jsonl",
        "day_start_equity_file": "mt5-forex/state/day_start_equity.json",
    }
    defaults.update(cfg)
    if not 0 < float(defaults["risk_percent"]) <= 0.5:
        raise ValueError("risk_percent must be > 0 and <= 0.5")
    if not 0 < float(defaults["max_daily_loss_percent"]) <= 5:
        raise ValueError("max_daily_loss_percent must be > 0 and <= 5")
    if float(defaults["reward_risk"]) <= 0 or float(defaults["atr_stop_multiple"]) <= 0:
        raise ValueError("reward_risk and atr_stop_multiple must be positive")
    if defaults["timeframe"] not in TIMEFRAMES:
        raise ValueError("Unsupported timeframe")
    if int(defaults["max_open_positions"]) < 1:
        raise ValueError("max_open_positions must be at least 1")
    if int(defaults["max_quote_age_seconds"]) < 1 or int(defaults["max_spread_points"]) < 0:
        raise ValueError("quote age must be positive and spread limit nonnegative")
    return defaults


def log_event(path: str, event: dict) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    record = {"time_utc": datetime.now(timezone.utc).isoformat(), **event}
    with target.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def require_demo_account():
    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
    terminal = mt5.terminal_info()
    account = mt5.account_info()
    if terminal is None or account is None:
        raise RuntimeError("Cannot read terminal/account information")
    if not terminal.connected:
        raise RuntimeError("MT5 terminal is disconnected")
    if not terminal.trade_allowed:
        raise RuntimeError("Algorithmic trading is disabled in MT5 terminal")
    if account.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO:
        raise RuntimeError("Safety lock: this bot only supports DEMO accounts")
    return account


def daily_guard(account, cfg: dict) -> None:
    """Persist day-start equity. If date/account state is missing or corrupt, fail closed."""
    path = Path(cfg["day_start_equity_file"])
    path.parent.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).date().isoformat()
    state = None
    if path.exists():
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raise RuntimeError("Day-start equity state invalid; refusing trading")

    account_id = f"{account.login}:{account.server}"
    if state is None or state.get("date_utc") != today or state.get("account_id") != account_id:
        state = {
            "date_utc": today,
            "account_id": account_id,
            "start_equity": float(account.equity),
        }
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        temp.replace(path)

    start_equity = float(state.get("start_equity", 0))
    if start_equity <= 0 or account.equity < 0:
        raise RuntimeError("Day-start equity data invalid; refusing trading")
    drawdown = max(0.0, start_equity - float(account.equity))
    limit = start_equity * float(cfg["max_daily_loss_percent"]) / 100.0
    log_event(cfg["log_file"], {
        "event": "daily_risk_check", "account_id": account_id,
        "date_utc": today, "start_equity": start_equity,
        "current_equity": float(account.equity), "drawdown": drawdown,
        "daily_limit": limit,
    })
    if drawdown >= limit:
        raise RuntimeError("Daily loss limit reached; no new trades")

    # Don't allow a missing or wrong account record to be silently overwritten.
    if not within_daily_loss_limit(start_equity, float(account.equity),
                                   float(cfg["max_daily_loss_percent"])):
        raise RuntimeError("Daily loss guard blocked trading")


def supported_filling_mode(info):
    """Pick a policy supported by the symbol's filling flags."""
    flags = int(info.filling_mode)
    # SYMBOL_FILLING_FOK=1, SYMBOL_FILLING_IOC=2. RETURN is not permitted
    # for Market Execution, but is valid for other execution modes.
    if flags & 2:
        return mt5.ORDER_FILLING_IOC
    if flags & 1:
        return mt5.ORDER_FILLING_FOK
    if info.trade_exemode != mt5.SYMBOL_TRADE_EXECUTION_MARKET:
        return mt5.ORDER_FILLING_RETURN
    raise RuntimeError("No supported filling mode found for this symbol")


def build_request(cfg: dict, signal: dict, info, tick, account) -> dict | None:
    side = signal.get("signal")
    if side not in ("BUY", "SELL"):
        return None
    if info.point <= 0 or info.volume_step <= 0 or info.volume_min <= 0:
        raise RuntimeError("Invalid broker symbol specification")
    if tick.ask <= tick.bid or tick.bid <= 0:
        raise RuntimeError("Invalid market quote")
    spread_points = (tick.ask - tick.bid) / info.point
    if spread_points > cfg["max_spread_points"]:
        raise RuntimeError(f"Spread too high: {spread_points:.1f} points")

    age = time.time() - tick.time
    if age < -5 or age > cfg["max_quote_age_seconds"]:
        raise RuntimeError(f"Quote stale or timestamp invalid: age={age:.1f}s")

    entry = tick.ask if side == "BUY" else tick.bid
    min_stop = max(info.trade_stops_level * info.point, 2 * info.point)
    stop_distance = max(signal["atr14"] * cfg["atr_stop_multiple"], min_stop * 1.2)

    if side == "BUY":
        order_type = mt5.ORDER_TYPE_BUY
        sl = entry - stop_distance
        tp = entry + stop_distance * cfg["reward_risk"]
    else:
        order_type = mt5.ORDER_TYPE_SELL
        sl = entry + stop_distance
        tp = entry - stop_distance * cfg["reward_risk"]

    entry, sl, tp = (round(v, info.digits) for v in (entry, sl, tp))
    loss_per_lot_signed = mt5.order_calc_profit(order_type, cfg["symbol"], 1.0, entry, sl)
    if loss_per_lot_signed is None or loss_per_lot_signed >= 0:
        raise RuntimeError(f"Unable to calculate stop loss in account currency: {mt5.last_error()}")

    volume, estimated_loss = calculate_volume(RiskQuote(
        account_equity=float(account.equity),
        risk_percent=float(cfg["risk_percent"]),
        loss_per_lot=abs(float(loss_per_lot_signed)),
        volume_spec=VolumeSpec(info.volume_min, info.volume_max, info.volume_step),
    ))
    if volume <= 0:
        raise RuntimeError("Broker minimum lot would exceed risk budget; trade refused")

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": cfg["symbol"],
        "volume": volume,
        "type": order_type,
        "price": entry,
        "sl": sl,
        "tp": tp,
        "deviation": int(cfg["deviation_points"]),
        "magic": int(cfg["magic_number"]),
        "comment": "mt5-forex-demo",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": supported_filling_mode(info),
    }
    return request, estimated_loss, spread_points


def run(config_path: str) -> int:
    cfg = load_config(config_path)
    try:
        account = require_demo_account()
        daily_guard(account, cfg)

        symbol = cfg["symbol"]
        if not mt5.symbol_select(symbol, True):
            raise RuntimeError(f"Symbol not available: {symbol}")
        info = mt5.symbol_info(symbol)
        tick = mt5.symbol_info_tick(symbol)
        if info is None or tick is None:
            raise RuntimeError("Cannot read symbol metadata or quote")

        positions = mt5.positions_get()
        if positions is None:
            raise RuntimeError("Cannot inspect existing positions")
        if len(positions) >= int(cfg["max_open_positions"]):
            raise RuntimeError("Open-position limit reached")
        if any(p.symbol == symbol and p.magic == int(cfg["magic_number"]) for p in positions):
            raise RuntimeError("Bot already has an open position on this symbol")

        rates = mt5.copy_rates_from_pos(symbol, TIMEFRAMES[cfg["timeframe"]], 1, 120)
        if rates is None:
            raise RuntimeError(f"Cannot read rates: {mt5.last_error()}")
        signal = calculate_signal(rates)
        print("Signal:", json.dumps(signal, indent=2))

        proposal = build_request(cfg, signal, info, tick, account)
        if proposal is None:
            print("WAIT: no setup")
            return 0
        request, estimated_loss, spread = proposal
        event = {
            "symbol": symbol, "signal": signal["signal"],
            "volume": request["volume"], "price": request["price"],
            "sl": request["sl"], "tp": request["tp"],
            "estimated_stop_loss_account_currency": estimated_loss,
            "spread_points": spread, "mode": "demo" if cfg["enable_demo_orders"] else "dry-run",
        }
        print("Proposal:", json.dumps(event, indent=2))
        log_event(cfg["log_file"], {"event": "proposal", **event})

        if not cfg["enable_demo_orders"]:
            print("DRY RUN: no order sent. Demo execution remains off.")
            return 0

        account = mt5.account_info()
        if account is None or account.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO:
            raise RuntimeError("Demo lock failed immediately before order")
        daily_guard(account, cfg)

        # Revalidate current positions and quote immediately before sending.
        positions = mt5.positions_get()
        if positions is None or len(positions) >= int(cfg["max_open_positions"]):
            raise RuntimeError("Position limit check failed immediately before order")
        if any(p.symbol == symbol and p.magic == int(cfg["magic_number"]) for p in positions):
            raise RuntimeError("Duplicate bot position detected")

        fresh = mt5.symbol_info_tick(symbol)
        if fresh is None or time.time() - fresh.time > cfg["max_quote_age_seconds"]:
            raise RuntimeError("Quote stale immediately before order_send")
        if (fresh.ask - fresh.bid) / info.point > cfg["max_spread_points"]:
            raise RuntimeError("Spread limit breached immediately before order_send")
        current = fresh.ask if request["type"] == mt5.ORDER_TYPE_BUY else fresh.bid
        if abs(current - request["price"]) > int(cfg["deviation_points"]) * info.point:
            raise RuntimeError("Price moved; discard proposal and await next signal")

        check = mt5.order_check(request)
        # MT5 order_check uses retcode=0 for a successful check; other codes block.
        if check is None or check.retcode != 0:
            log_event(cfg["log_file"], {"event": "order_check_failed", "result": str(check)})
            raise RuntimeError(f"order_check rejected the order: {check}")

        result = mt5.order_send(request)
        if result is None:
            raise RuntimeError(f"order_send returned None: {mt5.last_error()}")
        log_event(cfg["log_file"], {
            "event": "order_result", "retcode": result.retcode,
            "order": result.order, "deal": result.deal, "comment": result.comment,
        })
        print("Order result:", result)
        if result.retcode not in (mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_PLACED):
            raise RuntimeError(f"Broker did not confirm order: retcode={result.retcode}")

        # Verify that the broker-visible position carries SL/TP after execution.
        time.sleep(0.5)
        positions = mt5.positions_get(symbol=symbol)
        if positions is None:
            raise RuntimeError("Order submitted, but position verification failed; inspect terminal immediately")
        ours = [p for p in positions if p.magic == int(cfg["magic_number"])]
        if not ours or any(p.sl <= 0 or p.tp <= 0 for p in ours):
            log_event(cfg["log_file"], {"event": "protective_stops_verification_failed", "positions": len(ours)})
            raise RuntimeError("Order result received but SL/TP not verified; inspect demo terminal immediately")
        log_event(cfg["log_file"], {"event": "protective_stops_verified", "positions": len(ours)})
        return 0
    finally:
        mt5.shutdown()


def main():
    parser = argparse.ArgumentParser(description="Demo-only MT5 forex bot (dry-run by default)")
    parser.add_argument("--config", default="mt5-forex/config.json")
    args = parser.parse_args()
    try:
        return run(args.config)
    except Exception as exc:
        logging.exception("Bot stopped safely: %s", exc)
        print(f"BOT STOPPED: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
