"""
Message Formatter - Professional Telegram messages for signals, results, and stats.
"""


def format_signal_message(symbol, signal_type, entry_time, duration,
                          wins, losses, pair_wins, pair_losses,
                          score=0, reasons="", entry_price=None):
    total = wins + losses
    win_rate = round((wins / total * 100)) if total > 0 else 0
    pair_total = pair_wins + pair_losses
    pair_rate = round((pair_wins / pair_total * 100)) if pair_total > 0 else 0

    emoji = "🟢" if signal_type == "CALL" else "🔴"

    price_line = f"💰 Entry: {entry_price}\n" if entry_price else ""
    score_line = f"⚡ Score: {score}\n" if score else ""
    reasons_line = f"📋 {reasons}\n" if reasons else ""

    return (
        f"》 JOKER 15 M 《\n\n"
        f"🏁 {symbol}\n"
        f"{emoji} {signal_type}\n"
        f"🕐 {entry_time}\n"
        f"⏳ {duration} min\n"
        f"{price_line}"
        f"{score_line}"
        f"{reasons_line}\n"
        f"📊 Win: {wins} | Loss: {losses} ({win_rate}%)\n"
        f"📈 {symbol}: {pair_wins}x{pair_losses} ({pair_rate}%)"
    )


def format_result_message(symbol, entry_time, signal_type, result,
                          entry_price=None, close_price=None):
    dir_emoji = "⬆️" if signal_type == "CALL" else "⬇️"
    res_emoji = "✅" if result == "WIN" else "❌"

    msg = f"{res_emoji} → {symbol} {entry_time} {dir_emoji}"
    if entry_price and close_price:
        msg += f"\n💰 {entry_price} → {close_price}"
    return msg


def format_stats_message(wins, losses, win_rate, pairs=None):
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


def format_overall_stats(stats):
    return (
        f"🧮 الإحصائيات التراكمية\n\n"
        f"🔢 Total: {stats['total']}\n"
        f"✅ Win: {stats['wins']}\n"
        f"❌ Loss: {stats['losses']}\n"
        f"📈 Win Rate: {stats['win_rate']}%"
    )


def format_startup_message():
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone(timedelta(hours=3)))
    time_str = now.strftime("%Y-%m-%d %H:%M:%S")

    return (
        "🃏 》 JOKER Trading Bot 《 🃏\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"⏰ {time_str} (UTC+3)\n\n"
        "✅ محرك الإشارات: نشط\n"
        "✅ تعقب النتائج: نشط\n"
        "✅ قاعدة البيانات: متصلة\n"
        "✅ Webhook: جاهز\n\n"
        "📊 الأزواج: EURUSD | GBPUSD | USDJPY | AUDUSD\n"
        "🕐 ساعات العمل: 3:00 AM - 11:00 PM\n"
        "📅 أيام العمل: الاثنين - الجمعة\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📌 الأوامر المتاحة:\n"
        "/stats - إحصائيات اليوم\n"
        "/overall - الإحصائيات التراكمية\n"
        "/recent - آخر 10 إشارات\n"
        "━━━━━━━━━━━━━━━━━━━━━━━"
    )


def format_pre_alert_message(symbol, signal_type, alert_time, entry_time,
                             duration, score=0, reasons="", entry_price=None,
                             strength="Normal"):
    """Pre-alert message sent before entry time."""
    emoji = "🟢" if signal_type == "CALL" else "🔴"
    strength_emoji = "🔥" if strength == "Strong" else ("⚡" if strength == "Good" else "📊")
    price_line = f"💰 السعر الحالي: {entry_price}\n" if entry_price else ""
    reasons_line = f"📋 {reasons}\n" if reasons else ""

    return (
        f"🃏 》 JOKER Trading Bot 《 🃏\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ تنبيه مسبق - استعد للدخول!\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🏁 الزوج: {symbol}\n"
        f"{emoji} الاتجاه: {signal_type}\n"
        f"{strength_emoji} القوة: {strength} ({score}/5)\n"
        f"⏳ المدة: {duration} دقيقة\n\n"
        f"{price_line}"
        f"{reasons_line}\n"
        f"🕐 وقت التنبيه: {alert_time}\n"
        f"🎯 وقت الدخول: {entry_time}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⏳ ادخل الصفقة عند الساعة {entry_time} بالضبط\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━"
    )


def format_entry_message(symbol, signal_type, entry_time, duration,
                         wins, losses, pair_wins, pair_losses,
                         score=0, reasons="", entry_price=None,
                         strength="Normal"):
    """Entry confirmation message sent exactly at entry time."""
    emoji = "🟢" if signal_type == "CALL" else "🔴"
    total = wins + losses
    win_rate = round((wins / total * 100)) if total > 0 else 0
    pair_total = pair_wins + pair_losses
    pair_rate = round((pair_wins / pair_total * 100)) if pair_total > 0 else 0
    strength_emoji = "🔥" if strength == "Strong" else ("⚡" if strength == "Good" else "📊")
    price_line = f"💰 سعر الدخول: {entry_price}\n" if entry_price else ""

    return (
        f"🃏 》 JOKER Trading Bot 《 🃏\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🚀 ادخل الآن!\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🏁 الزوج: {symbol}\n"
        f"{emoji} الاتجاه: {signal_type}\n"
        f"{strength_emoji} القوة: {strength} ({score}/5)\n"
        f"⏳ المدة: {duration} دقيقة\n"
        f"🕐 الدخول: {entry_time}\n"
        f"{price_line}\n"
        f"📊 اليوم: {wins}W / {losses}L ({win_rate}%)\n"
        f"📈 {symbol}: {pair_wins}W / {pair_losses}L ({pair_rate}%)\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━"
    )
