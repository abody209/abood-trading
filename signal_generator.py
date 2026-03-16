"""
Signal Generator with Balanced Scoring System
Uses: Bollinger Bands + RSI + EMA + Stochastic + ADX + Candle Patterns + Momentum
Balanced: enough signals (every 15-30 min) with good accuracy.
ONE TRADE AT A TIME: bot.py handles the lock, this just returns best signal.
"""

import logging
import time
import pandas as pd
import numpy as np
import yfinance as yf
from ta.volatility import BollingerBands
from ta.momentum import RSIIndicator, StochasticOscillator
from ta.trend import EMAIndicator, ADXIndicator
from datetime import datetime, timedelta, timezone

import config

logger = logging.getLogger(__name__)


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
        """Balanced score-based signal evaluation."""
        if len(df) < 12:
            return None

        curr = df.iloc[-2]   # last completed candle
        prev = df.iloc[-3]
        prev2 = df.iloc[-4]

        call_score = 0.0
        put_score = 0.0
        call_reasons = []
        put_reasons = []

        # === BOLLINGER BANDS ===
        if config.ENABLE_BOLLINGER and "BB_Lower" in df.columns:
            bb_width = curr["BB_Upper"] - curr["BB_Lower"]
            if bb_width > 0:
                dist_lower = (curr["Close"] - curr["BB_Lower"]) / bb_width
                dist_upper = (curr["BB_Upper"] - curr["Close"]) / bb_width

                # Touch or break = strong
                if curr["Close"] <= curr["BB_Lower"]:
                    call_score += 1.0
                    call_reasons.append("BB Lower touch")
                elif dist_lower < 0.20:
                    call_score += 0.6
                    call_reasons.append("Near BB Lower")
                elif dist_lower < 0.35:
                    call_score += 0.3
                    call_reasons.append("Approaching BB Lower")

                if curr["Close"] >= curr["BB_Upper"]:
                    put_score += 1.0
                    put_reasons.append("BB Upper touch")
                elif dist_upper < 0.20:
                    put_score += 0.6
                    put_reasons.append("Near BB Upper")
                elif dist_upper < 0.35:
                    put_score += 0.3
                    put_reasons.append("Approaching BB Upper")

        # === RSI ===
        if config.ENABLE_RSI and "RSI" in df.columns:
            rsi = curr["RSI"]
            if rsi <= config.RSI_OVERSOLD:
                call_score += 1.0
                call_reasons.append(f"RSI oversold ({rsi:.0f})")
            elif rsi <= 42:
                call_score += 0.5
                call_reasons.append(f"RSI low ({rsi:.0f})")
            elif rsi <= 48:
                call_score += 0.2
                call_reasons.append(f"RSI below mid ({rsi:.0f})")

            if rsi >= config.RSI_OVERBOUGHT:
                put_score += 1.0
                put_reasons.append(f"RSI overbought ({rsi:.0f})")
            elif rsi >= 58:
                put_score += 0.5
                put_reasons.append(f"RSI high ({rsi:.0f})")
            elif rsi >= 52:
                put_score += 0.2
                put_reasons.append(f"RSI above mid ({rsi:.0f})")

        # === EMA CROSSOVER ===
        if config.ENABLE_EMA and "EMA_Fast" in df.columns:
            if curr["EMA_Fast"] > curr["EMA_Slow"] and prev["EMA_Fast"] <= prev["EMA_Slow"]:
                call_score += 1.5
                call_reasons.append("EMA bullish cross")
            elif curr["EMA_Fast"] > curr["EMA_Slow"]:
                call_score += 0.4
                call_reasons.append("EMA bullish")

            if curr["EMA_Fast"] < curr["EMA_Slow"] and prev["EMA_Fast"] >= prev["EMA_Slow"]:
                put_score += 1.5
                put_reasons.append("EMA bearish cross")
            elif curr["EMA_Fast"] < curr["EMA_Slow"]:
                put_score += 0.4
                put_reasons.append("EMA bearish")

        # === STOCHASTIC ===
        if config.ENABLE_STOCHASTIC and "Stoch_K" in df.columns:
            sk = curr["Stoch_K"]
            sd = curr["Stoch_D"]
            sk_prev = prev["Stoch_K"]
            sd_prev = prev["Stoch_D"]

            if sk <= config.STOCH_OVERSOLD:
                if sk > sd and sk_prev <= sd_prev:
                    call_score += 1.0
                    call_reasons.append(f"Stoch oversold cross ({sk:.0f})")
                else:
                    call_score += 0.5
                    call_reasons.append(f"Stoch oversold ({sk:.0f})")
            elif sk <= 35:
                call_score += 0.3
                call_reasons.append(f"Stoch low ({sk:.0f})")

            if sk >= config.STOCH_OVERBOUGHT:
                if sk < sd and sk_prev >= sd_prev:
                    put_score += 1.0
                    put_reasons.append(f"Stoch overbought cross ({sk:.0f})")
                else:
                    put_score += 0.5
                    put_reasons.append(f"Stoch overbought ({sk:.0f})")
            elif sk >= 65:
                put_score += 0.3
                put_reasons.append(f"Stoch high ({sk:.0f})")

        # === ADX ===
        if config.ENABLE_ADX and "ADX" in df.columns:
            adx_val = curr["ADX"]
            if adx_val < config.ADX_THRESHOLD:
                if call_score > 0:
                    call_score += 0.4
                    call_reasons.append("ADX low (reversal)")
                if put_score > 0:
                    put_score += 0.4
                    put_reasons.append("ADX low (reversal)")
            else:
                if curr["DI_Plus"] > curr["DI_Minus"]:
                    call_score += 0.5
                    call_reasons.append(f"ADX trend bullish ({adx_val:.0f})")
                else:
                    put_score += 0.5
                    put_reasons.append(f"ADX trend bearish ({adx_val:.0f})")

        # === CANDLE PATTERNS ===
        if config.ENABLE_CANDLE_PATTERNS:
            cp_call, cp_put, cp_call_r, cp_put_r = self._detect_candle_patterns(curr, prev, prev2)
            call_score += cp_call
            put_score += cp_put
            call_reasons.extend(cp_call_r)
            put_reasons.extend(cp_put_r)

        # === MOMENTUM (ROC) ===
        if config.ENABLE_MOMENTUM and "ROC" in df.columns:
            roc = curr["ROC"]
            if roc > 0.05:
                call_score += 0.3
                call_reasons.append(f"Momentum up ({roc:.2f}%)")
            elif roc < -0.05:
                put_score += 0.3
                put_reasons.append(f"Momentum down ({roc:.2f}%)")

        # ============================================================
        # FILTERS
        # ============================================================

        # Filter 1: Conflict filter (relaxed)
        score_diff = abs(call_score - put_score)
        if score_diff < config.CONFLICT_THRESHOLD:
            logger.debug(f"{display_name}: Conflict (CALL={call_score:.1f} PUT={put_score:.1f})")
            return None

        # Filter 2: Minimum score
        min_score = config.MIN_SIGNAL_SCORE

        # Decision
        if call_score >= min_score and call_score > put_score:
            return self._build_signal(display_name, "CALL", call_score, call_reasons, curr)
        elif put_score >= min_score and put_score > call_score:
            return self._build_signal(display_name, "PUT", put_score, put_reasons, curr)

        return None

    def _get_dynamic_duration(self, score):
        """Choose trade duration based on signal strength."""
        if score >= 4.0:
            return config.DURATION_STRONG   # 15 min
        elif score >= 3.0:
            return config.DURATION_GOOD     # 10 min
        else:
            return config.DURATION_NORMAL   # 5 min

    def _build_signal(self, display_name, direction, score, reasons, candle):
        now = datetime.now(timezone.utc)
        alert_time = now
        entry_time = now + timedelta(minutes=config.PRE_ALERT_MINUTES)
        duration = self._get_dynamic_duration(score)

        return {
            "symbol": display_name,
            "type": direction,
            "score": round(score, 1),
            "reasons": " | ".join(reasons),
            "alert_time": alert_time.strftime("%H:%M"),
            "entry_time": entry_time.strftime("%H:%M"),
            "entry_datetime": entry_time,
            "entry_price": float(candle["Close"]),
            "duration": str(duration),
            "strength": "Strong" if score >= 4.0 else ("Good" if score >= 3.0 else "Normal"),
        }

    def _is_on_cooldown(self, display_name):
        last = self.last_signal_times.get(display_name)
        if not last:
            return False
        elapsed = (datetime.now(timezone.utc) - last).total_seconds() / 60
        return elapsed < config.MIN_SIGNAL_INTERVAL

    def check_all_pairs(self):
        """Scan all configured pairs and return list of signals."""
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
