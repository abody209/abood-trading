"""
Database layer for Joker Trading Bot.
SQLite with proper multi-pair support and auto-cleanup.
"""

import sqlite3
import os
import logging
from datetime import datetime, date, timedelta

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "signals.db")
logger = logging.getLogger(__name__)


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = _connect()
    c = conn.cursor()

    c.executescript("""
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            signal_type TEXT NOT NULL,
            entry_time TEXT NOT NULL,
            entry_datetime TEXT NOT NULL,
            expiry_datetime TEXT NOT NULL,
            entry_price REAL,
            close_price REAL,
            score REAL DEFAULT 0,
            reasons TEXT DEFAULT '',
            result TEXT DEFAULT 'PENDING',
            date TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS daily_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            symbol TEXT NOT NULL,
            wins INTEGER DEFAULT 0,
            losses INTEGER DEFAULT 0,
            win_rate REAL DEFAULT 0.0,
            updated_at TEXT NOT NULL,
            UNIQUE(date, symbol)
        );

        CREATE INDEX IF NOT EXISTS idx_signals_result ON signals(result);
        CREATE INDEX IF NOT EXISTS idx_signals_symbol_date ON signals(symbol, date);
        CREATE INDEX IF NOT EXISTS idx_daily_stats_date_symbol ON daily_stats(date, symbol);
    """)

    conn.commit()
    conn.close()
    logger.info(f"Database initialized at {DB_PATH}")


def save_signal(symbol, signal_type, entry_time, entry_datetime, expiry_datetime, entry_price, score=0, reasons=""):
    conn = _connect()
    c = conn.cursor()
    today = date.today().isoformat()
    now = datetime.utcnow().isoformat()

    c.execute("""
        INSERT INTO signals (symbol, signal_type, entry_time, entry_datetime, expiry_datetime,
                             entry_price, score, reasons, result, date, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, ?)
    """, (symbol, signal_type, entry_time, entry_datetime.isoformat(),
          expiry_datetime.isoformat(), entry_price, score, reasons, today, now))

    signal_id = c.lastrowid
    conn.commit()
    conn.close()
    return signal_id


def update_signal_result(signal_id, close_price, result):
    conn = _connect()
    conn.execute("UPDATE signals SET close_price = ?, result = ? WHERE id = ?",
                 (close_price, result, signal_id))
    conn.commit()
    conn.close()


def get_pending_signals():
    conn = _connect()
    rows = conn.execute("""
        SELECT id, symbol, signal_type, entry_time, entry_datetime,
               expiry_datetime, entry_price
        FROM signals WHERE result = 'PENDING' ORDER BY created_at ASC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_daily_stats(symbol, date_str=None):
    if date_str is None:
        date_str = date.today().isoformat()

    conn = _connect()
    row = conn.execute("""
        SELECT
            SUM(CASE WHEN result = 'WIN' THEN 1 ELSE 0 END) as wins,
            SUM(CASE WHEN result = 'LOSS' THEN 1 ELSE 0 END) as losses
        FROM signals WHERE date = ? AND symbol = ? AND result != 'PENDING'
    """, (date_str, symbol)).fetchone()

    wins = row["wins"] or 0
    losses = row["losses"] or 0
    total = wins + losses
    win_rate = round((wins / total * 100), 1) if total > 0 else 0.0
    now = datetime.utcnow().isoformat()

    conn.execute("""
        INSERT INTO daily_stats (date, symbol, wins, losses, win_rate, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(date, symbol) DO UPDATE SET
            wins = excluded.wins, losses = excluded.losses,
            win_rate = excluded.win_rate, updated_at = excluded.updated_at
    """, (date_str, symbol, wins, losses, win_rate, now))

    conn.commit()
    conn.close()
    return {"wins": wins, "losses": losses, "win_rate": win_rate}


def get_daily_stats(symbol=None, date_str=None):
    if date_str is None:
        date_str = date.today().isoformat()

    conn = _connect()
    if symbol:
        row = conn.execute(
            "SELECT wins, losses, win_rate FROM daily_stats WHERE date = ? AND symbol = ?",
            (date_str, symbol)
        ).fetchone()
        conn.close()
        if row:
            return {"wins": row["wins"], "losses": row["losses"], "win_rate": row["win_rate"]}
        return {"wins": 0, "losses": 0, "win_rate": 0.0}
    else:
        rows = conn.execute(
            "SELECT symbol, wins, losses, win_rate FROM daily_stats WHERE date = ?",
            (date_str,)
        ).fetchall()
        conn.close()
        total_w = sum(r["wins"] for r in rows)
        total_l = sum(r["losses"] for r in rows)
        total = total_w + total_l
        return {
            "wins": total_w,
            "losses": total_l,
            "win_rate": round((total_w / total * 100), 1) if total > 0 else 0.0,
            "pairs": [dict(r) for r in rows],
        }


def get_pair_stats(symbol, date_str=None):
    return get_daily_stats(symbol=symbol, date_str=date_str)


def get_overall_stats(symbol=None):
    conn = _connect()
    if symbol:
        row = conn.execute("""
            SELECT
                SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN result='LOSS' THEN 1 ELSE 0 END) as losses
            FROM signals WHERE symbol = ? AND result != 'PENDING'
        """, (symbol,)).fetchone()
    else:
        row = conn.execute("""
            SELECT
                SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN result='LOSS' THEN 1 ELSE 0 END) as losses
            FROM signals WHERE result != 'PENDING'
        """).fetchone()
    conn.close()

    wins = row["wins"] or 0
    losses = row["losses"] or 0
    total = wins + losses
    return {
        "wins": wins,
        "losses": losses,
        "total": total,
        "win_rate": round((wins / total * 100), 1) if total > 0 else 0.0,
    }


def get_recent_signals(limit=10):
    conn = _connect()
    rows = conn.execute(
        "SELECT * FROM signals ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def cleanup_old_data(days=30):
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    conn = _connect()
    s = conn.execute("DELETE FROM signals WHERE date < ?", (cutoff,)).rowcount
    d = conn.execute("DELETE FROM daily_stats WHERE date < ?", (cutoff,)).rowcount
    conn.commit()
    conn.close()
    logger.info(f"Cleanup: deleted {s} signals, {d} stats older than {days} days")
    return {"signals": s, "stats": d}


if __name__ == "__main__":
    init_db()
    print("Database ready.")
