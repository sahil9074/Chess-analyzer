"""Aggregate analysed games into dashboard stats and plain-language recommendations."""
from . import db

CAT_LABELS = {
    "hung_piece": "Hung a piece (left it capturable)",
    "missed_capture": "Missed a free piece",
    "missed_mate": "Missed a checkmate",
    "allowed_mate": "Walked into checkmate",
    "missed_tactic": "Missed a tactic (check/fork)",
    "tactical_oversight": "Other tactical oversight",
    "positional": "Positional / planning slip",
}


def _pct(a, b):
    return round(100 * a / b, 1) if b else 0.0


def overview():
    games = db.rows("SELECT * FROM games ORDER BY end_time")
    analysed = [g for g in games if g["analyzed"]]
    by_tc = {}
    for tc in ("rapid", "blitz"):
        gs = [g for g in games if g["time_class"] == tc]
        w = sum(g["result"] == "win" for g in gs)
        d = sum(g["result"] == "draw" for g in gs)
        by_tc[tc] = {"games": len(gs), "win": w, "draw": d, "loss": len(gs) - w - d, "win_rate": _pct(w, len(gs)),
                     "rating": gs[-1]["my_rating"] if gs else None,
                     "peak": max((g["my_rating"] for g in gs), default=None)}
    ratings = [{"t": g["end_time"], "r": g["my_rating"], "tc": g["time_class"]} for g in games]
    accs = [g["accuracy"] for g in analysed if g["accuracy"] is not None]
    # accuracy trend, in order of play
    acc_trend = [{"t": g["end_time"], "a": round(g["accuracy"], 1)} for g in analysed if g["accuracy"] is not None]
    return {"total_games": len(games), "analysed_games": len(analysed), "by_time_class": by_tc,
            "rating_history": ratings, "avg_accuracy": round(sum(accs) / len(accs), 1) if accs else None,
            "accuracy_trend": acc_trend}


def mistakes():
    total_moves = db.rows("SELECT phase, COUNT(*) n FROM moves GROUP BY phase")
    nmoves = {r["phase"]: r["n"] for r in total_moves}
    sev = db.rows("SELECT phase, cls, COUNT(*) n FROM moves WHERE cls IN ('inaccuracy','mistake','blunder') GROUP BY phase, cls")
    phase = {}
    for p in ("opening", "middlegame", "endgame"):
        row = {"moves": nmoves.get(p, 0)}
        for s in ("inaccuracy", "mistake", "blunder"):
            row[s] = sum(r["n"] for r in sev if r["phase"] == p and r["cls"] == s)
        big = row["mistake"] + row["blunder"]
        row["big_per_100"] = round(100 * big / row["moves"], 1) if row["moves"] else 0
        phase[p] = row
    cats = db.rows("SELECT category, COUNT(*) n, AVG(wp_loss) loss FROM moves WHERE category IS NOT NULL GROUP BY category ORDER BY n DESC")
    for c in cats:
        c["label"] = CAT_LABELS.get(c["category"], c["category"])
        c["loss"] = round(c["loss"], 1)
    pieces = db.rows("SELECT piece, COUNT(*) n FROM moves WHERE cls IN ('mistake','blunder') GROUP BY piece ORDER BY n DESC")
    tt = db.rows("SELECT time_trouble tt, COUNT(*) moves, SUM(cls IN ('mistake','blunder')) big FROM moves WHERE clock_left IS NOT NULL GROUP BY time_trouble")
    time_trouble = {str(r["tt"]): {"moves": r["moves"], "big": r["big"] or 0, "rate": _pct(r["big"] or 0, r["moves"])} for r in tt}
    return {"phase": phase, "categories": cats, "pieces": pieces, "time_trouble": time_trouble}


def openings():
    out = {}
    for color in ("white", "black"):
        rows = db.rows(
            "SELECT opening_family fam, COUNT(*) n, SUM(result='win') w, SUM(result='draw') d, SUM(result='loss') l, "
            "AVG(accuracy) acc FROM games WHERE color=? GROUP BY opening_family ORDER BY n DESC", (color,))
        for r in rows:
            r["score"] = _pct((r["w"] or 0) + 0.5 * (r["d"] or 0), r["n"])
            r["acc"] = round(r["acc"], 1) if r["acc"] is not None else None
            # eval after move 10 (ply 20) from my point of view, and early big mistakes
            ev = db.rows("SELECT AVG(ge.cp) cp FROM game_evals ge JOIN games g ON g.uuid=ge.game_uuid "
                         "WHERE g.color=? AND g.opening_family=? AND ge.ply=20", (color, r["fam"]))
            r["eval_move10"] = round(ev[0]["cp"] / 100, 2) if ev and ev[0]["cp"] is not None else None
            em = db.rows("SELECT COUNT(*) n FROM moves m JOIN games g ON g.uuid=m.game_uuid WHERE g.color=? AND g.opening_family=? "
                         "AND m.phase='opening' AND m.cls IN ('mistake','blunder')", (color, r["fam"]))
            r["early_big_mistakes"] = em[0]["n"]
            names = db.rows("SELECT opening_name nm, COUNT(*) n, SUM(result='win') w FROM games WHERE color=? AND opening_family=? "
                            "GROUP BY opening_name ORDER BY n DESC LIMIT 5", (color, r["fam"]))
            r["variations"] = names
        out[color] = rows
    return out


