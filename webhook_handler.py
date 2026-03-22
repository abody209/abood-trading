"""
Webhook Handler - Receives signals from TradingView indicators.
==============================================================
Two webhook endpoints:
  1. /webhook/gainzalgo  → GainzAlgo V2 "Once Per Bar" signals (CALL/PUT arrows)
  2. /webhook/luxalgo    → LuxAlgo SMC Order Block data

GainzAlgo JSON format expected:
{
    "secret": "gainzalgo_secret",
    "symbol": "EURUSD",
    "signal": "CALL" or "PUT",
    "price": 1.08500,
    "timeframe": "15",
    "timestamp": "2026-03-22T10:00:00Z"
}

LuxAlgo SMC JSON format expected:
{
    "secret": "luxalgo_secret",
    "symbol": "EURUSD",
    "order_block": "bullish" or "bearish" or "none",
    "ob_high": 1.08600,
    "ob_low": 1.08400,
    "price": 1.08500,
    "timestamp": "2026-03-22T10:00:00Z"
}
"""

import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any

import config

logger = logging.getLogger(__name__)


class WebhookHandler:
    """
    Manages incoming webhook data from TradingView.
    Stores the latest GainzAlgo signal and LuxAlgo SMC state per pair.
    """

    def __init__(self):
        # Latest GainzAlgo signal per pair
        # Key: symbol, Value: {signal, price, received_at, candle_id, ...}
        self._gainzalgo_signals: Dict[str, Dict[str, Any]] = {}

        # Latest LuxAlgo SMC state per pair
        # Key: symbol, Value: {order_block, ob_high, ob_low, price, received_at, ...}
        self._luxalgo_state: Dict[str, Dict[str, Any]] = {}

        # Anti-flicker: track last signal candle per pair
        self._last_signal_candle: Dict[str, str] = {}

    # ----------------------------------------------------------
    # GainzAlgo V2 Webhook
    # ----------------------------------------------------------

    def process_gainzalgo(self, data: dict) -> Optional[dict]:
        """
        Process a GainzAlgo V2 webhook.
        Returns the signal dict if valid, None if rejected.
        """
        # Validate secret
        if data.get("secret") != config.GAINZALGO_SECRET:
            # Fallback: check generic secret
            if data.get("secret") != config.WEBHOOK_SECRET:
                logger.warning("GainzAlgo webhook: invalid secret")
                return None

        symbol = self._normalize_symbol(data.get("symbol", ""))
        signal_type = data.get("signal", "").upper()
        price = float(data.get("price", 0))

        # Validate
        if symbol not in config.TRADING_PAIRS:
            logger.warning(f"GainzAlgo: symbol {symbol} not in allowed pairs")
            return None
        if signal_type not in ("CALL", "PUT"):
            logger.warning(f"GainzAlgo: invalid signal type: {signal_type}")
            return None

        # Anti-flicker: compute candle ID to prevent duplicates
        now_utc = datetime.now(timezone.utc)
        candle_id = self._get_candle_id(now_utc)

        if self._last_signal_candle.get(symbol) == candle_id:
            logger.info(f"GainzAlgo: duplicate signal for {symbol} candle {candle_id}, ignoring")
            return None

        # Calculate time remaining in current candle
        remaining_seconds = self._seconds_until_candle_close(now_utc)

        signal_data = {
            "symbol": symbol,
            "signal": signal_type,
            "price": price,
            "received_at": now_utc,
            "received_ts": time.time(),
            "candle_id": candle_id,
            "remaining_seconds": remaining_seconds,
        }

        self._gainzalgo_signals[symbol] = signal_data
        logger.info(f"GainzAlgo signal received: {symbol} {signal_type} @ {price:.5f} "
                     f"(candle {candle_id}, {remaining_seconds}s remaining)")

        return signal_data

    # ----------------------------------------------------------
    # LuxAlgo SMC Webhook
    # ----------------------------------------------------------

    def process_luxalgo(self, data: dict) -> Optional[dict]:
        """
        Process a LuxAlgo SMC webhook.
        Returns the SMC state dict if valid, None if rejected.
        """
        if data.get("secret") != config.LUXALGO_SECRET:
            if data.get("secret") != config.WEBHOOK_SECRET:
                logger.warning("LuxAlgo webhook: invalid secret")
                return None

        symbol = self._normalize_symbol(data.get("symbol", ""))
        order_block = data.get("order_block", "none").lower()
        ob_high = float(data.get("ob_high", 0))
        ob_low = float(data.get("ob_low", 0))
        price = float(data.get("price", 0))

        if symbol not in config.TRADING_PAIRS:
            logger.warning(f"LuxAlgo: symbol {symbol} not in allowed pairs")
            return None

        now_utc = datetime.now(timezone.utc)

        smc_data = {
            "symbol": symbol,
            "order_block": order_block,  # "bullish", "bearish", or "none"
            "ob_high": ob_high,
            "ob_low": ob_low,
            "price": price,
            "received_at": now_utc,
        }

        self._luxalgo_state[symbol] = smc_data
        logger.info(f"LuxAlgo SMC update: {symbol} OB={order_block} "
                     f"range=[{ob_low:.5f}-{ob_high:.5f}] price={price:.5f}")

        return smc_data

    # ----------------------------------------------------------
    # Query Methods (used by Pipeline)
    # ----------------------------------------------------------

    def get_latest_signal(self, symbol: str) -> Optional[dict]:
        """Get the latest GainzAlgo signal for a symbol."""
        return self._gainzalgo_signals.get(symbol)

    def get_smc_state(self, symbol: str) -> Optional[dict]:
        """Get the latest LuxAlgo SMC state for a symbol."""
        return self._luxalgo_state.get(symbol)

    def mark_signal_used(self, symbol: str):
        """Mark a signal as consumed (after confirmation sent)."""
        candle_id = self._gainzalgo_signals.get(symbol, {}).get("candle_id")
        if candle_id:
            self._last_signal_candle[symbol] = candle_id
        self._gainzalgo_signals.pop(symbol, None)
        logger.info(f"Signal marked as used for {symbol}")

    def clear_signal(self, symbol: str):
        """Clear a signal (e.g., after it failed a filter)."""
        self._gainzalgo_signals.pop(symbol, None)

    # ----------------------------------------------------------
    # Helpers
    # ----------------------------------------------------------

    @staticmethod
    def _normalize_symbol(raw: str) -> str:
        """Normalize symbol name: EURUSD, EUR/USD, EUR_USD → EURUSD"""
        return raw.upper().replace("/", "").replace("_", "").replace("=X", "").strip()

    @staticmethod
    def _get_candle_id(dt: datetime) -> str:
        """
        Generate a unique candle ID based on date + 15-min boundary.
        e.g., '2026-03-22_10:15'
        """
        minute = (dt.minute // config.CANDLE_INTERVAL) * config.CANDLE_INTERVAL
        return f"{dt.strftime('%Y-%m-%d')}_{dt.hour:02d}:{minute:02d}"

    @staticmethod
    def _seconds_until_candle_close(now: datetime) -> int:
        """Calculate seconds remaining until the current 15-min candle closes."""
        interval = config.CANDLE_INTERVAL
        current_boundary = (now.minute // interval) * interval
        next_boundary_minute = current_boundary + interval

        if next_boundary_minute >= 60:
            next_close = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        else:
            next_close = now.replace(minute=next_boundary_minute, second=0, microsecond=0)

        remaining = (next_close - now).total_seconds()
        return max(0, int(remaining))
