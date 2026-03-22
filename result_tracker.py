"""
Result Tracker - ABOOD القناص V1.0
====================================
Fetches live prices from Yahoo Finance.
Resolves pending signals as a fallback safety net.
Primary result checking is done by the PrecisionTimer in bot.py.
"""

import logging
import time
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta, timezone

import config
from database import get_pending_signals, update_signal_result, update_daily_stats

logger = logging.getLogger(__name__)


class ResultTracker:
    def __init__(self):
        pass

    @staticmethod
    def _yf_symbol(display_name: str) -> str:
        """Convert display name to Yahoo Finance symbol."""
        return config.YF_SYMBOL_MAP.get(display_name, f"{display_name}=X")

    def get_current_price(self, display_name: str) -> float:
        """
        Get the current live price for a pair.
        Uses 1-minute data for precision.
        Returns float price or None on failure.
        """
        yf_sym = self._yf_symbol(display_name)
        for attempt in range(3):
            try:
                data = yf.download(yf_sym, period="1d", interval="1m",
                                   progress=False, auto_adjust=False, threads=False)
                if data.empty:
                    raise ValueError("empty data")
                if isinstance(data.columns, pd.MultiIndex):
                    data.columns = data.columns.get_level_values(0)
                price = float(data.iloc[-1]["Close"])
                return round(price, config.PRICE_PRECISION)
            except Exception as e:
                logger.warning(f"Price fetch attempt {attempt+1}/3 for {display_name}: {e}")
                time.sleep(2)
        return None

    def get_price_at_time(self, display_name: str, target_dt_str: str) -> float:
        """
        Get the price at a specific time.
        Used for historical price lookups.
        """
        yf_sym = self._yf_symbol(display_name)
        try:
            target_dt = datetime.fromisoformat(target_dt_str)
            if target_dt.tzinfo is None:
                target_dt = target_dt.replace(tzinfo=timezone.utc)

            start = target_dt - timedelta(minutes=5)
            end = target_dt + timedelta(minutes=10)

            data = yf.download(
                yf_sym,
                start=start.strftime("%Y-%m-%d %H:%M:%S"),
                end=end.strftime("%Y-%m-%d %H:%M:%S"),
                interval="1m", progress=False,
                auto_adjust=False, threads=False,
            )
            if data.empty:
                return None
            if isinstance(data.columns, pd.MultiIndex):
                data.columns = data.columns.get_level_values(0)

            data.index = pd.to_datetime(data.index)
            after = data[data.index >= target_dt]
            if not after.empty:
                return round(float(after.iloc[0]["Close"]), config.PRICE_PRECISION)
            return round(float(data.iloc[-1]["Close"]), config.PRICE_PRECISION)
        except Exception as e:
            logger.error(f"Error fetching price at {target_dt_str}: {e}")
            return None

    def check_and_resolve_pending(self) -> list:
        """
        Fallback: Check all pending signals and resolve any that are past expiry.
        This is a safety net - primary checking is done by PrecisionTimer.
        """
        pending = get_pending_signals()
        resolved = []
        now = datetime.now(timezone.utc)

        for sig in pending:
            expiry_dt = datetime.fromisoformat(sig["expiry_datetime"])
            if expiry_dt.tzinfo is None:
                expiry_dt = expiry_dt.replace(tzinfo=timezone.utc)

            # Only check if past expiry + 1 minute buffer
            if now < expiry_dt + timedelta(minutes=1):
                continue

            close_price = self.get_price_at_time(sig["symbol"], sig["expiry_datetime"])
            if close_price is None:
                close_price = self.get_current_price(sig["symbol"])
            if close_price is None:
                continue

            entry_price = round(float(sig["entry_price"]), config.PRICE_PRECISION)

            if sig["signal_type"] == "CALL":
                result = "WIN" if close_price > entry_price else "LOSS"
            else:
                result = "WIN" if close_price < entry_price else "LOSS"

            update_signal_result(sig["id"], close_price, result)
            update_daily_stats(sig["symbol"])

            resolved.append({
                "id": sig["id"],
                "symbol": sig["symbol"],
                "signal_type": sig["signal_type"],
                "entry_time": sig["entry_time"],
                "entry_price": entry_price,
                "close_price": close_price,
                "result": result,
            })

            logger.info(f"Fallback resolved: {sig['symbol']} {sig['signal_type']} → {result}")

        return resolved
