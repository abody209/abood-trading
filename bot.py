#!/usr/bin/env python3
"""
ABOOD "القناص" الذكي - V1.0 Main Bot
=======================================
5-Stage Pipeline Trading System:
  Stage 1: Detection (GainzAlgo V2 Webhook)
  Stage 2: 120-Second Stability Rule
  Stage 3: LuxAlgo SMC Order Block Filter
  Stage 4: Candle Close Confirmation (Zero Latency)
  Stage 5: Post-Trade Audit (15min + 1s)

Messages:
  أ. Pre-Alert  → after Stages 2+3 pass
  ب. Execution  → at Stage 4 (candle close 00:00:00)
  ج. Result     → at Stage 5 (15 min later)

Tech: Python 3.10+, FastAPI, Telegram-Python-Bot, Precision Timer
All times: UTC+3
"""

import asyncio
import logging
import sys
import os
import threading
import time
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
    update_signal_result, update_daily_stats, get_pending_signals,
)
from webhook_handler import WebhookHandler
from pipeline import SignalPipeline
from precision_timer import (
    PrecisionTimer, now_utc, now_utc3, utc_to_utc3_str,
    next_candle_boundary, seconds_until_candle_close, UTC3,
)
from result_tracker import ResultTracker
from message_formatter import (
    format_pre_alert, format_execution, format_result,
    format_startup_message, format_stats_message, format_overall_stats,
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
# CORE COMPONENTS
# ============================================================
webhook_handler = WebhookHandler()
pipeline = SignalPipeline(webhook_handler)
timer = PrecisionTimer()
result_tracker = ResultTracker()

app = FastAPI(title="ABOOD القناص V1.0")
telegram_application = None


# ============================================================
# TELEGRAM SEND HELPER
# ============================================================

async def _send(text: str):
    """Send a text message to the Telegram channel."""
    if not telegram_application:
        return
    chat_id = config.TELEGRAM_CHAT_ID
    if not chat_id or chat_id.startswith("YOUR"):
        logger.warning("TELEGRAM_CHAT_ID not set")
        return
    try:
        await telegram_application.bot.send_message(chat_id=chat_id, text=text)
        logger.info(f"Telegram message sent ({len(text)} chars)")
    except Exception as e:
        logger.error(f"Telegram send failed: {e}")


# ============================================================
# TRADING HOURS CHECK
# ============================================================

def is_trading_hours() -> bool:
    """Check if current time is within trading hours."""
    now = now_utc()
    if config.ENABLE_TRADING_DAYS:
        if now.weekday() not in config.TRADING_DAYS:
            return False
    if config.ENABLE_TRADING_HOURS:
        if not (config.TRADING_START_HOUR_UTC <= now.hour < config.TRADING_END_HOUR_UTC):
            return False
    return True


# ============================================================
# FASTAPI WEBHOOK ENDPOINTS
# ============================================================

@app.get("/")
async def health():
    """Health check endpoint."""
    return {
        "status": "ok",
        "bot": "ABOOD القناص V1.0",
        "active_pipeline": len(pipeline.get_active_entries()),
        "has_active_trade": pipeline.has_active_trade(),
        "time_utc3": now_utc3().strftime("%H:%M:%S"),
    }


@app.post("/webhook/gainzalgo")
async def gainzalgo_webhook(request: Request):
    """
    Stage 1: Receive GainzAlgo V2 "Once Per Bar" signal.
    Starts the 5-stage pipeline.
    """
    try:
        data = await request.json()
        logger.info(f"GainzAlgo webhook received: {data}")

        if not is_trading_hours():
            return {"status": "skipped", "reason": "Outside trading hours"}

        # Block if there's already an active trade
        if pipeline.has_active_trade():
            return {"status": "skipped", "reason": "Active trade in progress"}

        # Process the GainzAlgo signal
        signal_data = webhook_handler.process_gainzalgo(data)
        if signal_data is None:
            return {"status": "rejected", "reason": "Invalid or duplicate signal"}

        # Start the pipeline
        entry = pipeline.on_signal_detected(signal_data)

        # Schedule Stage 2: 120-second stability check
        await timer.schedule_stability_check(
            signal_data["symbol"],
            _on_stability_check_complete,
            signal_data["symbol"],
        )

        return {
            "status": "accepted",
            "symbol": signal_data["symbol"],
            "signal": signal_data["signal"],
            "pipeline_state": entry["state"],
            "stability_check_in": f"{config.STABILITY_WINDOW_SECONDS}s",
        }

    except Exception as e:
        logger.error(f"GainzAlgo webhook error: {e}")
        return {"status": "error", "message": str(e)}


@app.post("/webhook/luxalgo")
async def luxalgo_webhook(request: Request):
    """
    Receive LuxAlgo SMC Order Block data.
    This data is used by Stage 3 of the pipeline.
    """
    try:
        data = await request.json()
        logger.info(f"LuxAlgo webhook received: {data}")

        smc_data = webhook_handler.process_luxalgo(data)
        if smc_data is None:
            return {"status": "rejected", "reason": "Invalid data"}

        return {
            "status": "accepted",
            "symbol": smc_data["symbol"],
            "order_block": smc_data["order_block"],
        }

    except Exception as e:
        logger.error(f"LuxAlgo webhook error: {e}")
        return {"status": "error", "message": str(e)}


@app.post("/webhook")
async def generic_webhook(request: Request):
    """
    Generic webhook endpoint (backward compatible).
    Treats incoming signals as GainzAlgo signals.
    """
    try:
        data = await request.json()
        # Check secret
        secret = request.headers.get("X-Webhook-Secret", data.get("secret", ""))
        if secret not in (config.WEBHOOK_SECRET, config.GAINZALGO_SECRET):
            raise HTTPException(status_code=401, detail="Invalid secret")

        logger.info(f"Generic webhook received: {data}")

        if not is_trading_hours():
            return {"status": "skipped", "reason": "Outside trading hours"}

        if pipeline.has_active_trade():
            return {"status": "skipped", "reason": "Active trade in progress"}

        # Normalize to GainzAlgo format
        if "secret" not in data:
            data["secret"] = config.GAINZALGO_SECRET

        signal_data = webhook_handler.process_gainzalgo(data)
        if signal_data is None:
            return {"status": "rejected", "reason": "Invalid signal"}

        entry = pipeline.on_signal_detected(signal_data)

        await timer.schedule_stability_check(
            signal_data["symbol"],
            _on_stability_check_complete,
            signal_data["symbol"],
        )

        return {"status": "accepted", "pipeline_state": entry["state"]}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Generic webhook error: {e}")
        return {"status": "error", "message": str(e)}


# ============================================================
# PIPELINE STAGE CALLBACKS
# ============================================================

async def _on_stability_check_complete(symbol: str):
    """
    Stage 2 callback: Called after 120 seconds.
    Check if the signal is still valid, then proceed to Stage 3.
    """
    logger.info(f"Stage 2 check for {symbol}")

    # Check stability
    passed, reason = pipeline.check_stability(symbol)
    if not passed:
        logger.info(f"Stage 2 FAILED for {symbol}: {reason}")
        webhook_handler.clear_signal(symbol)
        return

    logger.info(f"Stage 2 PASSED for {symbol}: {reason}")

    # Wick Filter (between Stage 2 and 3)
    # Try to get candle data from the signal
    entry = pipeline.get_entry(symbol)
    if entry:
        # We pass None for candle_data since we don't have OHLC from webhook
        # The wick filter will be skipped if no data is available
        # For full implementation, the GainzAlgo webhook should include OHLC
        wick_passed, wick_reason = pipeline.check_wick_filter(symbol)
        if not wick_passed:
            logger.info(f"Wick Filter FAILED for {symbol}: {wick_reason}")
            webhook_handler.clear_signal(symbol)
            return
        logger.info(f"Wick Filter: {wick_reason}")

    # Stage 3: SMC Filter
    smc_passed, smc_reason = pipeline.check_smc_filter(symbol)
    if not smc_passed:
        logger.info(f"Stage 3 FAILED for {symbol}: {smc_reason}")
        webhook_handler.clear_signal(symbol)
        return

    logger.info(f"Stage 3 PASSED for {symbol}: {smc_reason}")

    # ✅ Stages 2+3 passed → Mark READY and send Pre-Alert
    pipeline.mark_ready(symbol)
    await _send_pre_alert(symbol)

    # Schedule Stage 4: Candle close confirmation
    await timer.schedule_candle_close_confirmation(
        symbol,
        _on_candle_close,
        symbol,
    )


async def _send_pre_alert(symbol: str):
    """
    Send the Pre-Alert message (رسالة الاستعداد).
    Triggered when Stages 2+3 pass.
    """
    entry = pipeline.get_entry(symbol)
    if not entry:
        return

    # Calculate entry time (next candle boundary in UTC+3)
    next_boundary = next_candle_boundary()
    entry_time_utc3 = utc_to_utc3_str(next_boundary)

    # Calculate remaining minutes
    remaining_sec = seconds_until_candle_close()
    remaining_min = max(1, int(remaining_sec / 60))

    # Get stats
    stats = get_daily_stats()
    pair_stats = get_pair_stats(symbol)

    message = format_pre_alert(
        symbol=symbol,
        signal_type=entry["signal"],
        entry_time_utc3=entry_time_utc3,
        remaining_minutes=remaining_min,
        wins=stats["wins"],
        losses=stats["losses"],
        pair_wins=pair_stats["wins"],
        pair_losses=pair_stats["losses"],
    )

    await _send(message)
    logger.info(f"Pre-Alert sent: {symbol} {entry['signal']} entry={entry_time_utc3} "
                 f"({remaining_min} min remaining)")


async def _on_candle_close(symbol: str):
    """
    Stage 4 callback: Called at exact candle close (00:00:00).
    Confirm the signal is still valid and send execution message.
    """
    logger.info(f"Stage 4: Candle close confirmation for {symbol}")

    entry = pipeline.get_entry(symbol)
    if not entry or entry["state"] == pipeline.STATE_REJECTED:
        logger.info(f"Stage 4: {symbol} entry not found or rejected")
        return

    # Check if the signal is still present in the webhook handler
    latest_signal = webhook_handler.get_latest_signal(symbol)
    still_valid = (latest_signal is not None and latest_signal["signal"] == entry["signal"])

    # Get current price at the exact moment of candle close
    entry_price = result_tracker.get_current_price(symbol)
    if entry_price is None:
        # Retry once
        await asyncio.sleep(1)
        entry_price = result_tracker.get_current_price(symbol)

    if entry_price is None:
        logger.error(f"Stage 4: Cannot get entry price for {symbol}")
        pipeline.get_entry(symbol)["state"] = pipeline.STATE_REJECTED
        return

    # Confirm in pipeline
    confirmed, reason = pipeline.on_candle_close_confirmation(symbol, still_valid, entry_price)

    if not confirmed:
        logger.info(f"Stage 4 FAILED for {symbol}: {reason}")
        return

    logger.info(f"Stage 4 PASSED for {symbol}: {reason}")

    # Save to database
    entry = pipeline.get_entry(symbol)
    signal_id = save_signal(
        symbol=symbol,
        signal_type=entry["signal"],
        entry_time=entry["entry_time"],
        entry_datetime=entry["entry_datetime"],
        expiry_datetime=entry["expiry_datetime"],
        entry_price=entry_price,
        score=0,
        reasons="GainzAlgo V2 + 120s + SMC",
    )
    entry["signal_id"] = signal_id

    # Mark as active trade
    pipeline.mark_active(symbol)

    # Send Execution message (رسالة التنفيذ)
    exec_msg = format_execution(
        symbol=symbol,
        signal_type=entry["signal"],
        entry_time_utc3=entry["entry_time"],
    )
    await _send(exec_msg)
    logger.info(f"Execution message sent: {symbol} {entry['signal']} at {entry['entry_time']}")

    # Schedule Stage 5: Result check at expiry + 1 second
    await timer.schedule_result_check(
        symbol,
        entry["expiry_datetime"],
        _on_result_check,
        symbol,
    )


async def _on_result_check(symbol: str):
    """
    Stage 5 callback: Called at entry_time + 15min + 1s.
    Compare entry price with close price and send result.
    """
    logger.info(f"Stage 5: Result check for {symbol}")

    entry = pipeline.get_entry(symbol)
    if not entry:
        logger.error(f"Stage 5: No entry for {symbol}")
        return

    # Get close price with retries
    close_price = result_tracker.get_current_price(symbol)
    if close_price is None:
        await asyncio.sleep(3)
        close_price = result_tracker.get_current_price(symbol)
    if close_price is None:
        await asyncio.sleep(5)
        close_price = result_tracker.get_current_price(symbol)

    if close_price is None:
        logger.error(f"Stage 5: Cannot get close price for {symbol}")
        return

    # Resolve the trade in pipeline
    result_data = pipeline.resolve_trade(symbol, close_price)
    if result_data is None:
        logger.error(f"Stage 5: Failed to resolve trade for {symbol}")
        return

    # Update database
    signal_id = entry.get("signal_id")
    if signal_id:
        update_signal_result(signal_id, close_price, result_data["result"])
        update_daily_stats(symbol)

    # Send Result message (رسالة الحصاد)
    result_msg = format_result(
        symbol=symbol,
        entry_time_utc3=entry["entry_time"],
        result=result_data["result"],
    )
    await _send(result_msg)

    logger.info(f"Result sent: {symbol} {result_data['result']} "
                 f"entry={result_data['entry_price']:.5f} close={close_price:.5f}")


# ============================================================
# FALLBACK RESULT CHECKER (Safety Net)
# ============================================================

async def check_pending_results(context: ContextTypes.DEFAULT_TYPE):
    """
    Fallback: periodically check for any pending trades that
    might have been missed by the precision timer.
    """
    try:
        resolved = result_tracker.check_and_resolve_pending()
        for r in resolved:
            result_msg = format_result(
                symbol=r["symbol"],
                entry_time_utc3=utc_to_utc3_str(
                    datetime.now(timezone.utc).replace(
                        hour=int(r["entry_time"].split(":")[0]),
                        minute=int(r["entry_time"].split(":")[1])
                    )
                ) if ":" in r.get("entry_time", "") else r.get("entry_time", "??:??"),
                result=r["result"],
            )
            await _send(result_msg)

        # Cleanup old pipeline entries
        pipeline.cleanup_completed()

    except Exception as e:
        logger.error(f"Fallback result check error: {e}")


# ============================================================
# TELEGRAM COMMANDS
# ============================================================

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(format_startup_message())


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    symbol = context.args[0].upper() if context.args else None
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
    symbol = context.args[0].upper() if context.args else None
    s = get_overall_stats(symbol=symbol)
    await update.message.reply_text(format_overall_stats(s))


async def cmd_recent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    recent = get_recent_signals(limit=10)
    if not recent:
        await update.message.reply_text("لا توجد إشارات حديثة.")
        return
    lines = ["📋 آخر الإشارات:\n"]
    for r in recent:
        emoji = "✅" if r["result"] == "WIN" else ("❌" if r["result"] == "LOSS" else "⏳")
        lines.append(f"{emoji} {r['symbol']} {r['signal_type']} {r['entry_time']} | {r['result']}")
    await update.message.reply_text("\n".join(lines))


async def cmd_pipeline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current pipeline status."""
    entries = pipeline.get_active_entries()
    if not entries:
        await update.message.reply_text("🔍 لا توجد إشارات نشطة في الـ Pipeline حالياً.")
        return

    lines = ["🔍 حالة الـ Pipeline:\n"]
    for e in entries:
        time_utc3 = now_utc3().strftime("%H:%M:%S")
        lines.append(
            f"📊 {e['symbol']} {e['signal']}\n"
            f"   الحالة: {e['state']}\n"
            f"   الوقت: {time_utc3} (UTC+3)"
        )
    await update.message.reply_text("\n".join(lines))


# ============================================================
# MAIN
# ============================================================

def run_fastapi():
    """Run FastAPI in a separate thread."""
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="warning")


async def main():
    global telegram_application
    init_db()

    # Build Telegram application
    application = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()
    telegram_application = application

    # Register commands
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("stats", cmd_stats))
    application.add_handler(CommandHandler("today", cmd_today))
    application.add_handler(CommandHandler("overall", cmd_overall))
    application.add_handler(CommandHandler("recent", cmd_recent))
    application.add_handler(CommandHandler("pipeline", cmd_pipeline))

    # Schedule fallback result checker
    jq = application.job_queue
    jq.run_repeating(check_pending_results, interval=config.RESULT_CHECK_INTERVAL, first=30)

    # Start FastAPI in background thread
    threading.Thread(target=run_fastapi, daemon=True).start()

    # Initialize and start
    logger.info("=" * 60)
    logger.info("ABOOD القناص V1.0 - Starting...")
    logger.info(f"Pairs: {', '.join(config.TRADING_PAIRS)}")
    logger.info(f"Duration: {config.TRADE_DURATION} min | Candle: {config.CANDLE_INTERVAL} min")
    logger.info(f"Stability: {config.STABILITY_WINDOW_SECONDS}s | SMC: {config.SMC_FILTER_ENABLED}")
    logger.info(f"Wick Filter: {config.WICK_FILTER_ENABLED} (max ratio: {config.WICK_BODY_RATIO_MAX})")
    logger.info(f"Webhook endpoints: /webhook/gainzalgo, /webhook/luxalgo, /webhook")
    logger.info("=" * 60)

    await application.initialize()
    await application.start()
    await application.updater.start_polling()

    # Send startup message
    await _send(format_startup_message())
    logger.info("Startup message sent to Telegram.")

    try:
        while True:
            await asyncio.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        timer.shutdown()
        await application.updater.stop()
        await application.stop()
        await application.shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
