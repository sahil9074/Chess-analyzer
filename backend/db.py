"""SQLite storage for games, per-move analysis and cached engine evals."""
import os
import sqlite3
import threading
from pathlib import Path

DATA_DIR = Path(os.environ.get("CHESS_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
DB_PATH = DATA_DIR / "chess.db"
_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
  uuid TEXT PRIMARY KEY, url TEXT, end_time INTEGER, time_class TEXT, time_control TEXT,
  base_seconds INTEGER, color TEXT, opponent TEXT, my_rating INTEGER, opp_rating INTEGER,
  result TEXT, termination TEXT, eco_url TEXT, opening_family TEXT, opening_name TEXT,
  pgn TEXT, analyzed INTEGER DEFAULT 0, accuracy REAL, max_adv REAL, min_adv REAL
);
CREATE INDEX IF NOT EXISTS idx_games_time ON games(end_time);
CREATE TABLE IF NOT EXISTS moves (
  game_uuid TEXT, ply INTEGER, san TEXT, uci TEXT, fen_before TEXT,
  cp_before REAL, cp_after REAL, wp_loss REAL, cls TEXT,
  best_uci TEXT, best_san TEXT, category TEXT, phase TEXT, piece TEXT,
  clock_left REAL, time_trouble INTEGER, eval_source TEXT,
  PRIMARY KEY (game_uuid, ply)
);
CREATE INDEX IF NOT EXISTS idx_moves_cls ON moves(cls);
CREATE TABLE IF NOT EXISTS evals (
  fenkey TEXT PRIMARY KEY, cp REAL, best_uci TEXT, source TEXT
);
CREATE TABLE IF NOT EXISTS game_evals (
  game_uuid TEXT, ply INTEGER, cp REAL, PRIMARY KEY (game_uuid, ply)
);
"""


def conn() -> sqlite3.Connection:
    c = getattr(_local, "c", None)
    if c is None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(DB_PATH, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(SCHEMA)
        _local.c = c
    return c


def rows(sql, args=()):
    return [dict(r) for r in conn().execute(sql, args).fetchall()]
