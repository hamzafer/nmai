# NM i AI 2026

Norwegian AI Championship — 3 competition tracks.

## Competitions

### `astar/` — Astar Island
Predict terrain evolution on a 40x40 Norse world map. Query a black-box simulator through 15x15 viewports, then submit probability distributions for 6 terrain classes across 5 seeds.

```bash
python -m astar                  # play current round
python -m astar --dry-run        # test without submitting
python astar/backtest.py         # LOO-CV on historical rounds
python astar/build_model.py      # rebuild prior model from ground truth
python astar/score.py            # check leaderboard
```

**Approach:** Feature-based Bayesian priors trained on ground truth + cross-seed regime detection + global adjustment. v3 backtested 36.6 → 69.3 avg score.

### `tripletex/` — Tripletex Accounting
AI agent that completes accounting tasks via the Tripletex API. Receives multilingual prompts (7 languages), plans API calls, executes with auto-fix fallbacks.

```bash
python -m tripletex              # start FastAPI server
python tripletex/auto_submit.py  # auto-submit loop
python tripletex/test_local.py   # run against mock API
```

**Approach:** Claude Opus via Vertex AI → JSON API plan → execution engine with 20+ auto-fix patterns for common failures.

### `norgesgruppen/` — NorgesGruppen Object Detection
Detect and classify grocery products on store shelf images. Offline model submission.

```bash
# TODO: training + inference pipeline
```

**Scoring:** `0.7 × detection_mAP@0.5 + 0.3 × classification_mAP@0.5`

## Infrastructure

```bash
python infra/watcher.py --auto-play   # watch for rounds, auto-play astar
python infra/poller.py                # lightweight 30s poller
```

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
uv pip install requests numpy pymupdf fastapi uvicorn
cp .env.example .env  # add ASTAR_TOKEN
```

## Structure

```
astar/          — terrain prediction (Astar Island)
tripletex/      — accounting agent (Tripletex)
norgesgruppen/  — object detection (NorgesGruppen)
infra/          — automation (watcher, poller)
data/           — round observations (gitignored)
models/         — trained models (gitignored)
docs/task/      — competition references
```
