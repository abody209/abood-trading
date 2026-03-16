#!/usr/bin/env python3
"""
Joker Trading Bot - Main Entry Point
Telegram bot + FastAPI webhook server + scheduled scanning.
ONE TRADE AT A TIME: waits for result before sending next signal.
"""

import asyncio
import logging
import sys
import os
import threading
import uvicorn
from fastapi import FastAPI, Request, HTTPException
from datetime import datetime, timedelta, timezone
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes

sys.path.insert(0, os.path.dirname(__file__))

import config
from database import (
    init_db, save_signal, get_daily_stats, get_pair_stats,
    get_overall_stats, get_recent_signals, cleanup_old_data,
)
from signal_generator import SignalGenerator
from result_tracker import ResultTracker
from message_formatter import (
    format_signal_message, format_result_message,
    format_startup_message, format_stats_message,
    format_overall_stats, format_pre_alert_message,
    format_entry_message,
)

# ============================================================
# LOGGING
# ============================================================
os.makedirs(os.path.join(os.path.dirname(__file__), "logs"), exist_ok=True)
logging.basicConfig(
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    level=logging.INFO,
    handlers=[
        logging.FileHandler(os.path.join(os.path.dirname(__file__), "logs", "bot.log")),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

# ============================================================
# GLOBALS
# ============================================================
signal_generator = SignalGenerator()
result_tracker = ResultTracker()
app = FastAPI(title="Joker Trading Bot")
telegram_application = None

# --- ACTIVE TRADE LOCK ---
# Only one trade at a time. None = no active trade.
active_trade = None  # dict with trade info when a trade is open
active_trade_lock = asyncio.Lock() if hasattr(asyncio, 'Lock') else None


def is_trade_active():
    """Check if there's currently an open trade."""
    return active_trade is not None


def set_active_trade(trade_info):
    """Set the current active trade."""
    global active_trade
    active_trade = trade_info
    logger.info(f"Active trade set: {trade_info['symbol']} {trade_info['type']}")


def clear_active_trade():
    """Clear the active trade after result is received."""
    global active_trade
    if active_trade:
        logger.info(f"Active trade cleared: {active_trade['symbol']}")
    active_trade = None


# ============================================================
# WEBHOOK (secured)
# ============================================================

@app.get("/")
async def health():
    return {"status": "ok", "bot": config.BOT_NAME, "active_trade": is_trade_active()}


@app.post("/webhook")
async def tradingview_webhook(request: Request):
    secret = request.headers.get("X-Webhook-Secret", "")
    if secret != config.WEBHOOK_SECRET:
        body = await request.json()
        if body.get("secret") != config.WEBHOOK_SECRET:
            raise HTTPException(status_code=401, detail="Invalid secret")

    try:
        data = await request.json()
        logger.info(f"Webhook received: {data}")

        # Block if there's an active trade
        if is_trade_active():
            logger.info("Webhook signal ignored - active trade in progress")
            return {"status": "skipped", "message": "Active trade in progress"}

        symbol = data.get("symbol", "EURUSD")
        signal_type = data.get("signal", "").upper()
        duration = data.get("duration", str(config.DURATION_NORMAL))

        if signal_type not in ("CALL", "PUT"):
            return {"status": "error", "message": "Invalid signal type"}

        if telegram_application:
            await _process_webhook_signal(symbol, signal_type, duration, data)
            return {"status": "success"}

        return {"status": "error", "message": "Bot not ready"}
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return {"status": "error", "message": str(e)}


async def _process_webhook_signal(symbol, signal_type, duration, raw_data):
    now = datetime.now(timezone.utc)
    entry_time = now.strftime("%H:%M")
    expiry_dt = now + timedelta(minutes=int(duration))

    entry_price = float(raw_data.get("price", 0))
    if entry_price == 0:
        entry_price = result_tracker.get_current_price(symbol)
    if entry_price is None:
        logger.error(f"Cannot get price for webhook signal {symbol}")
        return

    stats = get_daily_stats()
    pair_stats = get_pair_stats(symbol)

    save_signal(symbol, signal_type, entry_time, now, expiry_dt, entry_price,
                score=float(raw_data.get("score", 0)),
                reasons=raw_data.get("reasons", "TradingView Webhook"))

    # Set active trade
    set_active_trade({
        "symbol": symbol, "type": signal_type,
        "expiry_dt": expiry_dt, "entry_time": entry_time,
    })

    message = format_signal_message(
        symbol, signal_type, entry_time, duration,
        stats["wins"], stats["losses"],
        pair_stats["wins"], pair_stats["losses"],
        score=raw_data.get("score", 0),
        reasons=raw_data.get("reasons", "TradingView"),
        entry_price=entry_price,
    )
    await _send(message)


# ============================================================
# HELPERS
# ============================================================

def is_trading_hours():
    now_utc = datetime.now(timezone.utc)
    # Check trading days (Monday=0 ... Sunday=6)
    if config.ENABLE_TRADING_DAYS:
        if now_utc.weekday() not in config.TRADING_DAYS:
            return False
    # Check trading hours
    if config.ENABLE_TRADING_HOURS:
        if not (config.TRADING_START_HOUR <= now_utc.hour < config.TRADING_END_HOUR):
            return False
    return True


async def _send(text):
    if not telegram_application:
        return
    chat_id = config.TELEGRAM_CHAT_ID
    if not chat_id or chat_id.startswith("YOUR"):
        logger.warning("TELEGRAM_CHAT_ID not set, skipping message")
        return
    try:
        await telegram_application.bot.send_message(chat_id=chat_id, text=text)
    except Exception as e:
        logger.error(f"Send message failed: {e}")


# ============================================================
# SCHEDULED JOBS
# ============================================================

async def check_and_send_signals(context: ContextTypes.DEFAULT_TYPE):
    if not is_trading_hours():
        return

    # --- ONE TRADE AT A TIME: skip if there's an active trade ---
    if is_trade_active():
        logger.info("Signal check skipped - waiting for active trade to finish")
        return

    try:
        signals = signal_generator.check_all_pairs()
        if not signals:
            return

        # Pick ONLY the best signal (highest score)
        best_signal = max(signals, key=lambda s: s.get("score", 0))
        sig = best_signal

        entry_price = sig["entry_price"]
        if entry_price is None:
            entry_price = result_tracker.get_current_price(sig["symbol"])
        if entry_price is None:
            return

        duration = int(sig["duration"])
        expiry_dt = sig["entry_datetime"] + timedelta(minutes=duration)

        stats = get_daily_stats()
        pair_stats = get_pair_stats(sig["symbol"])

        save_signal(
            sig["symbol"], sig["type"], sig["entry_time"],
            sig["entry_datetime"], expiry_dt, entry_price,
            score=sig.get("score", 0), reasons=sig.get("reasons", ""),
        )

        # Set active trade BEFORE sending
        set_active_trade({
            "symbol": sig["symbol"],
            "type": sig["type"],
            "expiry_dt": expiry_dt,
            "entry_time": sig["entry_time"],
            "duration": duration,
        })

        # --- PRE-ALERT: send now, entry after PRE_ALERT_MINUTES ---
        pre_alert_msg = format_pre_alert_message(
            sig["symbol"], sig["type"], sig["alert_time"],
            sig["entry_time"], sig["duration"],
            score=sig.get("score", 0),
            reasons=sig.get("reasons", ""),
            entry_price=entry_price,
            strength=sig.get("strength", "Normal"),
        )
        await _send(pre_alert_msg)

        # --- Schedule ENTRY confirmation after PRE_ALERT_MINUTES ---
        entry_data = {
            "symbol": sig["symbol"],
            "type": sig["type"],
            "entry_time": sig["entry_time"],
            "duration": sig["duration"],
            "wins": stats["wins"],
            "losses": stats["losses"],
            "pair_wins": pair_stats["wins"],
            "pair_losses": pair_stats["losses"],
            "score": sig.get("score", 0),
            "reasons": sig.get("reasons", ""),
            "entry_price": entry_price,
            "strength": sig.get("strength", "Normal"),
        }
        context.job_queue.run_once(
            send_entry_confirmation,
            when=config.PRE_ALERT_MINUTES * 60,
            data=entry_data,
            name=f"entry_{sig['symbol']}_{sig['entry_time']}",
        )
    except Exception as e:
        logger.error(f"Signal check error: {e}")


async def send_entry_confirmation(context: ContextTypes.DEFAULT_TYPE):
    """Send entry confirmation message when it's time to enter the trade."""
    try:
        d = context.job.data
        msg = format_entry_message(
            d["symbol"], d["type"], d["entry_time"], d["duration"],
            d["wins"], d["losses"], d["pair_wins"], d["pair_losses"],
            score=d["score"], reasons=d["reasons"],
            entry_price=d["entry_price"], strength=d["strength"],
        )
        await _send(msg)
    except Exception as e:
        logger.error(f"Entry confirmation error: {e}")


async def check_and_send_results(context: ContextTypes.DEFAULT_TYPE):
    """Check pending trades and send results. Clears active trade lock."""
    try:
        resolved = result_tracker.check_and_resolve_pending()
        for r in resolved:
            message = format_result_message(
                r["symbol"], r["entry_time"], r["signal_type"], r["result"],
                entry_price=r.get("entry_price"), close_price=r.get("close_price"),
            )
            await _send(message)

            # Clear active trade when result is received
            if active_trade and active_trade.get("symbol") == r["symbol"]:
                clear_active_trade()

        # Safety: clear active trade if it expired long ago (failsafe)
        if active_trade:
            expiry = active_trade.get("expiry_dt")
            if expiry and datetime.now(timezone.utc) > expiry + timedelta(minutes=5):
                logger.warning("Failsafe: clearing stale active trade")
                clear_active_trade()

    except Exception as e:
        logger.error(f"Result check error: {e}")


# ============================================================
# TELEGRAM COMMANDS
# ============================================================

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(format_startup_message())


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    symbol = context.args[0] if context.args else None
    if symbol:
        s = get_pair_stats(symbol)
        await update.message.reply_text(
            format_stats_message(s["wins"], s["losses"], s["win_rate"])
        )
    else:
        s = get_daily_stats()
        await update.message.reply_text(
            format_stats_message(s["wins"], s["losses"], s["win_rate"],
                                 pairs=s.get("pairs"))
        )


async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s = get_daily_stats()
    await update.message.reply_text(
        format_stats_message(s["wins"], s["losses"], s["win_rate"],
                             pairs=s.get("pairs"))
    )


async def cmd_overall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    symbol = context.args[0] if context.args else None
    s = get_overall_stats(symbol=symbol)
    await update.message.reply_text(format_overall_stats(s))


async def cmd_recent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    recent = get_recent_signals(limit=10)
    if not recent:
        await update.message.reply_text("No recent signals.")
        return
    lines = ["📋 آخر الإشارات:\n"]
    for r in recent:
        emoji = "✅" if r["result"] == "WIN" else ("❌" if r["result"] == "LOSS" else "⏳")
        lines.append(f"{emoji} {r['symbol']} {r['signal_type']} {r['entry_time']} | {r['result']}")
    await update.message.reply_text("\n".join(lines))


# ============================================================
# MAIN
# ============================================================

def run_fastapi():
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="warning")


async def main():
    global telegram_application
    init_db()

    application = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()
    telegram_application = application

    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("stats", cmd_stats))
    application.add_handler(CommandHandler("today", cmd_today))
    application.add_handler(CommandHandler("overall", cmd_overall))
    application.add_handler(CommandHandler("recent", cmd_recent))

    jq = application.job_queue
    jq.run_repeating(check_and_send_signals, interval=config.SIGNAL_CHECK_INTERVAL, first=10)
    jq.run_repeating(check_and_send_results, interval=config.RESULT_CHECK_INTERVAL, first=30)

    threading.Thread(target=run_fastapi, daemon=True).start()

    logger.info("Joker Trading Bot started successfully!")
    await application.initialize()
    await application.start()
    await application.updater.start_polling()

    # Send startup message to Telegram channel
    await _send(format_startup_message())
    logger.info("Startup message sent to Telegram.")

    try:
        while True:
            await asyncio.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        await application.updater.stop()
        await application.stop()
        await application.shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
