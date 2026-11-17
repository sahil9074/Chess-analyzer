"""Hybrid evaluator: Lichess cloud-eval first, local Stockfish for everything it doesn't know."""
import os
import shutil
import time

import chess
import chess.engine
import httpx

from . import db

CLOUD_URL = "https://lichess.org/api/cloud-eval"
MATE_CP = 3000


def find_stockfish():
    for cand in (os.environ.get("STOCKFISH_PATH"), shutil.which("stockfish"), "/usr/games/stockfish", "/usr/local/bin/stockfish"):
        if cand and os.path.exists(cand):
            return cand
    return None


def fenkey(board: chess.Board) -> str:
    return " ".join(board.fen().split()[:4])


class Evaluator:
    """eval(board) -> (cp from White's point of view, best move uci, source)."""

    def __init__(self, depth: int = 12):
        self.depth = depth
        self.http = httpx.Client(timeout=15, headers={"User-Agent": "chess-analyzer (personal study tool)"})
        path = find_stockfish()
        self.sf = chess.engine.SimpleEngine.popen_uci(path) if path else None
        if self.sf:
            try:
                self.sf.configure({"Threads": max(1, (os.cpu_count() or 2) - 1), "Hash": 128})
            except chess.engine.EngineError:
                pass
        self.cloud_ok = True
        self.cloud_pause_until = 0.0
        self.stats = {"cache": 0, "cloud": 0, "stockfish": 0, "none": 0}

    def close(self):
        if self.sf:
            self.sf.quit()
        self.http.close()

    def eval(self, board: chess.Board):
        if board.is_checkmate():
            return (-MATE_CP if board.turn == chess.WHITE else MATE_CP), None, "terminal"
        if board.is_stalemate() or board.is_insufficient_material():
            return 0.0, None, "terminal"
        key = fenkey(board)
        c = db.conn()
        row = c.execute("SELECT cp,best_uci,source FROM evals WHERE fenkey=?", (key,)).fetchone()
        if row:
            self.stats["cache"] += 1
            return row["cp"], row["best_uci"], row["source"]
        res = self._cloud(board) or self._stockfish(board)
        if res is None:
            self.stats["none"] += 1
            return None, None, "none"
        c.execute("INSERT OR REPLACE INTO evals VALUES (?,?,?,?)", (key, *res))
        return res

    def _cloud(self, board):
        if not self.cloud_ok or time.time() < self.cloud_pause_until:
            return None
        try:
            r = self.http.get(CLOUD_URL, params={"fen": board.fen(), "multiPv": 1})
        except httpx.HTTPError:
            return None
        if r.status_code == 429:  # rate limited: back off, let Stockfish carry on
            self.cloud_pause_until = time.time() + 60
            return None
        if r.status_code != 200:
            time.sleep(0.05)
            return None
        pv = r.json().get("pvs", [{}])[0]
        moves = pv.get("moves", "").split()
        if not moves:
            return None
        if "mate" in pv:
            m = pv["mate"]
            cp = (MATE_CP - abs(m)) * (1 if m > 0 else -1)
        else:
            cp = float(pv["cp"])
        self.stats["cloud"] += 1
        time.sleep(0.12)
        return cp, moves[0], "cloud"

    def _stockfish(self, board):
        if not self.sf:
            return None
        info = self.sf.analyse(board, chess.engine.Limit(depth=self.depth))
        score = info["score"].white()
        cp = float(score.score(mate_score=MATE_CP))
        pv = info.get("pv") or []
        self.stats["stockfish"] += 1
        return cp, (pv[0].uci() if pv else None), "stockfish"
