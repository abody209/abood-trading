"""
Signal Generator - STRONG 15-Minute Signals Only
Uses: Bollinger Bands + RSI + EMA + Stochastic + ADX + Candle Patterns + Momentum
STRICT: Only sends signals when multiple indicators confirm the same direction.
Entry is always at the start of the next 15-minute candle (:00, :15, :30, :45).
"""

import logging
import time
import math
import pandas as pd
import numpy as np
import yfinance as yf
from ta.volatility import BollingerBands
from ta.momentum import RSIIndicator, StochasticOscillator
from ta.trend import EMAIndicator, ADXIndicator
from datetime import datetime, timedelta, timezone

import config

logger = logging.getLogger(__name__)


def _next_candle_time():
    """
    Calculate the next 15-minute candle boundary (UTC).
    Candle boundaries: :00, :15, :30, :45
    Returns (entry_dt, minutes_until_entry).
    """
    now = datetime.now(timezone.utc)
    minute = now.minute
    interval = config.CANDLE_INTERVAL  # 15

    # Next candle boundary
    next_boundary = (math.floor(minute / interval) + 1) * interval
    if next_boundary >= 60:
        entry_dt = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    else:
        entry_dt = now.replace(minute=next_boundary, second=0, microsecond=0)

    minutes_until = (entry_dt - now).total_seconds() / 60
    return entry_dt, minutes_until


