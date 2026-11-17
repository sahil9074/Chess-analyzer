"""Export a self-contained, read-only snapshot of the dashboard (for sharing on a phone)."""
import json
import re
import sys
import time
from pathlib import Path

from . import db, insights

ROOT = Path(__file__).resolve().parent.parent
STUB = r"""
const SNAP=__SNAP__;
const api=async p=>{
  if(p.startsWith('/api/summary'))return SNAP.summary;
  if(p.startsWith('/api/repertoire'))return SNAP.repertoire;
  if(p.startsWith('/api/status'))return {user:SNAP.user,sync:{running:false},analysis:{running:false},games:SNAP.games,analysed:SNAP.analysed,stockfish:true};
  if(p.startsWith('/api/resources')){
    const u=new URLSearchParams(p.split('?')[1]),tag=u.get('tag')||'',op=u.get('opening')||'',R=SNAP.resources;
    const q=op||tag.replace(/_/g,' '),e=encodeURIComponent,
    sl=[{title:'YouTube: '+q+' chess lesson',url:'https://www.youtube.com/results?search_query='+e(q+' chess beginner lesson'),kind:'search'},
        {title:'Lichess studies: '+q,url:'https://lichess.org/study/search?q='+e(q),kind:'search'},
        {title:'chess.com articles: '+q,url:'https://www.chess.com/search?q='+e(q),kind:'search'}];
    if(op)sl.unshift({title:'Lichess opening explorer: '+op,url:'https://lichess.org/opening/'+e(op.replace(/ /g,'_')),kind:'tool'});
    return {curated:[...(R.openings[op]||[]),...(tag?R[tag]||[]:[])],search_links:sl};
  }
  throw new Error('offline snapshot');
};
"""

TOKENS_DARK = "--bg:#171a1e;--card:#21252b;--ink:#e8e9eb;--muted:#99a0ab;--line:#33383f;--accent:#5fb98a;--win:#4aa872;--draw:#7c7a70;--loss:#d9665c;--warn:#e5a243"


def build() -> str:
    s = (ROOT / "frontend" / "index.html").read_text()
    summary = insights.summary()
    st = db.rows("SELECT COUNT(*) n, SUM(analyzed) a FROM games")[0]
    snap = {"user": "dirkfade", "summary": summary, "games": st["n"], "analysed": st["a"] or 0,
            "resources": json.loads((Path(__file__).parent / "resources.json").read_text()),
            "repertoire": json.loads((Path(__file__).parent / "repertoire.json").read_text())}
    for fam in ("Queens Gambit", "Indian Game", "Slav Defense"):
        snap["resources"]["openings"].setdefault(fam, snap["repertoire"]["resources"])
    s = re.sub(r"<!doctype html>|<html[^>]*>|</?head>|<body>|</body>|</html>", "", s)
    s = re.sub(r'<meta charset="utf-8">\s*<meta name="viewport"[^>]*>', '<meta name="claude:viewport-fit-sides" content="cover">', s)
    # themes: tokens for explicit dark, guarded media query
    s = s.replace("@media (prefers-color-scheme:dark){:root{", '@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;')
    s = s.replace("*{box-sizing", f':root[data-theme="dark"]{{color-scheme:dark;{TOKENS_DARK}}}\n*{{box-sizing', 1)
    # side gutters that clear a phone system bar
    gut = "max(16px,env(safe-area-inset-left,0px))"
    gutr = "max(16px,env(safe-area-inset-right,0px))"
    s = s.replace("header{padding:20px 16px 8px;", f"header{{padding:20px {gutr} 8px {gut};")
    s = s.replace("main{max-width:1100px;margin:auto;padding:0 16px 48px}", f"main{{max-width:1100px;margin:auto;padding:0 {gutr} 48px {gut}}}")
    s = s.replace("table{width:100%;", "table{min-width:560px;width:100%;")
    s = s.replace('<div id="opWhite">', '<div id="opWhite" style="overflow-x:auto">').replace('<div id="opBlack">', '<div id="opBlack" style="overflow-x:auto">')
    # replace controls with a snapshot note
    when = time.strftime("%b %d, %Y", time.gmtime())
    s = re.sub(r'<div class="card">\s*<div class="row">\s*<button id="syncBtn">.*?<div id="toast">Loading…</div></div>\s*</div>',
               f'<div class="card"><b>Snapshot</b> · {st["a"] or 0} of {st["n"]} games analysed · taken {when}. '
               '<span class="muted">Static copy; ask Claude to refresh it after more games are analysed.</span></div>'
               '<div hidden><button id="syncBtn"></button><input id="limit"><button id="anBtn"></button><button id="refresh"></button><i id="prog"></i><div id="toast"></div></div>',
               s, flags=re.S)
    s = s.replace("<h2>Find learning resources</h2>", "<h2>Search for learning resources</h2>")
    s = s.replace('<button id="qBtn">Search the web</button>', '<button id="qBtn">Find links</button>')
    s = re.sub(r"const api=.*?\n", lambda m: STUB.replace("__SNAP__", json.dumps(snap)) + "\n", s, count=1)
    return s


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "snapshot.html")
    out.write_text(build())
    print(out, len(out.read_text()) // 1024, "KB")
