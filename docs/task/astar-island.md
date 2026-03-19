# Astar Island — Viking Civilisation Prediction

## Overview

- **Task type**: Observation + probabilistic prediction
- **Platform**: [app.ainm.no](https://app.ainm.no)
- **API**: REST endpoints at `api.ainm.no/astar-island/`
- **Goal**: Observe a black-box Norse civilisation simulator through limited viewports and predict the probability distribution of terrain types across the entire map.

## How It Works

1. A **round** starts — fixed map, hidden parameters, 5 random seeds
2. **Observe** through a viewport — `POST /astar-island/simulate` with coordinates (max 15x15 cells). 50 queries total per round, shared across all 5 seeds.
3. **Learn** the hidden rules from viewport observations
4. **Predict** — submit a `H x W x 6` probability tensor per seed
5. **Scored** by entropy-weighted KL divergence against ground truth

## Key Constraints

| Constraint | Value |
|---|---|
| Map size | 40x40 |
| Seeds per round | 5 |
| Queries per round | 50 (shared across seeds) |
| Viewport size | max 15x15 |
| Simulation length | 50 years |
| Prediction classes | 6 |
| Prediction window | ~2h 45m |
| Rate limit (simulate) | 5 req/s |
| Rate limit (submit) | 2 req/s |

## Terrain Types & Prediction Classes

| Internal Code | Terrain | Class Index | Description |
|---|---|---|---|
| 10 | Ocean | 0 (Empty) | Impassable water, borders the map |
| 11 | Plains | 0 (Empty) | Flat land, buildable |
| 0 | Empty | 0 | Generic empty cell |
| 1 | Settlement | 1 | Active Norse settlement |
| 2 | Port | 2 | Coastal settlement with harbour |
| 3 | Ruin | 3 | Collapsed settlement |
| 4 | Forest | 4 | Provides food to adjacent settlements |
| 5 | Mountain | 5 | Impassable terrain |

Ocean, Plains, and Empty all map to **class 0**. Mountains are static. Forests are mostly static. The dynamic cells are Settlements, Ports, and Ruins.

## Simulation Mechanics

Each of the 50 years cycles through 5 phases:

### 1. Growth
- Settlements produce food from adjacent terrain (forests + plains)
- Population grows when food is positive
- Prosperous settlements expand by founding new settlements on nearby land
- Coastal settlements can develop ports
- Ports can build longships

### 2. Conflict
- Settlements raid each other (longships extend range)
- Low-food settlements raid more aggressively
- Successful raids loot resources and damage the defender
- Conquered settlements can change faction allegiance

### 3. Trade
- Ports within range trade if not at war
- Trade generates wealth and food for both parties
- Technology diffuses between trading partners

### 4. Winter
- Variable severity each year
- All settlements lose food
- Settlements can collapse from starvation → become Ruins
- Population disperses to nearby friendly settlements

### 5. Environment
- Nearby thriving settlements may reclaim ruins (restore as settlement/port)
- Unreclaimed ruins may be overtaken by forest or decay to plains

## Settlement Properties (Hidden State)

Each settlement tracks: position, population, food, wealth, defense, tech level, port status, longship ownership, and faction allegiance (owner_id).

Only position and port status are visible in initial states. Internal stats are never directly observable.

## Hidden Parameters (Change Per Round)

These control the simulation behavior and are the same for all 5 seeds in a round:
- Growth rate, food consumption, expansion threshold
- Raid probability, damage, conquest chance
- Trade range, food/wealth generation
- Winter severity (base + range)
- Ruin reclaim probability, forest regrowth rate

## Map Generation

Each map is procedurally generated from a **map seed**:
- Ocean borders surround the map
- Fjords cut inland from random edges
- Mountain chains form via random walks
- Forest patches cover land with clustered groves
- Initial settlements placed on land cells, spaced apart

The map seed is visible — you can reconstruct initial terrain locally.

## API Endpoints

**Base URL**: `https://api.ainm.no/astar-island`
**Auth**: JWT via cookie (`access_token`) or `Authorization: Bearer <token>`

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/rounds` | Public | List all rounds |
| GET | `/rounds/{id}` | Public | Round details + initial states per seed |
| GET | `/budget` | Team | Remaining queries for active round |
| POST | `/simulate` | Team | Observe viewport (costs 1 query) |
| POST | `/submit` | Team | Submit H x W x 6 prediction tensor |
| GET | `/my-rounds` | Team | Your scores, ranks, budget across rounds |
| GET | `/my-predictions/{round_id}` | Team | Your submitted predictions + confidence |
| GET | `/analysis/{round_id}/{seed_index}` | Team | Post-round ground truth (after completion) |
| GET | `/leaderboard` | Public | Public leaderboard |

### POST /simulate

```json
{
  "round_id": "uuid",
  "seed_index": 0,
  "viewport_x": 10,
  "viewport_y": 5,
  "viewport_w": 15,
  "viewport_h": 15
}
```

Returns the grid + settlements within the viewport after one stochastic 50-year simulation. Each call uses a different random seed — different outcomes every time.

### POST /submit

```json
{
  "round_id": "uuid",
  "seed_index": 0,
  "prediction": [[[0.01, 0.6, 0.25, 0.1, 0.03, 0.01], ...], ...]
}
```

Prediction is `H x W x 6`, probabilities must sum to 1.0 (±0.01 tolerance). Resubmitting overwrites previous.

### Error Codes

- 400: Round not active or invalid seed
- 403: Not on a team
- 404: Round not found
- 429: Budget exhausted or rate limit exceeded

## Round Lifecycle

`pending` → `active` → `scoring` → `completed`

- **active**: queries and submissions open (prediction window ~2h 45m)
- **scoring**: submissions locked, scoring in progress
- **completed**: scores finalized, ground truth available via `/analysis`

**You cannot submit after the window closes.** Miss a round = 0 score for that round.

## Scoring

### Entropy-Weighted KL Divergence

Ground truth is computed from **hundreds of simulations** with the true hidden parameters.

```
KL(p || q) = Σ pᵢ × log(pᵢ / qᵢ)
```

Where `p` = ground truth, `q` = your prediction.

Only **dynamic cells** (non-zero entropy) contribute to the score:

```
weighted_kl = Σ entropy(cell) × KL(ground_truth[cell], prediction[cell])
              ─────────────────────────────────────────────────────────
                            Σ entropy(cell)

score = max(0, min(100, 100 × exp(-3 × weighted_kl)))
```

- **100** = perfect prediction
- **0** = terrible prediction

### Critical: Never Assign 0.0 Probability

If ground truth has `pᵢ > 0` but your prediction has `qᵢ = 0`, KL divergence becomes **infinite**. Always enforce a minimum floor of **0.01** per class, then renormalize.

### Per-Round Score

```
round_score = average(score_seed_0, ..., score_seed_4)
```

Missing seeds score **0**. Always submit all 5.

### Leaderboard

```
leaderboard_score = best round_score of all time
```

Later rounds may have higher weights. Hot streak = average of last 3 rounds.

## Quickstart

### Authentication

```python
import requests
session = requests.Session()
session.headers["Authorization"] = "Bearer YOUR_JWT_TOKEN"
```

Get your JWT from `app.ainm.no` browser cookies → `access_token`.

### Find Active Round

```python
BASE = "https://api.ainm.no"
rounds = session.get(f"{BASE}/astar-island/rounds").json()
active = next((r for r in rounds if r["status"] == "active"), None)
round_id = active["id"]
```

### Get Initial States (Free)

```python
detail = session.get(f"{BASE}/astar-island/rounds/{round_id}").json()
for i, state in enumerate(detail["initial_states"]):
    grid = state["grid"]          # 40x40 terrain codes
    settlements = state["settlements"]  # [{x, y, has_port, alive}]
```

### Query Simulator

```python
result = session.post(f"{BASE}/astar-island/simulate", json={
    "round_id": round_id,
    "seed_index": 0,
    "viewport_x": 10, "viewport_y": 5,
    "viewport_w": 15, "viewport_h": 15,
}).json()
# result["grid"] = 2D array of terrain codes after 50 years
```

### Submit Predictions

```python
import numpy as np
prediction = np.full((40, 40, 6), 1/6)  # uniform baseline
session.post(f"{BASE}/astar-island/submit", json={
    "round_id": round_id,
    "seed_index": 0,
    "prediction": prediction.tolist(),
})
```

## Our System

```
nmai/
├── astar.py          # Plays rounds (Bayesian + simulator + cumulative learning)
├── simulator.py      # Local Norse world sim (growth/conflict/trade/winter/env)
├── analyze.py        # Post-round: pulls ground truth, builds cumulative models
├── watcher.py        # Auto-detects rounds, triggers bot + analyzer
├── check_score.py    # Quick leaderboard check
├── .env              # JWT token (ASTAR_TOKEN=...)
├── data/round_N/     # Per-round saved data (observations, predictions, ground truth)
└── models/           # Cumulative models (transition_model.npy, neighborhood_model.npy)
```

### Usage

```bash
# Play a round manually
python3 astar.py

# Auto-play rounds (background)
python3 watcher.py --auto-play &

# Check scores
python3 check_score.py

# Analyze completed rounds + build models
python3 analyze.py --all

# Test simulator locally
python3 simulator.py 500
```

### How It Gets Smarter Each Round

1. `watcher.py` detects active round → triggers `astar.py`
2. `astar.py` loads cumulative models from past rounds
3. Uses 50 queries on dynamic areas (repeated observations)
4. Calibrates local simulator parameters from observations
5. Runs 200 local simulations per seed
6. Blends simulator output with Bayesian predictions
7. Submits all 5 seeds
8. Round closes → `watcher.py` triggers `analyze.py`
9. `analyze.py` pulls ground truth → updates cumulative models
10. Next round starts with better models → repeat
