"""Per-game analysis: evaluate positions, grade the user's moves, tag mistake themes."""
import math
from io import StringIO

import chess
import chess.pgn

from . import db
from .engine import Evaluator

MAX_PLY = 100  # analyse at most the first 100 plies (50 moves each)
VALUES = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9}
MATE_THRESHOLD = 2900


def win_pct(cp: float) -> float:
    cp = max(-1500.0, min(1500.0, cp))
    return 50 + 50 * (2 / (1 + math.exp(-0.00368208 * cp)) - 1)


def move_accuracy(loss: float) -> float:
    return max(0.0, min(100.0, 103.1668 * math.exp(-0.04354 * loss) - 3.1669))


def classify(loss: float, is_best: bool) -> str:
    if loss >= 20:
        return "blunder"
    if loss >= 10:
        return "mistake"
    if loss >= 5:
        return "inaccuracy"
    return "best" if is_best else "good"


def phase_of(board: chess.Board) -> str:
    if board.fullmove_number <= 10:
        return "opening"
    material = sum(VALUES[p.piece_type] for p in board.piece_map().values() if p.piece_type in VALUES and p.piece_type != chess.PAWN)
    return "endgame" if material <= 24 else "middlegame"


def captured_value(board: chess.Board, move: chess.Move) -> int:
    if not board.is_capture(move):
        return 0
    if board.is_en_passant(move):
        return 1
    p = board.piece_at(move.to_square)
    return VALUES.get(p.piece_type, 0) if p else 0


def categorize(before: chess.Board, move: chess.Move, after: chess.Board, me_before: float, me_after: float,
               best_before: str | None, opp_best_after: str | None, loss: float) -> str:
    if me_after <= -MATE_THRESHOLD:
        return "allowed_mate"
    if me_before >= MATE_THRESHOLD:
        return "missed_mate"
    if opp_best_after:
        try:
            reply = chess.Move.from_uci(opp_best_after)
            if captured_value(after, reply) >= 3:
                return "hung_piece"
        except ValueError:
            pass
    if best_before:
        try:
            bm = chess.Move.from_uci(best_before)
            if captured_value(before, bm) >= 3:
                return "missed_capture"
            if before.gives_check(bm) and loss >= 20:
                return "missed_tactic"
        except ValueError:
            pass
    return "tactical_oversight" if loss >= 20 else "positional"


def analyze_game(game: dict, ev: Evaluator) -> dict:
    g = chess.pgn.read_game(StringIO(game["pgn"]))
    me_white = game["color"] == "white"
    board = g.board()
    nodes = list(g.mainline())
    n = min(len(nodes), MAX_PLY)

    boards = [board.copy()]
    for node in nodes[:n]:
        board.push(node.move)
        boards.append(board.copy())

    evals = [ev.eval(b) for b in boards]  # (cp_white, best_uci, source)

    def me_cp(cp):
        return None if cp is None else (cp if me_white else -cp)

    rows, accs, advs = [], [], []
    for k in range(1, n + 1):
        node = nodes[k - 1]
        mover_white = boards[k - 1].turn == chess.WHITE
        cpb, best_before, src_b = evals[k - 1]
        cpa, best_after, src_a = evals[k]
        if cpa is not None:
            advs.append(me_cp(cpa))
        if mover_white != me_white or cpb is None or cpa is None:
            continue
        mb, ma = me_cp(cpb), me_cp(cpa)
        loss = max(0.0, win_pct(mb) - win_pct(ma))
        move = node.move
        is_best = best_before == move.uci()
        if is_best:
            loss = 0.0
        cls = classify(loss, is_best)
        accs.append(move_accuracy(loss))
        best_san = None
        if best_before:
            try:
                best_san = boards[k - 1].san(chess.Move.from_uci(best_before))
            except ValueError:
                pass
        category = categorize(boards[k - 1], move, boards[k], mb, ma, best_before, best_after, loss) if loss >= 10 else None
        clock = node.clock()
        base = game["base_seconds"] or 600
        trouble = int(clock is not None and clock <= max(15.0, 0.08 * base))
        rows.append((game["uuid"], k, boards[k - 1].san(move), move.uci(), boards[k - 1].fen(), cpb, cpa, loss, cls,
                     best_before, best_san, category, phase_of(boards[k - 1]),
                     chess.piece_name(boards[k - 1].piece_at(move.from_square).piece_type), clock, trouble, src_b))

    c = db.conn()
    c.execute("DELETE FROM moves WHERE game_uuid=?", (game["uuid"],))
    c.executemany("INSERT INTO moves VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    c.execute("DELETE FROM game_evals WHERE game_uuid=?", (game["uuid"],))
    c.executemany("INSERT INTO game_evals VALUES (?,?,?)",
                  [(game["uuid"], i, me_cp(e[0])) for i, e in enumerate(evals) if e[0] is not None])
    c.execute("UPDATE games SET analyzed=1, accuracy=?, max_adv=?, min_adv=? WHERE uuid=?",
              (sum(accs) / len(accs) if accs else None, max(advs) if advs else None, min(advs) if advs else None, game["uuid"]))
    c.commit()
    return {"moves": len(rows)}


def run(limit: int, status: dict, depth: int = 12):
    """Analyse the `limit` most recent unanalysed games."""
    todo = db.rows("SELECT * FROM games WHERE analyzed=0 ORDER BY end_time DESC LIMIT ?", (limit,))
    status.update(running=True, done=0, total=len(todo), error=None, engine=None)
    ev = Evaluator(depth=depth)
    status["engine"] = "cloud + stockfish" if ev.sf else "cloud only (install Stockfish for full coverage)"
    try:
        for i, game in enumerate(todo):
            status["current"] = f'{game["opponent"]} ({game["opening_family"]})'
            try:
                analyze_game(game, ev)
            except Exception as exc:  # keep going; one bad PGN shouldn't kill the batch
                status["last_error"] = f'{game["uuid"]}: {exc}'
            status["done"] = i + 1
            status["sources"] = dict(ev.stats)
    finally:
        ev.close()
        status["running"] = False
