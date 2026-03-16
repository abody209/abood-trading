"""
Joker Trading Bot - Configuration
All settings can be overridden via environment variables.
"""
import os

# ============================================================
# TELEGRAM SETTINGS
# ============================================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8547784958:AAEJPryv_lKK3W9wvL4gfPjFAbkEmB3Bewc")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "-1003815910109")

# ============================================================
# TRADING PAIRS - Add or remove pairs freely
# ============================================================
TRADING_PAIRS = [
    ("EURUSD=X", "EURUSD"),
    ("GBPUSD=X", "GBPUSD"),
    ("USDJPY=X", "USDJPY"),
    ("AUDUSD=X", "AUDUSD"),
]

# ============================================================
# TIMEFRAME & TRADE
# ============================================================
TIMEFRAME = "5m"
TIMEZONE = os.getenv("BOT_TIMEZONE", "Asia/Riyadh")

# ============================================================
# PRE-ALERT: Send signal X minutes before entry
# ============================================================
PRE_ALERT_MINUTES = int(os.getenv("PRE_ALERT_MINUTES", "5"))

# ============================================================
# DYNAMIC DURATION (auto-selected based on signal strength)
# ============================================================
# Score >= 4.0 (strong signal)  -> 15 min
# Score >= 3.0 (good signal)    -> 10 min
# Score >= 2.0 (normal signal)  -> 5 min
DURATION_STRONG = 15   # for score >= 4.0
DURATION_GOOD = 10     # for score >= 3.0
DURATION_NORMAL = 5    # for score >= 2.0

# ============================================================
# SCORING SYSTEM
# ============================================================
MIN_SIGNAL_SCORE = float(os.getenv("MIN_SIGNAL_SCORE", "2.0"))
CONFLICT_THRESHOLD = float(os.getenv("CONFLICT_THRESHOLD", "0.5"))

# ============================================================
# INDICATORS
# ============================================================
RSI_PERIOD = int(os.getenv("RSI_PERIOD", "14"))
RSI_OVERBOUGHT = int(os.getenv("RSI_OVERBOUGHT", "65"))
RSI_OVERSOLD = int(os.getenv("RSI_OVERSOLD", "35"))

EMA_FAST = int(os.getenv("EMA_FAST", "9"))
EMA_SLOW = int(os.getenv("EMA_SLOW", "21"))

BB_PERIOD = int(os.getenv("BB_PERIOD", "20"))
BB_STD = float(os.getenv("BB_STD", "2.0"))

STOCH_K = int(os.getenv("STOCH_K", "14"))
STOCH_D = int(os.getenv("STOCH_D", "3"))
STOCH_SMOOTH = int(os.getenv("STOCH_SMOOTH", "3"))
STOCH_OVERBOUGHT = int(os.getenv("STOCH_OVERBOUGHT", "75"))
STOCH_OVERSOLD = int(os.getenv("STOCH_OVERSOLD", "25"))

ADX_PERIOD = int(os.getenv("ADX_PERIOD", "14"))
ADX_THRESHOLD = float(os.getenv("ADX_THRESHOLD", "25"))

ENABLE_BOLLINGER = True
ENABLE_RSI = True
ENABLE_EMA = True
ENABLE_STOCHASTIC = True
ENABLE_ADX = True
ENABLE_CANDLE_PATTERNS = True
ENABLE_MOMENTUM = True

# ============================================================
# BOT BEHAVIOR
# ============================================================
SIGNAL_CHECK_INTERVAL = int(os.getenv("SIGNAL_CHECK_INTERVAL", "45"))
RESULT_CHECK_INTERVAL = int(os.getenv("RESULT_CHECK_INTERVAL", "30"))
MIN_SIGNAL_INTERVAL = int(os.getenv("MIN_SIGNAL_INTERVAL", "10"))

# ============================================================
# TRADING HOURS (UTC) - 3:00 AM to 11:00 PM Riyadh = 0:00 to 20:00 UTC
# ============================================================
ENABLE_TRADING_HOURS = os.getenv("ENABLE_TRADING_HOURS", "true").lower() == "true"
TRADING_START_HOUR = int(os.getenv("TRADING_START_HOUR", "0"))   # 3:00 AM Riyadh = 0:00 UTC
TRADING_END_HOUR = int(os.getenv("TRADING_END_HOUR", "20"))      # 11:00 PM Riyadh = 20:00 UTC

# ============================================================
# TRADING DAYS (0=Monday ... 6=Sunday)
# ============================================================
ENABLE_TRADING_DAYS = os.getenv("ENABLE_TRADING_DAYS", "true").lower() == "true"
TRADING_DAYS = [0, 1, 2, 3, 4]  # Monday to Friday

# ============================================================
# WEBHOOK SECURITY
# ============================================================
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "joker_secret_change_me")

# ============================================================
# HOSTING
# ============================================================
PORT = int(os.getenv("PORT", "8000"))
HOST = os.getenv("HOST", "0.0.0.0")

# ============================================================
# DISPLAY
# ============================================================
BOT_NAME = "JOKER 15 M"
