"""Pull game history from the public chess.com API into SQLite."""
import re
import time
from io import StringIO

import chess.pgn
import httpx

from . import db

HEADERS = {"User-Agent": "chess-analyzer (personal study tool)"}
KEEP = {"rapid", "blitz"}
DRAWS = {"agreed", "repetition", "stalemate", "insufficient", "50move", "timevsinsufficient"}
OPENING_WORDS = ("Defense", "Defence", "Opening", "Game", "Gambit", "Attack", "System")


def parse_opening(eco_url: str | None):
    """chess.com ECOUrl slug -> (family, readable name)."""
    if not eco_url:
        return "Unknown", "Unknown"
    slug = eco_url.rstrip("/").rsplit("/", 1)[-1]
    for hyph in ("Caro-Kann", "Nimzo-Indian", "Queens-Indian", "Kings-Indian", "Neo-Grunfeld"):
        slug = slug.replace(hyph, hyph.replace("-", "_"))
    parts = []
    for p in slug.split("-"):
        if re.match(r"^\d", p) or "..." in p:
            break
        parts.append(p)
    if not parts or parts[0] == "Undefined":
        return "Unknown", "Unknown"
    parts = [p.replace("_", "-") for p in parts]
    name = " ".join(parts)
    family = None
    for i, p in enumerate(parts):
        if p in OPENING_WORDS:
            family = " ".join(parts[: i + 1])
            break
    return family or " ".join(parts[:2]), name


def base_seconds(tc: str) -> int:
    try:
        return int(tc.split("+")[0].split("/")[-1])
    except ValueError:
        return 600


def result_of(code: str) -> str:
    if code == "win":
        return "win"
    return "draw" if code in DRAWS else "loss"


def sync(username: str, progress=None) -> dict:
    username = username.lower()
    c = db.conn()
    with httpx.Client(headers=HEADERS, timeout=30) as http:
        r = http.get(f"https://api.chess.com/pub/player/{username}/games/archives")
        r.raise_for_status()
        archives = r.json()["archives"]
        added = 0
        for n, url in enumerate(archives):
            # Re-fetch only the newest month plus months we have never stored.
            month = url.rsplit("/", 2)[-2] + "-" + url.rsplit("/", 1)[-1]
            have = c.execute("SELECT COUNT(*) FROM games WHERE strftime('%Y-%m', end_time,'unixepoch')=?", (month,)).fetchone()[0]
            if have and n < len(archives) - 1:
                if progress:
                    progress(n + 1, len(archives))
                continue
            for _ in range(3):
                resp = http.get(url)
                if resp.status_code == 429:
                    time.sleep(5)
                    continue
                break
            resp.raise_for_status()
            for g in resp.json().get("games", []):
                if g.get("rules") != "chess" or g.get("time_class") not in KEEP or not g.get("pgn"):
                    continue
                white, black = g["white"], g["black"]
                is_white = white["username"].lower() == username
                me, opp = (white, black) if is_white else (black, white)
                family, name = parse_opening(g.get("eco"))
                game = chess.pgn.read_game(StringIO(g["pgn"]))
                if game and game.headers.get("ECOUrl"):
                    family, name = parse_opening(game.headers["ECOUrl"])
                cur = c.execute(
                    "INSERT OR IGNORE INTO games (uuid,url,end_time,time_class,time_control,base_seconds,color,opponent,"
                    "my_rating,opp_rating,result,termination,eco_url,opening_family,opening_name,pgn) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (g["uuid"], g["url"], g["end_time"], g["time_class"], g["time_control"], base_seconds(g["time_control"]),
                     "white" if is_white else "black", opp["username"], me["rating"], opp["rating"],
                     result_of(me["result"]), me["result"] if me["result"] != "win" else opp["result"],
                     g.get("eco"), family, name, g["pgn"]),
                )
                added += cur.rowcount
            c.commit()
            if progress:
                progress(n + 1, len(archives))
            time.sleep(0.3)
    total = c.execute("SELECT COUNT(*) FROM games").fetchone()[0]
    return {"added": added, "total": total}