class SignalGenerator:
    def __init__(self):
        self.last_signal_times = {}

    def fetch_data(self, yf_symbol):
        """Fetch market data with retry logic."""
        for attempt in range(3):
            try:
                data = yf.download(
                    yf_symbol,
                    period="5d",
                    interval=config.TIMEFRAME,
                    progress=False,
                    auto_adjust=False,
                    threads=False,
                )
                if data.empty:
                    raise ValueError(f"No data for {yf_symbol}")

                if isinstance(data.columns, pd.MultiIndex):
                    data.columns = data.columns.get_level_values(0)

                return data.copy()
            except Exception as e:
                logger.warning(f"Fetch attempt {attempt+1}/3 failed for {yf_symbol}: {e}")
                time.sleep(2)
        return None

    def add_indicators(self, df):
        """Calculate all enabled indicators on the dataframe."""
        close = df["Close"]
        high = df["High"]
        low = df["Low"]

        if config.ENABLE_BOLLINGER:
            bb = BollingerBands(close=close, window=config.BB_PERIOD, window_dev=config.BB_STD)
            df["BB_Upper"] = bb.bollinger_hband()
            df["BB_Lower"] = bb.bollinger_lband()
            df["BB_Mid"] = bb.bollinger_mavg()

        if config.ENABLE_RSI:
            df["RSI"] = RSIIndicator(close=close, window=config.RSI_PERIOD).rsi()

        if config.ENABLE_EMA:
            df["EMA_Fast"] = EMAIndicator(close=close, window=config.EMA_FAST).ema_indicator()
            df["EMA_Slow"] = EMAIndicator(close=close, window=config.EMA_SLOW).ema_indicator()

        if config.ENABLE_STOCHASTIC:
            stoch = StochasticOscillator(
                high=high, low=low, close=close,
                window=config.STOCH_K, smooth_window=config.STOCH_SMOOTH,
            )
            df["Stoch_K"] = stoch.stoch()
            df["Stoch_D"] = stoch.stoch_signal()

        if config.ENABLE_ADX:
            adx = ADXIndicator(high=high, low=low, close=close, window=config.ADX_PERIOD)
            df["ADX"] = adx.adx()
            df["DI_Plus"] = adx.adx_pos()
            df["DI_Minus"] = adx.adx_neg()

        # Momentum: Rate of Change (3 candles)
        if config.ENABLE_MOMENTUM:
            df["ROC"] = close.pct_change(3) * 100

        return df.dropna().copy()

    def _detect_candle_patterns(self, curr, prev, prev2):
        """Detect simple candle patterns for extra scoring."""
        call_score = 0.0
        put_score = 0.0
        call_reasons = []
        put_reasons = []

        body_curr = curr["Close"] - curr["Open"]
        body_prev = prev["Close"] - prev["Open"]
        range_curr = curr["High"] - curr["Low"]

        # Bullish engulfing
        if body_prev < 0 and body_curr > 0 and abs(body_curr) > abs(body_prev) * 1.2:
            call_score += 0.8
            call_reasons.append("Bullish engulfing")

        # Bearish engulfing
        if body_prev > 0 and body_curr < 0 and abs(body_curr) > abs(body_prev) * 1.2:
            put_score += 0.8
            put_reasons.append("Bearish engulfing")

        # Hammer (bullish): small body at top, long lower wick
        if range_curr > 0:
            lower_wick = min(curr["Open"], curr["Close"]) - curr["Low"]
            upper_wick = curr["High"] - max(curr["Open"], curr["Close"])
            if lower_wick > abs(body_curr) * 2 and upper_wick < abs(body_curr) * 0.5:
                call_score += 0.6
                call_reasons.append("Hammer candle")

        # Shooting star (bearish): small body at bottom, long upper wick
        if range_curr > 0:
            lower_wick = min(curr["Open"], curr["Close"]) - curr["Low"]
            upper_wick = curr["High"] - max(curr["Open"], curr["Close"])
            if upper_wick > abs(body_curr) * 2 and lower_wick < abs(body_curr) * 0.5:
                put_score += 0.6
                put_reasons.append("Shooting star")

        # Three consecutive bullish/bearish candles
        body_prev2 = prev2["Close"] - prev2["Open"]
        if body_prev2 > 0 and body_prev > 0 and body_curr > 0:
            call_score += 0.4
            call_reasons.append("3 bullish candles")
        if body_prev2 < 0 and body_prev < 0 and body_curr < 0:
            put_score += 0.4
            put_reasons.append("3 bearish candles")

        return call_score, put_score, call_reasons, put_reasons

    def evaluate(self, df, display_name):
        """STRICT score-based signal evaluation - only strong signals pass."""
        if len(df) < 12:
            return None

        curr = df.iloc[-2]   # last completed candle
        prev = df.iloc[-3]
        prev2 = df.iloc[-4]

        call_score = 0.0
        put_score = 0.0
        call_reasons = []
        put_reasons = []
        call_indicators = 0  # count of confirming indicators
        put_indicators = 0

        # === BOLLINGER BANDS ===
        if config.ENABLE_BOLLINGER and "BB_Lower" in df.columns:
            bb_width = curr["BB_Upper"] - curr["BB_Lower"]
            if bb_width > 0:
                dist_lower = (curr["Close"] - curr["BB_Lower"]) / bb_width
                dist_upper = (curr["BB_Upper"] - curr["Close"]) / bb_width

                # Touch or break = strong
                if curr["Close"] <= curr["BB_Lower"]:
                    call_score += 1.2
                    call_reasons.append("BB Lower touch")
                    call_indicators += 1
                elif dist_lower < 0.15:
                    call_score += 0.7
                    call_reasons.append("Near BB Lower")
                    call_indicators += 1

                if curr["Close"] >= curr["BB_Upper"]:
                    put_score += 1.2
                    put_reasons.append("BB Upper touch")
                    put_indicators += 1
                elif dist_upper < 0.15:
                    put_score += 0.7
                    put_reasons.append("Near BB Upper")
                    put_indicators += 1

        # === RSI ===
        if config.ENABLE_RSI and "RSI" in df.columns:
            rsi = curr["RSI"]
            if rsi <= config.RSI_OVERSOLD:
                call_score += 1.2
                call_reasons.append(f"RSI oversold ({rsi:.0f})")
                call_indicators += 1
            elif rsi <= 38:
                call_score += 0.6
                call_reasons.append(f"RSI low ({rsi:.0f})")
                call_indicators += 1

            if rsi >= config.RSI_OVERBOUGHT:
                put_score += 1.2
                put_reasons.append(f"RSI overbought ({rsi:.0f})")
                put_indicators += 1
            elif rsi >= 62:
                put_score += 0.6
                put_reasons.append(f"RSI high ({rsi:.0f})")
                put_indicators += 1

        # === EMA CROSSOVER ===
        if config.ENABLE_EMA and "EMA_Fast" in df.columns:
            if curr["EMA_Fast"] > curr["EMA_Slow"] and prev["EMA_Fast"] <= prev["EMA_Slow"]:
                call_score += 1.5
                call_reasons.append("EMA bullish cross")
                call_indicators += 1
            elif curr["EMA_Fast"] > curr["EMA_Slow"]:
                ema_gap = (curr["EMA_Fast"] - curr["EMA_Slow"]) / curr["EMA_Slow"] * 100
                if ema_gap > 0.02:
                    call_score += 0.5
                    call_reasons.append("EMA bullish trend")
                    call_indicators += 1

            if curr["EMA_Fast"] < curr["EMA_Slow"] and prev["EMA_Fast"] >= prev["EMA_Slow"]:
                put_score += 1.5
                put_reasons.append("EMA bearish cross")
                put_indicators += 1
            elif curr["EMA_Fast"] < curr["EMA_Slow"]:
                ema_gap = (curr["EMA_Slow"] - curr["EMA_Fast"]) / curr["EMA_Slow"] * 100
                if ema_gap > 0.02:
                    put_score += 0.5
                    put_reasons.append("EMA bearish trend")
                    put_indicators += 1

        # === STOCHASTIC ===
        if config.ENABLE_STOCHASTIC and "Stoch_K" in df.columns:
            sk = curr["Stoch_K"]
            sd = curr["Stoch_D"]
            sk_prev = prev["Stoch_K"]
            sd_prev = prev["Stoch_D"]

            if sk <= config.STOCH_OVERSOLD:
                if sk > sd and sk_prev <= sd_prev:
                    call_score += 1.2
                    call_reasons.append(f"Stoch oversold cross ({sk:.0f})")
                    call_indicators += 1
                else:
                    call_score += 0.6
                    call_reasons.append(f"Stoch oversold ({sk:.0f})")
                    call_indicators += 1

            if sk >= config.STOCH_OVERBOUGHT:
                if sk < sd and sk_prev >= sd_prev:
                    put_score += 1.2
                    put_reasons.append(f"Stoch overbought cross ({sk:.0f})")
                    put_indicators += 1
                else:
                    put_score += 0.6
                    put_reasons.append(f"Stoch overbought ({sk:.0f})")
                    put_indicators += 1

        # === ADX (Trend Strength) ===
        if config.ENABLE_ADX and "ADX" in df.columns:
            adx_val = curr["ADX"]
            if adx_val >= config.ADX_THRESHOLD:
                # Strong trend - add to the trending direction
                if curr["DI_Plus"] > curr["DI_Minus"]:
                    call_score += 0.6
                    call_reasons.append(f"ADX strong trend bullish ({adx_val:.0f})")
                    call_indicators += 1
                else:
                    put_score += 0.6
                    put_reasons.append(f"ADX strong trend bearish ({adx_val:.0f})")
                    put_indicators += 1
            elif adx_val < 20:
                # Weak trend = good for reversal signals
                if call_score > put_score:
                    call_score += 0.3
                    call_reasons.append("ADX weak (reversal zone)")
                if put_score > call_score:
                    put_score += 0.3
                    put_reasons.append("ADX weak (reversal zone)")

        # === CANDLE PATTERNS ===
        if config.ENABLE_CANDLE_PATTERNS:
            cp_call, cp_put, cp_call_r, cp_put_r = self._detect_candle_patterns(curr, prev, prev2)
            call_score += cp_call
            put_score += cp_put
            call_reasons.extend(cp_call_r)
            put_reasons.extend(cp_put_r)
            if cp_call > 0:
                call_indicators += 1
            if cp_put > 0:
                put_indicators += 1

        # === MOMENTUM (ROC) ===
        if config.ENABLE_MOMENTUM and "ROC" in df.columns:
            roc = curr["ROC"]
            if roc > 0.08:
                call_score += 0.4
                call_reasons.append(f"Strong momentum up ({roc:.2f}%)")
                call_indicators += 1
            elif roc < -0.08:
                put_score += 0.4
                put_reasons.append(f"Strong momentum down ({roc:.2f}%)")
                put_indicators += 1

        # ============================================================
        # STRICT FILTERS - Only strong, reliable signals pass
        # ============================================================

        # Filter 1: Conflict filter - signals must be clearly one-directional
        score_diff = abs(call_score - put_score)
        if score_diff < config.CONFLICT_THRESHOLD:
            logger.debug(f"{display_name}: Conflict (CALL={call_score:.1f} PUT={put_score:.1f})")
            return None

        # Filter 2: Minimum score (raised to 3.5 for strong signals)
        min_score = config.MIN_SIGNAL_SCORE

        # Filter 3: Minimum confirming indicators (at least 3 must agree)
        min_indicators = config.MIN_CONFIRMING_INDICATORS

        # Decision - only if BOTH score AND indicator count pass
        if call_score >= min_score and call_score > put_score and call_indicators >= min_indicators:
            return self._build_signal(display_name, "CALL", call_score, call_reasons, curr, call_indicators)
        elif put_score >= min_score and put_score > call_score and put_indicators >= min_indicators:
            return self._build_signal(display_name, "PUT", put_score, put_reasons, curr, put_indicators)

        return None

    def _build_signal(self, display_name, direction, score, reasons, candle, indicators_count):
        """Build signal - entry at next 15-min candle boundary."""
        entry_dt, minutes_until = _next_candle_time()
        duration = config.TRADE_DURATION  # Fixed 15 minutes

        return {
            "symbol": display_name,
            "type": direction,
            "score": round(score, 1),
            "reasons": " | ".join(reasons),
            "entry_time": entry_dt.strftime("%H:%M"),
            "entry_datetime": entry_dt,
            "entry_price": float(candle["Close"]),
            "duration": str(duration),
            "minutes_until_entry": round(minutes_until, 1),
            "indicators_count": indicators_count,
            "strength": "Strong" if score >= 4.5 else ("Good" if score >= 3.5 else "Normal"),
        }

    def _is_on_cooldown(self, display_name):
        last = self.last_signal_times.get(display_name)
        if not last:
            return False
        elapsed = (datetime.now(timezone.utc) - last).total_seconds() / 60
        return elapsed < config.MIN_SIGNAL_INTERVAL

    def check_all_pairs(self):
        """Scan all configured pairs and return list of signals."""
        # First check: is the next candle 2-5 minutes away?
        # Only generate signals when we're in the sweet spot before a new candle
        _, minutes_until = _next_candle_time()
        if minutes_until > 5 or minutes_until < 1:
            # Not in the right window (2-5 min before candle)
            # Allow 1 min minimum to ensure message arrives before entry
            logger.debug(f"Not in signal window: {minutes_until:.1f} min until next candle")
            return []

        signals = []
        for yf_symbol, display_name in config.TRADING_PAIRS:
            if self._is_on_cooldown(display_name):
                continue
            try:
                df = self.fetch_data(yf_symbol)
                if df is None or len(df) < 30:
                    continue
                df = self.add_indicators(df)
                signal = self.evaluate(df, display_name)
                if signal:
                    self.last_signal_times[display_name] = datetime.now(timezone.utc)
                    signals.append(signal)
            except Exception as e:
                logger.error(f"Error scanning {display_name}: {e}")
        return signals
