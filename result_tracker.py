"""
Result Tracker - Checks pending signals and resolves WIN/LOSS.
Supports multiple pairs.
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
        # Build reverse lookup: display_name -> yf_symbol
        self.pair_map = {display: yf for yf, display in config.TRADING_PAIRS}

    def _yf_symbol(self, display_name):
        return self.pair_map.get(display_name, f"{display_name}=X")

    def get_current_price(self, display_name):
        yf_sym = self._yf_symbol(display_name)
        for attempt in range(3):
            try:
                data = yf.download(yf_sym, period="1d", interval="1m", progress=False)
                if data.empty:
                    raise ValueError("empty data")
                if isinstance(data.columns, pd.MultiIndex):
                    data.columns = data.columns.get_level_values(0)
                return float(data.iloc[-1]["Close"])
            except Exception as e:
                logger.warning(f"Price fetch attempt {attempt+1}/3 for {display_name}: {e}")
                time.sleep(2)
        return None

    def get_price_at_time(self, display_name, target_dt_str):
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
            )
            if data.empty:
                return None
            if isinstance(data.columns, pd.MultiIndex):
                data.columns = data.columns.get_level_values(0)

            data.index = pd.to_datetime(data.index)
            after = data[data.index >= target_dt]
            if not after.empty:
                return float(after.iloc[0]["Close"])
            return float(data.iloc[-1]["Close"])
        except Exception as e:
            logger.error(f"Error fetching price at {target_dt_str}: {e}")
            return None

    def check_and_resolve_pending(self):
        pending = get_pending_signals()
        resolved = []
        now = datetime.now(timezone.utc)

        for sig in pending:
            expiry_dt = datetime.fromisoformat(sig["expiry_datetime"])
            if expiry_dt.tzinfo is None:
                expiry_dt = expiry_dt.replace(tzinfo=timezone.utc)

            if now < expiry_dt + timedelta(minutes=1):
                continue

            close_price = self.get_price_at_time(sig["symbol"], sig["expiry_datetime"])
            if close_price is None:
                close_price = self.get_current_price(sig["symbol"])
            if close_price is None:
                continue

            entry_price = float(sig["entry_price"])
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

        return resolved
