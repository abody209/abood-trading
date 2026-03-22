"""
ABOOD "القناص" الذكي - V1.0 Configuration
============================================
5-Stage Pipeline: Detection → 120s Rule → SMC Filter → Confirmation → Result
Fixed 15-minute trades on high-liquidity pairs only.
All times displayed in UTC+3.
"""
import os

# ============================================================
# TELEGRAM
# ============================================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8547784958:AAEJPryv_lKK3W9wvL4gfPjFAbkEmB3Bewc")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "-1003815910109")

# ============================================================
# TRADING PAIRS - High liquidity only (per spec)
# ============================================================
TRADING_PAIRS = ["EURUSD", "GBPUSD", "AUDUSD"]

# Yahoo Finance symbol mapping
YF_SYMBOL_MAP = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "AUDUSD": "AUDUSD=X",
}

# ============================================================
# TIMEFRAME & TRADE
# ============================================================
TIMEFRAME = "15m"                # 15-minute candles
TRADE_DURATION = 15              # Fixed 15 minutes
CANDLE_INTERVAL = 15             # Candle boundaries: :00, :15, :30, :45

# ============================================================
# TIMEZONE
# ============================================================
UTC_OFFSET = 3                   # UTC+3 (Riyadh / AST)

# ============================================================
# 5-STAGE PIPELINE SETTINGS
# ============================================================

# Stage 1: Detection - GainzAlgo V2 "Once Per Bar" webhook
# (No config needed - handled by webhook receiver)

# Stage 2: The 120-Second Stability Rule
STABILITY_WINDOW_SECONDS = 120   # Signal must persist for 120s without vanishing

# Stage 3: LuxAlgo SMC Filter
# CALL accepted only if price is inside/touching Bullish Order Block
# PUT accepted only if price is inside/touching Bearish Order Block
SMC_FILTER_ENABLED = True

# Stage 4: Execution Confirmation at candle close (00:00:00)
# Confirm the arrow still exists on the new candle
CONFIRMATION_ENABLED = True

# Stage 5: Post-Trade Audit
# Check price at entry_time + 15min + 1 second
RESULT_CHECK_DELAY_SECONDS = 1   # Extra second after 15 min for price settle
PRICE_PRECISION = 5              # Compare prices to 5 decimal places

# ============================================================
# WICK FILTER (Anti-Weak-Impulse)
# ============================================================
# Reject if previous candle's wick > 40% of body
WICK_FILTER_ENABLED = True
WICK_BODY_RATIO_MAX = 0.40       # Max wick/body ratio allowed

# ============================================================
# ANTI-FLICKER
# ============================================================
# Prevent duplicate signals for the same candle
MIN_SIGNAL_INTERVAL_SECONDS = 900  # 15 minutes = 900 seconds

# ============================================================
# WEBHOOK SECURITY
# ============================================================
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "abood_sniper_v1_secret")

# Separate secrets for each TradingView indicator
GAINZALGO_SECRET = os.getenv("GAINZALGO_SECRET", "gainzalgo_secret")
LUXALGO_SECRET = os.getenv("LUXALGO_SECRET", "luxalgo_secret")

# ============================================================
# TRADING HOURS (UTC)
# 3:00 AM Riyadh (UTC+3) = 00:00 UTC
# 11:00 PM Riyadh (UTC+3) = 20:00 UTC
# ============================================================
ENABLE_TRADING_HOURS = os.getenv("ENABLE_TRADING_HOURS", "true").lower() == "true"
TRADING_START_HOUR_UTC = int(os.getenv("TRADING_START_HOUR", "0"))
TRADING_END_HOUR_UTC = int(os.getenv("TRADING_END_HOUR", "20"))

# ============================================================
# TRADING DAYS (0=Monday ... 6=Sunday)
# ============================================================
ENABLE_TRADING_DAYS = os.getenv("ENABLE_TRADING_DAYS", "true").lower() == "true"
TRADING_DAYS = [0, 1, 2, 3, 4]  # Monday to Friday

# ============================================================
# HOSTING
# ============================================================
PORT = int(os.getenv("PORT", "8000"))
HOST = os.getenv("HOST", "0.0.0.0")

# ============================================================
# DISPLAY
# ============================================================
BOT_NAME = "ABOOD TRADING"
BOT_DISPLAY_HEADER = "abood Trading 15M POCKETOPTION BOT 🔵"

# ============================================================
# RESULT CHECK FALLBACK
# ============================================================
RESULT_CHECK_INTERVAL = int(os.getenv("RESULT_CHECK_INTERVAL", "30"))

# ============================================================
# LOGO (reference only, not sent in messages)
# ============================================================
LOGO_PATH = os.path.join(os.path.dirname(__file__), "logo.png")