def conversion():
    g = db.rows("SELECT result, max_adv, min_adv FROM games WHERE analyzed=1")
    ahead = [x for x in g if (x["max_adv"] or 0) >= 300]
    behind = [x for x in g if (x["min_adv"] or 0) <= -300]
    return {
        "had_winning_position": len(ahead),
        "converted": sum(x["result"] == "win" for x in ahead),
        "threw_away": sum(x["result"] != "win" for x in ahead),
        "was_losing": len(behind),
        "saved": sum(x["result"] != "loss" for x in behind),
    }


def worst_moments(limit=15):
    return db.rows(
        "SELECT m.game_uuid, m.ply, m.san, m.best_san, m.best_uci, m.uci, m.fen_before, m.wp_loss, m.category, m.phase, m.piece, "
        "m.time_trouble, g.url, g.opening_family, g.color, g.opponent, g.end_time "
        "FROM moves m JOIN games g ON g.uuid=m.game_uuid WHERE m.cls='blunder' ORDER BY m.wp_loss DESC, g.end_time DESC LIMIT ?", (limit,))


def recommendations(ov, mk, op, conv):
    recs = []
    if not ov["analysed_games"]:
        return recs
    cats = mk["categories"]
    total_big = sum(c["n"] for c in cats) or 1
    top = cats[0] if cats else None
    if top:
        recs.append({"tag": top["category"], "severity": "high",
                     "title": f'Your #1 leak: {top["label"].lower()}',
                     "detail": f'{top["n"]} of your {total_big} mistakes/blunders ({_pct(top["n"], total_big)}%) fall in this bucket, costing about {top["loss"]} win-% each.'})
    for c in cats[1:3]:
        recs.append({"tag": c["category"], "severity": "medium", "title": c["label"],
                     "detail": f'{c["n"]} mistakes ({_pct(c["n"], total_big)}% of the total).'})
    ph = mk["phase"]
    worst = max(ph, key=lambda p: ph[p]["big_per_100"])
    if ph[worst]["moves"]:
        recs.append({"tag": worst, "severity": "medium", "title": f"Weakest phase: {worst}",
                     "detail": f'{ph[worst]["big_per_100"]} mistakes/blunders per 100 moves in the {worst}, versus '
                               + ", ".join(f'{v["big_per_100"]} in the {k}' for k, v in ph.items() if k != worst) + "."})
    tt = mk["time_trouble"]
    if "1" in tt and tt["1"]["moves"] >= 15:
        calm = tt.get("0", {"rate": 0})["rate"]
        recs.append({"tag": "time_trouble", "severity": "high" if tt["1"]["rate"] > calm * 1.5 else "low",
                     "title": "Time pressure",
                     "detail": f'You blunder {tt["1"]["rate"]}% of moves when low on the clock vs {calm}% otherwise.'})
    if conv["had_winning_position"] >= 3 and conv["threw_away"] / conv["had_winning_position"] >= 0.3:
        recs.append({"tag": "conversion", "severity": "high", "title": "Converting winning positions",
                     "detail": f'You reached a clearly winning position (+3) in {conv["had_winning_position"]} games but only won {conv["converted"]}.'})
    for color in ("white", "black"):
        cand = [o for o in op[color] if o["n"] >= 3 and o["fam"] != "Unknown"]
        if cand:
            w = min(cand, key=lambda o: o["score"])
            if w["score"] < 45:
                recs.append({"tag": "opening", "opening": w["fam"], "color": color, "severity": "medium",
                             "title": f'Rework your {w["fam"]} as {color.capitalize()}',
                             "detail": f'{w["n"]} games, only {w["score"]}% score and {w["early_big_mistakes"]} big mistakes in the first 10 moves.'})
    return recs


def summary():
    ov, mk, op, conv = overview(), mistakes(), openings(), conversion()
    return {"overview": ov, "mistakes": mk, "openings": op, "conversion": conv,
            "worst": worst_moments(), "recommendations": recommendations(ov, mk, op, conv)}
