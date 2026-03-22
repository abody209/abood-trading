"""
The 5-Stage Signal Pipeline - "القناص" Engine
================================================
Stage 1: Detection        → GainzAlgo V2 "Once Per Bar" webhook received
Stage 2: 120s Stability   → Signal must persist 120 seconds without vanishing
Stage 3: SMC Filter       → LuxAlgo Order Block confirmation
Stage 4: Candle Confirm   → Arrow still present at candle close (00:00:00)
Stage 5: Post-Trade Audit → Compare entry vs close price after 15 min

This module manages Stages 2, 3, and the Wick Filter.
Stages 1 (webhook) and 4-5 (timing) are handled by bot.py + precision_timer.py.
"""

import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Tuple

import config
from webhook_handler import WebhookHandler

logger = logging.getLogger(__name__)


class SignalPipeline:
    """
    Manages the lifecycle of each signal through the 5-stage pipeline.
    """

    # Signal states
    STATE_DETECTED = "DETECTED"           # Stage 1: GainzAlgo signal received
    STATE_STABILITY_CHECK = "STABILITY"   # Stage 2: Waiting 120s
    STATE_STABILITY_PASSED = "STABLE"     # Stage 2 passed
    STATE_SMC_PASSED = "SMC_CONFIRMED"    # Stage 3 passed
    STATE_READY = "READY"                 # Stages 2+3 passed → send pre-alert
    STATE_AWAITING_CONFIRM = "AWAITING"   # Waiting for Stage 4 (candle close)
    STATE_CONFIRMED = "CONFIRMED"         # Stage 4 passed → send execution msg
    STATE_ACTIVE = "ACTIVE"               # Trade is live, waiting for result
    STATE_COMPLETED = "COMPLETED"         # Stage 5: Result received
    STATE_REJECTED = "REJECTED"           # Failed a filter

    def __init__(self, webhook_handler: WebhookHandler):
        self.wh = webhook_handler

        # Active pipeline entries per symbol
        # Key: symbol, Value: pipeline state dict
        self._entries: Dict[str, dict] = {}

    # ----------------------------------------------------------
    # Stage 1: Detection (called when GainzAlgo webhook arrives)
    # ----------------------------------------------------------

    def on_signal_detected(self, signal_data: dict) -> dict:
        """
        Stage 1: A new GainzAlgo signal has been detected.
        Initialize the pipeline entry and start the 120s timer.
        """
        symbol = signal_data["symbol"]

        # If there's already an active pipeline for this symbol, reject
        existing = self._entries.get(symbol)
        if existing and existing["state"] not in (self.STATE_COMPLETED, self.STATE_REJECTED):
            logger.info(f"Pipeline: {symbol} already has active entry in state {existing['state']}")
            return existing

        entry = {
            "symbol": symbol,
            "signal": signal_data["signal"],
            "price_at_detection": signal_data["price"],
            "detected_at": signal_data["received_at"],
            "detected_ts": signal_data["received_ts"],
            "candle_id": signal_data["candle_id"],
            "remaining_seconds": signal_data["remaining_seconds"],
            "state": self.STATE_DETECTED,
            "stability_start_ts": time.time(),
            "smc_confirmed": False,
            "wick_passed": False,
            "entry_time": None,       # Will be set at candle close
            "entry_price": None,      # Will be set at candle close
            "expiry_time": None,
            "result": None,
        }

        self._entries[symbol] = entry
        logger.info(f"Pipeline Stage 1: {symbol} {signal_data['signal']} detected "
                     f"(candle {signal_data['candle_id']}, {signal_data['remaining_seconds']}s remaining)")

        return entry

    # ----------------------------------------------------------
    # Stage 2: The 120-Second Stability Rule
    # ----------------------------------------------------------

    def check_stability(self, symbol: str) -> Tuple[bool, str]:
        """
        Stage 2: Check if 120 seconds have elapsed since detection.
        The signal must still be present (not cleared by a counter-webhook).

        Returns: (passed: bool, reason: str)
        """
        entry = self._entries.get(symbol)
        if not entry:
            return False, "No pipeline entry"

        if entry["state"] == self.STATE_REJECTED:
            return False, "Signal was rejected"

        # Check if signal was cancelled (counter-signal received)
        latest_signal = self.wh.get_latest_signal(symbol)
        if latest_signal is None:
            # Signal vanished
            entry["state"] = self.STATE_REJECTED
            logger.info(f"Pipeline Stage 2 FAIL: {symbol} signal vanished before 120s")
            return False, "Signal vanished (flicker)"

        # Check if signal direction changed
        if latest_signal["signal"] != entry["signal"]:
            entry["state"] = self.STATE_REJECTED
            logger.info(f"Pipeline Stage 2 FAIL: {symbol} signal flipped "
                         f"{entry['signal']} → {latest_signal['signal']}")
            return False, "Signal direction changed"

        # Check elapsed time
        elapsed = time.time() - entry["stability_start_ts"]
        if elapsed < config.STABILITY_WINDOW_SECONDS:
            remaining = config.STABILITY_WINDOW_SECONDS - elapsed
            return False, f"Stability check: {remaining:.0f}s remaining"

        # PASSED
        entry["state"] = self.STATE_STABILITY_PASSED
        logger.info(f"Pipeline Stage 2 PASS: {symbol} signal stable for {elapsed:.0f}s")
        return True, "Stability confirmed"

    # ----------------------------------------------------------
    # Stage 3: LuxAlgo SMC Filter
    # ----------------------------------------------------------

    def check_smc_filter(self, symbol: str) -> Tuple[bool, str]:
        """
        Stage 3: Check LuxAlgo Smart Money Concepts Order Block.
        - CALL: price must be inside/touching Bullish Order Block
        - PUT: price must be inside/touching Bearish Order Block

        Returns: (passed: bool, reason: str)
        """
        entry = self._entries.get(symbol)
        if not entry:
            return False, "No pipeline entry"

        if not config.SMC_FILTER_ENABLED:
            entry["smc_confirmed"] = True
            entry["state"] = self.STATE_SMC_PASSED
            logger.info(f"Pipeline Stage 3 SKIP: SMC filter disabled")
            return True, "SMC filter disabled (bypassed)"

        smc_state = self.wh.get_smc_state(symbol)
        if smc_state is None:
            # No SMC data received yet - allow pass with warning
            # This handles the case where LuxAlgo webhook hasn't fired yet
            logger.warning(f"Pipeline Stage 3: No SMC data for {symbol}, allowing pass")
            entry["smc_confirmed"] = True
            entry["state"] = self.STATE_SMC_PASSED
            return True, "No SMC data available (allowed)"

        signal_type = entry["signal"]
        order_block = smc_state["order_block"]
        ob_high = smc_state["ob_high"]
        ob_low = smc_state["ob_low"]
        current_price = smc_state["price"]

        # Check SMC alignment
        if signal_type == "CALL":
            if order_block == "bullish":
                # Price should be inside or touching the bullish OB
                if ob_low > 0 and ob_high > 0:
                    if ob_low <= current_price <= ob_high:
                        entry["smc_confirmed"] = True
                        entry["state"] = self.STATE_SMC_PASSED
                        logger.info(f"Pipeline Stage 3 PASS: {symbol} CALL inside Bullish OB "
                                     f"[{ob_low:.5f}-{ob_high:.5f}]")
                        return True, f"Bullish OB confirmed [{ob_low:.5f}-{ob_high:.5f}]"
                    else:
                        # Allow if price is very close (within 10 pips)
                        distance = min(abs(current_price - ob_low), abs(current_price - ob_high))
                        if distance < 0.0010:  # 10 pips tolerance
                            entry["smc_confirmed"] = True
                            entry["state"] = self.STATE_SMC_PASSED
                            logger.info(f"Pipeline Stage 3 PASS: {symbol} CALL near Bullish OB")
                            return True, f"Near Bullish OB (distance: {distance:.5f})"
                else:
                    # OB exists but no range data
                    entry["smc_confirmed"] = True
                    entry["state"] = self.STATE_SMC_PASSED
                    return True, "Bullish OB present (no range data)"
            else:
                entry["state"] = self.STATE_REJECTED
                logger.info(f"Pipeline Stage 3 FAIL: {symbol} CALL but OB is {order_block}")
                return False, f"CALL signal but OB is {order_block}"

        elif signal_type == "PUT":
            if order_block == "bearish":
                if ob_low > 0 and ob_high > 0:
                    if ob_low <= current_price <= ob_high:
                        entry["smc_confirmed"] = True
                        entry["state"] = self.STATE_SMC_PASSED
                        logger.info(f"Pipeline Stage 3 PASS: {symbol} PUT inside Bearish OB "
                                     f"[{ob_low:.5f}-{ob_high:.5f}]")
                        return True, f"Bearish OB confirmed [{ob_low:.5f}-{ob_high:.5f}]"
                    else:
                        distance = min(abs(current_price - ob_low), abs(current_price - ob_high))
                        if distance < 0.0010:
                            entry["smc_confirmed"] = True
                            entry["state"] = self.STATE_SMC_PASSED
                            logger.info(f"Pipeline Stage 3 PASS: {symbol} PUT near Bearish OB")
                            return True, f"Near Bearish OB (distance: {distance:.5f})"
                else:
                    entry["smc_confirmed"] = True
                    entry["state"] = self.STATE_SMC_PASSED
                    return True, "Bearish OB present (no range data)"
            else:
                entry["state"] = self.STATE_REJECTED
                logger.info(f"Pipeline Stage 3 FAIL: {symbol} PUT but OB is {order_block}")
                return False, f"PUT signal but OB is {order_block}"

        return False, "Unknown signal type"

    # ----------------------------------------------------------
    # Wick Filter (applied between Stage 2 and 3)
    # ----------------------------------------------------------

    def check_wick_filter(self, symbol: str, candle_data: dict = None) -> Tuple[bool, str]:
        """
        Wick Filter: Reject if the signal candle's wick > 40% of its body.
        This filters out weak impulse signals.

        candle_data: {open, high, low, close} of the signal candle
        If not provided, the filter is skipped (passed).

        Returns: (passed: bool, reason: str)
        """
        entry = self._entries.get(symbol)
        if not entry:
            return False, "No pipeline entry"

        if not config.WICK_FILTER_ENABLED:
            entry["wick_passed"] = True
            return True, "Wick filter disabled"

        if candle_data is None:
            # No candle data available - pass by default
            entry["wick_passed"] = True
            return True, "No candle data (filter skipped)"

        o = candle_data.get("open", 0)
        h = candle_data.get("high", 0)
        l = candle_data.get("low", 0)
        c = candle_data.get("close", 0)

        body = abs(c - o)
        if body < 0.00001:
            # Doji candle - very small body, reject
            entry["state"] = self.STATE_REJECTED
            return False, "Doji candle (body too small)"

        signal_type = entry["signal"]

        if signal_type == "CALL":
            # For bullish signal, check lower wick
            wick = min(o, c) - l
        else:
            # For bearish signal, check upper wick
            wick = h - max(o, c)

        ratio = wick / body if body > 0 else 999

        if ratio > config.WICK_BODY_RATIO_MAX:
            entry["state"] = self.STATE_REJECTED
            logger.info(f"Wick Filter FAIL: {symbol} wick/body ratio = {ratio:.2f} > {config.WICK_BODY_RATIO_MAX}")
            return False, f"Wick too long (ratio: {ratio:.2f})"

        entry["wick_passed"] = True
        logger.info(f"Wick Filter PASS: {symbol} wick/body ratio = {ratio:.2f}")
        return True, f"Wick OK (ratio: {ratio:.2f})"

    # ----------------------------------------------------------
    # Mark READY (Stages 2+3 passed → send pre-alert)
    # ----------------------------------------------------------

    def mark_ready(self, symbol: str):
        """Mark signal as READY - triggers pre-alert message."""
        entry = self._entries.get(symbol)
        if entry:
            entry["state"] = self.STATE_READY
            logger.info(f"Pipeline: {symbol} marked READY for pre-alert")

    # ----------------------------------------------------------
    # Stage 4: Candle Close Confirmation
    # ----------------------------------------------------------

    def on_candle_close_confirmation(self, symbol: str, still_valid: bool,
                                      entry_price: float) -> Tuple[bool, str]:
        """
        Stage 4: At candle close (00:00:00), confirm the signal arrow is still present.
        Called by the precision timer at the exact candle boundary.

        Args:
            symbol: The pair symbol
            still_valid: Whether the GainzAlgo arrow is still showing
            entry_price: The price at the moment of candle close

        Returns: (confirmed: bool, reason: str)
        """
        entry = self._entries.get(symbol)
        if not entry:
            return False, "No pipeline entry"

        if not still_valid:
            entry["state"] = self.STATE_REJECTED
            logger.info(f"Pipeline Stage 4 FAIL: {symbol} arrow disappeared at candle close")
            return False, "Arrow disappeared at candle close"

        # Calculate entry and expiry times
        now_utc = datetime.now(timezone.utc)
        # Entry is at the start of the NEW candle (which just opened)
        entry_dt = now_utc.replace(second=0, microsecond=0)
        expiry_dt = entry_dt + timedelta(minutes=config.TRADE_DURATION)

        # Convert to UTC+3 for display
        utc3 = timezone(timedelta(hours=config.UTC_OFFSET))
        entry_utc3 = entry_dt.astimezone(utc3)

        entry["state"] = self.STATE_CONFIRMED
        entry["entry_time"] = entry_utc3.strftime("%H:%M")
        entry["entry_time_utc"] = entry_dt.strftime("%H:%M")
        entry["entry_datetime"] = entry_dt
        entry["entry_price"] = entry_price
        entry["expiry_datetime"] = expiry_dt

        logger.info(f"Pipeline Stage 4 PASS: {symbol} {entry['signal']} confirmed "
                     f"entry={entry['entry_time']} (UTC+3) price={entry_price:.5f}")

        return True, f"Confirmed at {entry['entry_time']} (UTC+3)"

    # ----------------------------------------------------------
    # Stage 5: Post-Trade Audit
    # ----------------------------------------------------------

    def resolve_trade(self, symbol: str, close_price: float) -> Optional[dict]:
        """
        Stage 5: Compare entry price with close price after 15 minutes.
        Uses 5 decimal place precision.

        Returns result dict or None.
        """
        entry = self._entries.get(symbol)
        if not entry:
            return None

        entry_price = entry["entry_price"]
        signal_type = entry["signal"]

        # Round to 5 decimal places for precision comparison
        entry_rounded = round(entry_price, config.PRICE_PRECISION)
        close_rounded = round(close_price, config.PRICE_PRECISION)

        if signal_type == "CALL":
            result = "WIN" if close_rounded > entry_rounded else "LOSS"
        else:
            result = "WIN" if close_rounded < entry_rounded else "LOSS"

        entry["state"] = self.STATE_COMPLETED
        entry["close_price"] = close_price
        entry["result"] = result

        logger.info(f"Pipeline Stage 5: {symbol} {signal_type} "
                     f"entry={entry_rounded:.5f} close={close_rounded:.5f} → {result}")

        # Mark signal as used in webhook handler
        self.wh.mark_signal_used(symbol)

        return {
            "symbol": symbol,
            "signal": signal_type,
            "entry_time": entry["entry_time"],
            "entry_price": entry_price,
            "close_price": close_price,
            "result": result,
        }

    # ----------------------------------------------------------
    # Query Methods
    # ----------------------------------------------------------

    def get_entry(self, symbol: str) -> Optional[dict]:
        """Get the current pipeline entry for a symbol."""
        return self._entries.get(symbol)

    def get_active_entries(self) -> list:
        """Get all non-completed, non-rejected entries."""
        return [
            e for e in self._entries.values()
            if e["state"] not in (self.STATE_COMPLETED, self.STATE_REJECTED)
        ]

    def has_active_trade(self) -> bool:
        """Check if any symbol has an active (non-completed) trade."""
        for e in self._entries.values():
            if e["state"] in (self.STATE_CONFIRMED, self.STATE_ACTIVE):
                return True
        return False

    def mark_active(self, symbol: str):
        """Mark a confirmed trade as actively running."""
        entry = self._entries.get(symbol)
        if entry:
            entry["state"] = self.STATE_ACTIVE

    def cleanup_completed(self):
        """Remove completed/rejected entries older than 30 minutes."""
        now = time.time()
        to_remove = []
        for symbol, entry in self._entries.items():
            if entry["state"] in (self.STATE_COMPLETED, self.STATE_REJECTED):
                age = now - entry.get("detected_ts", now)
                if age > 1800:  # 30 minutes
                    to_remove.append(symbol)
        for s in to_remove:
            del self._entries[s]
