"""FastAPI app: sync from chess.com, analyse with the hybrid engine, serve the dashboard."""
import html
import json
import os
import re
import threading
from pathlib import Path
from urllib.parse import quote_plus, unquote, urlparse, parse_qs

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import analyze, chesscom, db, engine, insights

ROOT = Path(__file__).resolve().parent.parent
USERNAME = os.environ.get("CHESS_USER", "dirkfade")
RESOURCES = json.loads((Path(__file__).parent / "resources.json").read_text())
PRIORITIES = json.loads((Path(__file__).parent / "priorities.json").read_text())
REPERTOIRE = json.loads((Path(__file__).parent / "repertoire.json").read_text())
_SECTION = {s["id"]: s for s in REPERTOIRE["sections"]}
for _fam, _sec in (("Queens Gambit", "slav"), ("Indian Game", "slav"), ("Slav Defense", "slav"), ("Caro-Kann Defense", "caro"),
                   ("London System", "london"), ("Italian Game", "italian"), ("Ruy Lopez", "italian")):
    RESOURCES["openings"][_fam] = _SECTION[_sec]["resources"] + RESOURCES["openings"].get(_fam, [])

app = FastAPI(title="Chess Analyzer")
state = {
    "sync": {"running": False, "done": 0, "total": 0, "error": None, "result": None},
    "analysis": {"running": False, "done": 0, "total": 0, "error": None},
}
lock = threading.Lock()


class AnalyzeReq(BaseModel):
    limit: int = 25
    depth: int = 12


@app.get("/api/status")
def status():
    c = db.conn()
    counts = c.execute("SELECT COUNT(*) total, SUM(analyzed) analysed FROM games").fetchone()
    return {"user": USERNAME, **state, "games": counts["total"], "analysed": counts["analysed"] or 0,
            "stockfish": bool(engine.find_stockfish())}


@app.post("/api/sync")
def sync(bg: BackgroundTasks):
    with lock:
        if state["sync"]["running"]:
            return state["sync"]
        state["sync"] = {"running": True, "done": 0, "total": 0, "error": None, "result": None}

    def job():
        def prog(d, t):
            state["sync"].update(done=d, total=t)
        try:
            state["sync"]["result"] = chesscom.sync(USERNAME, prog)
        except Exception as exc:
            state["sync"]["error"] = str(exc)
        finally:
            state["sync"]["running"] = False

    bg.add_task(job)
    return state["sync"]


@app.post("/api/analyze")
def run_analysis(req: AnalyzeReq, bg: BackgroundTasks):
    with lock:
        if state["analysis"]["running"]:
            return state["analysis"]
        state["analysis"] = {"running": True, "done": 0, "total": 0, "error": None}

    def job():
        try:
            analyze.run(req.limit, state["analysis"], req.depth)
        except Exception as exc:
            state["analysis"]["error"] = str(exc)
            state["analysis"]["running"] = False

    bg.add_task(job)
    return state["analysis"]


@app.get("/api/priorities")
def priorities():
    return PRIORITIES


@app.get("/api/repertoire")
def repertoire():
    return REPERTOIRE


@app.get("/api/summary")
def summary():
    return insights.summary()


@app.get("/api/resources")
def resources(tag: str = "", opening: str = ""):
    out = list(RESOURCES.get(tag, [])) if tag else []
    curated_opening = RESOURCES["openings"].get(opening, []) if opening else []
    q = opening or tag.replace("_", " ")
    live_links = [
        {"title": f"YouTube: {q} chess lesson", "url": "https://www.youtube.com/results?search_query=" + quote_plus(f"{q} chess beginner lesson"), "kind": "search"},
        {"title": f"Lichess studies: {q}", "url": "https://lichess.org/study/search?q=" + quote_plus(q), "kind": "search"},
        {"title": f"chess.com articles: {q}", "url": "https://www.chess.com/search?q=" + quote_plus(q), "kind": "search"},
    ]
    if opening:
        live_links.insert(0, {"title": f"Lichess opening explorer: {opening}", "url": "https://lichess.org/opening/" + quote_plus(opening.replace(" ", "_")), "kind": "tool"})
    return {"curated": curated_opening + out, "search_links": live_links}


@app.get("/api/live-search")
def live_search(q: str):
    """Live web search (DuckDuckGo HTML endpoint, no API key). Needs outbound access to duckduckgo.com."""
    if not q.strip():
        raise HTTPException(400, "empty query")
    try:
        r = httpx.post("https://html.duckduckgo.com/html/", data={"q": q + " chess"}, timeout=15,
                       headers={"User-Agent": "Mozilla/5.0 (chess-analyzer study tool)"})
        r.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"live search unavailable: {exc}")
    results = []
    for m in re.finditer(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', r.text, re.S):
        href, title = m.group(1), re.sub(r"<.*?>", "", m.group(2))
        qs = parse_qs(urlparse(href).query)
        url = unquote(qs["uddg"][0]) if "uddg" in qs else href
        if url.startswith("http"):
            results.append({"title": html.unescape(title).strip(), "url": url})
        if len(results) >= 8:
            break
    return {"results": results}


app.mount("/static", StaticFiles(directory=ROOT / "frontend"), name="static")


@app.get("/")
def index():
    return FileResponse(ROOT / "frontend" / "index.html")
