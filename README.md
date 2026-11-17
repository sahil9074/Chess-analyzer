# Chess Analyzer

Pulls your rapid and blitz games from chess.com's public API, grades every move with a hybrid engine
(Lichess cloud-eval first, local Stockfish for positions it doesn't know), and shows a dashboard of your
recurring mistakes, opening results and what to study next.

## Run it
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
# Stockfish: apt install stockfish | brew install stockfish  (or set STOCKFISH_PATH)
CHESS_USER=dirkfade .venv/bin/uvicorn backend.main:app --port 8000
```
Open http://localhost:8000, click **Sync**, then **Analyse** (start with 25 games, ~15 s/game; results are cached).

## How it works
- `backend/chesscom.py` – downloads monthly archives, keeps rapid/blitz standard games in SQLite (`data/chess.db`).
- `backend/engine.py` – cloud-eval → Stockfish fallback; every position is cached.
- `backend/analyze.py` – win-% loss per move (Lichess formula) → inaccuracy / mistake / blunder; tags each big
  mistake (hung piece, missed capture, missed mate, allowed mate, tactical oversight, positional), phase, piece and time trouble.
- `backend/insights.py` – aggregates stats and writes the "what to work on" recommendations.
- `backend/resources.json` – curated links per mistake type / opening; the dashboard also builds search links and
  has a live web search box (DuckDuckGo HTML endpoint, needs outbound access).
