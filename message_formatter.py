"""
Message Formatter - ABOOD "القناص" V1.0
=========================================
Three message types as specified in the requirements document:

أ. رسالة الاستعداد (Pre-Alert) - Stages 2+3 passed:
   》 ABOOD 15 M 《
   📊 {PAIR_NAME}
   ✅ {TYPE: CALL/PUT}
   ⌚ {ENTRY_TIME}
   ⏳ {REMAINING} minutos
   Win: {W} | Loss: {L} ({WR}%)
   Esse par: {PW}x{PL} ({PWR}%)

ب. رسالة التنفيذ (Action) - Stage 4 confirmed:
   abood Trading 15M POCKETOPTION BOT 🔵
   ✅ 🔜 {PAIR_NAME} {TIME} {⬆️/⬇️}

ج. رسالة الحصاد (Result) - Stage 5:
   WIN:  abood Trading 15M POCKETOPTION BOT 🔵 | ✅ {PAIR} {TIME} 💎 WIN
   LOSS: abood Trading 15M POCKETOPTION BOT 🔵 | ❌ {PAIR} {TIME} 💀 LOSS

All times displayed in UTC+3.
"""

from datetime import datetime, timezone, timedelta

import config

# UTC+3 timezone
UTC3 = timezone(timedelta(hours=config.UTC_OFFSET))

HEADER = config.BOT_DISPLAY_HEADER  # "abood Trading 15M POCKETOPTION BOT 🔵"


def _now_utc3():
    """Get current time in UTC+3."""
    return datetime.now(UTC3)


# ============================================================
# أ. رسالة الاستعداد (Pre-Alert)
# ============================================================

def format_pre_alert(symbol: str, signal_type: str, entry_time_utc3: str,
                     remaining_minutes: int,
                     wins: int, losses: int,
                     pair_wins: int, pair_losses: int) -> str:
    """
    Pre-Alert message sent when Stages 2+3 pass.
    entry_time_utc3: already in UTC+3 format "HH:MM"
    remaining_minutes: minutes until entry
    """
    total = wins + losses
    win_rate = round((wins / total * 100)) if total > 0 else 0
    pair_total = pair_wins + pair_losses
    pair_rate = round((pair_wins / pair_total * 100)) if pair_total > 0 else 0

    return (
        f"》 ABOOD 15 M 《\n\n"
        f"📊  {symbol}\n"
        f"✅  {signal_type}\n"
        f"⌚  {entry_time_utc3}\n"
        f"⏳  {remaining_minutes} minutos\n\n"
        f"Win: {wins} | Loss: {losses} ({win_rate}%)\n"
        f"Esse par: {pair_wins}x{pair_losses} ({pair_rate}%)"
    )


# ============================================================
# ب. رسالة التنفيذ (Action / Execution Confirmation)
# ============================================================

def format_execution(symbol: str, signal_type: str, entry_time_utc3: str) -> str:
    """
    Execution confirmation message sent at candle close (Stage 4).
    """
    direction = "⬆️" if signal_type == "CALL" else "⬇️"

    return (
        f"{HEADER}\n"
        f"✅ 🔜 {symbol} {entry_time_utc3} {direction}"
    )


# ============================================================
# ج. رسالة الحصاد (Result)
# ============================================================

def format_result(symbol: str, entry_time_utc3: str, result: str) -> str:
    """
    Result message sent after 15 minutes (Stage 5).
    WIN:  ✅ {PAIR} {TIME} 💎 WIN
    LOSS: ❌ {PAIR} {TIME} 💀 LOSS
    """
    if result == "WIN":
        return (
            f"{HEADER}\n"
            f"✅ {symbol} {entry_time_utc3} 💎 WIN"
        )
    else:
        return (
            f"{HEADER}\n"
            f"❌ {symbol} {entry_time_utc3} 💀 LOSS"
        )


# ============================================================
# Utility Messages
# ============================================================

def format_stats_message(wins: int, losses: int, win_rate: float, pairs=None) -> str:
    """Daily stats message."""
    total = wins + losses
    lines = [
        "📊 إحصائيات اليوم\n",
        f"✅ Win: {wins}",
        f"❌ Loss: {losses}",
        f"📈 Win Rate: {win_rate}%",
        f"🔢 Total: {total}",
    ]
    if pairs:
        lines.append("\n📋 تفصيل الأزواج:")
        for p in pairs:
            lines.append(f"  {p['symbol']}: {p['wins']}W / {p['losses']}L ({p['win_rate']}%)")
    return "\n".join(lines)


def format_overall_stats(stats: dict) -> str:
    """Overall cumulative stats."""
    return (
        f"🧮 الإحصائيات التراكمية\n\n"
        f"🔢 Total: {stats['total']}\n"
        f"✅ Win: {stats['wins']}\n"
        f"❌ Loss: {stats['losses']}\n"
        f"📈 Win Rate: {stats['win_rate']}%"
    )


def format_startup_message() -> str:
    """Bot startup message."""
    now = _now_utc3()
    time_str = now.strftime("%Y-%m-%d %H:%M:%S")
    pairs = " | ".join(config.TRADING_PAIRS)

    return (
        f"📈 》 ABOOD القناص 《 V1.0 📈\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"⏰ {time_str} (UTC+3)\n\n"
        f"✅ GainzAlgo V2 Webhook: جاهز\n"
        f"✅ LuxAlgo SMC Filter: {'مفعّل' if config.SMC_FILTER_ENABLED else 'معطّل'}\n"
        f"✅ 120s Stability Rule: مفعّل\n"
        f"✅ Wick Filter: {'مفعّل' if config.WICK_FILTER_ENABLED else 'معطّل'}\n"
        f"✅ قاعدة البيانات: متصلة\n\n"
        f"📊 الأزواج: {pairs}\n"
        f"⏱ مدة الصفقات: 15 دقيقة (Fixed Time)\n"
        f"🎯 الدخول: عند إغلاق الشمعة (00:00:00)\n"
        f"🔍 Pipeline: 5 مراحل (Detection → 120s → SMC → Confirm → Result)\n"
        f"🕐 ساعات العمل: 3:00 AM - 11:00 PM (UTC+3)\n"
        f"📅 أيام العمل: الاثنين - الجمعة\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 الأوامر:\n"
        f"/stats - إحصائيات اليوم\n"
        f"/overall - الإحصائيات التراكمية\n"
        f"/recent - آخر 10 إشارات\n"
        f"/pipeline - حالة الـ Pipeline\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━"
    )
