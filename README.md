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
Detect and classify grocery products on store shelf images. 248 training images, 356 product categories, ~22,700 annotations.

```bash
# 1. Prepare dataset (COCO → YOLO format)
python norgesgruppen/prepare_dataset.py --annotations train/annotations.json --images train/images --output dataset --val-split 0.1

# 2. Train YOLOv8-L at 1536px
python norgesgruppen/train.py --data dataset/dataset.yaml --device 0 --imgsz 1536 --batch 12

# 3. Retrain on all data (no val split) for N epochs
python norgesgruppen/prepare_dataset.py --annotations train/annotations.json --images train/images --output dataset_full --val-split 0.0
python norgesgruppen/train.py --data dataset_full/dataset.yaml --device 0 --imgsz 1536 --batch 12 --epochs N --patience 0

# 4. Optional: balance rare classes
python norgesgruppen/prepare_balanced.py --annotations train/annotations.json --images train/images --input-dataset dataset_full --output dataset_balanced

# 5. Package submission
cp runs/detect/v5/weights/best.pt norgesgruppen/weights/best.pt
cd norgesgruppen && zip -r ../submission.zip run.py weights/best.pt
```

**Approach:** YOLOv8-L multiclass (356 classes) at 1536px with TTA. Detection 0.97, classification 0.73. **Score: 0.898.**

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
