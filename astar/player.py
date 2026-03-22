"""
Astar Island — Round Player (v3: Feature-Based Priors + Global Adjustment)
Usage: python astar.py [--token YOUR_JWT_TOKEN] [--dry-run]
       Reads ASTAR_TOKEN from .env if --token not provided.

Key improvements over v2:
- Feature-based priors from 17 rounds of ground truth (no more 97% "static" assumption)
- Cross-seed observation pooling for per-round regime detection
- Global prior adjustment based on observed transition rates
- LOO-CV backtested: 36.6 → 69.3 average score
"""

import argparse
import json
import os
import time
from datetime import datetime
from pathlib import Path
import requests
import numpy as np
from collections import defaultdict

BASE = "https://api.ainm.no"

# 6 prediction classes
CLASSES = ["Empty", "Settlement", "Port", "Ruin", "Forest", "Mountain"]
NUM_CLASSES = 6

# Map internal terrain codes to prediction class indices
TERRAIN_TO_CLASS = {
    0: 0,   # Empty -> Empty
    10: 0,  # Ocean -> Empty
    11: 0,  # Plains -> Empty
    1: 1,   # Settlement
    2: 2,   # Port
    3: 3,   # Ruin
    4: 4,   # Forest
    5: 5,   # Mountain
}

DYNAMIC_RADIUS = 7  # cells around settlements considered dynamic

DATA_DIR = Path(__file__).parent.parent / "data"
MODELS_DIR = Path(__file__).parent.parent / "models"

# Prediction parameters (tuned via LOO-CV on 17 rounds)
CONCENTRATION = 15.0  # Dirichlet concentration for Bayesian update
OBS_WEIGHT = 1.0      # Weight of global adjustment (1.0 = fully adjusted)
MIN_FLOOR = 0.005     # Minimum probability per class


# ─── Data persistence ─────────────────────────────────────────────────────────

def save_round_data(round_number, round_id, detail, all_observations, transition_priors):
    """Save all round data to disk for later analysis."""
    round_dir = DATA_DIR / f"round_{round_number}"
    round_dir.mkdir(parents=True, exist_ok=True)

    # Save round metadata
    meta = {
        "round_id": round_id,
        "round_number": round_number,
        "map_width": detail["map_width"],
        "map_height": detail["map_height"],
        "seeds_count": detail["seeds_count"],
        "saved_at": datetime.utcnow().isoformat(),
    }
    (round_dir / "meta.json").write_text(json.dumps(meta, indent=2))

    # Save initial states
    for i, state in enumerate(detail["initial_states"]):
        (round_dir / f"initial_state_seed_{i}.json").write_text(json.dumps(state, indent=2))

    # Save observations per seed
    for seed_idx, observations in all_observations.items():
        obs_file = round_dir / f"observations_seed_{seed_idx}.json"
        obs_file.write_text(json.dumps(observations, indent=2))

    # Save learned transition model
    np.save(round_dir / "transition_priors.npy", transition_priors)

    print(f"  Data saved to {round_dir}/")


def save_predictions(round_number, seed_idx, prediction):
    """Save a prediction tensor to disk."""
    round_dir = DATA_DIR / f"round_{round_number}"
    round_dir.mkdir(parents=True, exist_ok=True)
    np.save(round_dir / f"prediction_seed_{seed_idx}.npy", prediction)


# ─── API helpers ───────────────────────────────────────────────────────────────

def get_session(token: str) -> requests.Session:
    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {token}"
    return s


def get_active_round(session: requests.Session):
    rounds = session.get(f"{BASE}/astar-island/rounds").json()
    active = next((r for r in rounds if r["status"] == "active"), None)
    if not active:
        print("No active round! Listing all rounds:")
        for r in rounds:
            print(f"  Round {r.get('round_number', '?')}: {r['status']} (id: {r['id'][:8]}...)")
        return None
    return active


def get_round_details(session: requests.Session, round_id: str):
    return session.get(f"{BASE}/astar-island/rounds/{round_id}").json()


def check_budget(session: requests.Session):
    resp = session.get(f"{BASE}/astar-island/budget").json()
    print(f"Budget: {resp['queries_used']}/{resp['queries_max']} queries used")
    return resp


def observe(session: requests.Session, round_id: str, seed_index: int,
            vx: int, vy: int, vw: int = 15, vh: int = 15):
    """Run one simulation observation through a viewport."""
    resp = session.post(f"{BASE}/astar-island/simulate", json={
        "round_id": round_id,
        "seed_index": seed_index,
        "viewport_x": vx,
        "viewport_y": vy,
        "viewport_w": vw,
        "viewport_h": vh,
    })
    if resp.status_code == 429:
        print(f"  Rate limited, waiting 1s...")
        time.sleep(1)
        return observe(session, round_id, seed_index, vx, vy, vw, vh)
    if resp.status_code != 200:
        print(f"  Observe error {resp.status_code}: {resp.text[:200]}")
        return None
    return resp.json()


def submit_prediction(session: requests.Session, round_id: str, seed_index: int,
                      prediction: np.ndarray, max_retries: int = 3):
    """Submit H x W x 6 prediction tensor with retry on rate limit."""
    for attempt in range(max_retries):
        resp = session.post(f"{BASE}/astar-island/submit", json={
            "round_id": round_id,
            "seed_index": seed_index,
            "prediction": prediction.tolist(),
        })
        if resp.status_code == 200:
            print(f"  Seed {seed_index}: SUBMITTED OK")
            return resp
        elif resp.status_code == 429:
            wait = 2 ** attempt
            print(f"  Seed {seed_index}: rate limited, retrying in {wait}s...")
            time.sleep(wait)
        else:
            print(f"  Seed {seed_index}: SUBMIT FAILED {resp.status_code} - {resp.text[:200]}")
            return resp
    print(f"  Seed {seed_index}: FAILED after {max_retries} retries")
    return resp


# ─── Phase 0: Analyze initial states ──────────────────────────────────────────

def classify_cells(initial_grid, width, height):
    """
    Build a priority mask from the initial grid.
    Priority 3: settlement/port/ruin cells
    Priority 1: cells within DYNAMIC_RADIUS of any priority-3 cell
    Priority 0: static cells (no observation needed)
    """
    priority = np.zeros((height, width), dtype=int)

    # Mark settlement/port/ruin cells as high priority
    dynamic_positions = []
    for y in range(height):
        for x in range(width):
            cls = TERRAIN_TO_CLASS.get(initial_grid[y][x], 0)
            if cls in (1, 2, 3):  # Settlement, Port, Ruin
                priority[y][x] = 3
                dynamic_positions.append((x, y))

    # Expand dynamic zone around settlements
    for dx, dy in dynamic_positions:
        for oy in range(max(0, dy - DYNAMIC_RADIUS), min(height, dy + DYNAMIC_RADIUS + 1)):
            for ox in range(max(0, dx - DYNAMIC_RADIUS), min(width, dx + DYNAMIC_RADIUS + 1)):
                if priority[oy][ox] == 0:
                    priority[oy][ox] = 1

    return priority


def compute_optimal_viewports(priority, width, height, max_viewports=5):
    """
    Greedy set-cover: place 15x15 viewports to cover the most dynamic cells.
    Returns list of (vx, vy, vw, vh) tuples.
    """
    mask = priority.copy()
    viewports = []

    for _ in range(max_viewports):
        best_score = 0
        best_pos = None

        for vy in range(max(1, height - 14)):
            for vx in range(max(1, width - 14)):
                vw = min(15, width - vx)
                vh = min(15, height - vy)
                score = mask[vy:vy + vh, vx:vx + vw].sum()
                if score > best_score:
                    best_score = score
                    best_pos = (vx, vy, vw, vh)

        if best_score == 0 or best_pos is None:
            break

        viewports.append(best_pos)
        vx, vy, vw, vh = best_pos
        mask[vy:vy + vh, vx:vx + vw] = 0

    return viewports


def allocate_query_budget(seed_plans, total_budget):
    """Distribute queries across seeds proportional to dynamic cell count."""
    n_seeds = len(seed_plans)
    min_per_seed = max(1, total_budget // (n_seeds * 2))
    remaining = total_budget - min_per_seed * n_seeds

    dynamic_counts = []
    for _, _, priority in seed_plans:
        dynamic_counts.append(int((priority > 0).sum()))

    total_dynamic = sum(dynamic_counts) or 1
    budgets = []
    for i in range(n_seeds):
        extra = int(remaining * dynamic_counts[i] / total_dynamic)
        budgets.append(min_per_seed + extra)

    leftover = total_budget - sum(budgets)
    for i in range(leftover):
        budgets[i % n_seeds] += 1

    return budgets


def allocate_queries_to_viewports(viewports, priority, budget):
    """Distribute a seed's query budget across its viewports."""
    if not viewports:
        return []

    scores = []
    for vx, vy, vw, vh in viewports:
        score = priority[vy:vy + vh, vx:vx + vw].sum()
        scores.append(max(score, 1))

    total_score = sum(scores)
    allocation = []
    assigned = 0
    for i, (vp, score) in enumerate(zip(viewports, scores)):
        count = max(1, int(budget * score / total_score))
        allocation.append((vp, count))
        assigned += count

    leftover = budget - assigned
    sorted_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
    for i in range(max(0, leftover)):
        idx = sorted_indices[i % len(sorted_indices)]
        vp, count = allocation[idx]
        allocation[idx] = (vp, count + 1)

    return allocation


# ─── Feature-based prior model ────────────────────────────────────────────────

def load_feature_prior_model():
    """Load the feature-based prior model built from ground truth."""
    model_file = MODELS_DIR / "feature_prior_model.npz"
    if not model_file.exists():
        print("  WARNING: feature_prior_model.npz not found, falling back to uniform")
        return None, None

    data = np.load(model_file)

    full_model = {}
    for key, val in zip(data["full_keys"], data["full_vals"]):
        full_model[tuple(key)] = val

    simple_model = {}
    for key, val in zip(data["simple_keys"], data["simple_vals"]):
        simple_model[tuple(key)] = val

    print(f"  Loaded feature prior model: {len(full_model)} full + {len(simple_model)} simple buckets")
    return full_model, simple_model


def compute_cell_features(grid, y, x, H, W, settlement_positions):
    """Compute features for a cell: (initial_cls, dist, n_sn, n_r5, ocean, n_forest)."""
    initial_cls = TERRAIN_TO_CLASS.get(grid[y][x], 0)

    n_sn = 0
    n_forest_n = 0
    has_ocean = False
    for dy in [-1, 0, 1]:
        for dx in [-1, 0, 1]:
            if dy == 0 and dx == 0:
                continue
            ny, nx = y + dy, x + dx
            if 0 <= ny < H and 0 <= nx < W:
                ncls = TERRAIN_TO_CLASS.get(grid[ny][nx], 0)
                if ncls in (1, 2):
                    n_sn += 1
                if ncls == 4:
                    n_forest_n += 1
                if grid[ny][nx] == 10:
                    has_ocean = True

    min_dist = 999
    n_r5 = 0
    for sy, sx in settlement_positions:
        dist = abs(y - sy) + abs(x - sx)
        if dist < min_dist:
            min_dist = dist
        if dist <= 5:
            n_r5 += 1

    return (initial_cls, min(min_dist, 15), min(n_sn, 3), min(n_r5, 6),
            1 if has_ocean else 0, min(n_forest_n, 4))


def get_feature_prior(grid, y, x, H, W, settlement_positions, full_model, simple_model):
    """Get prior distribution for a cell using the feature-based model."""
    initial_cls = TERRAIN_TO_CLASS.get(grid[y][x], 0)
    feat = compute_cell_features(grid, y, x, H, W, settlement_positions)

    if full_model and feat in full_model:
        return full_model[feat].copy()

    simple_key = (feat[0], feat[1])
    if simple_model and simple_key in simple_model:
        return simple_model[simple_key].copy()

    # Last resort fallback
    prior = np.full(NUM_CLASSES, 0.005)
    prior[initial_cls] = 0.97
    prior /= prior.sum()
    return prior


def compute_all_priors(grid, H, W, full_model, simple_model):
    """Compute feature-based priors for all cells. Returns H×W×6 array."""
    settlement_positions = []
    for sy in range(H):
        for sx in range(W):
            if TERRAIN_TO_CLASS.get(grid[sy][sx], 0) in (1, 2):
                settlement_positions.append((sy, sx))

    priors = np.zeros((H, W, NUM_CLASSES), dtype=np.float64)
    for y in range(H):
        for x in range(W):
            priors[y][x] = get_feature_prior(grid, y, x, H, W, settlement_positions,
                                              full_model, simple_model)
    return priors


# ─── Cross-seed global adjustment ─────────────────────────────────────────────

def compute_global_adjustments(grids, all_observations, H, W, priors_per_seed):
    """
    Pool observations across ALL seeds to compute per-initial-class scaling factors.
    This detects the round's "regime" (harsh vs expansion) from observed transition rates.
    Returns NUM_CLASSES × NUM_CLASSES adjustment matrix.
    """
    obs_class_dist = np.zeros((NUM_CLASSES, NUM_CLASSES))
    obs_class_counts = np.zeros(NUM_CLASSES)
    prior_class_dist = np.zeros((NUM_CLASSES, NUM_CLASSES))
    prior_class_counts = np.zeros(NUM_CLASSES)

    for seed_idx in range(len(grids)):
        grid = grids[seed_idx]
        observations = all_observations.get(seed_idx, [])
        if not observations or grid is None:
            continue
        priors = priors_per_seed[seed_idx]

        for obs in observations:
            vp = obs["viewport"]
            og = obs["grid"]
            vx, vy = vp["x"], vp["y"]
            for ry, row in enumerate(og):
                for rx, val in enumerate(row):
                    gx, gy = vx + rx, vy + ry
                    if 0 <= gx < W and 0 <= gy < H:
                        initial_cls = TERRAIN_TO_CLASS.get(grid[gy][gx], 0)
                        final_cls = TERRAIN_TO_CLASS.get(val, 0)
                        obs_class_dist[initial_cls][final_cls] += 1
                        obs_class_counts[initial_cls] += 1
                        prior_class_dist[initial_cls] += priors[gy][gx]
                        prior_class_counts[initial_cls] += 1

    # Normalize
    for cls in range(NUM_CLASSES):
        if obs_class_counts[cls] > 0:
            obs_class_dist[cls] /= obs_class_counts[cls]
        if prior_class_counts[cls] > 0:
            prior_class_dist[cls] /= prior_class_counts[cls]

    # Compute adjustment ratios: observed / predicted
    adjustments = np.ones((NUM_CLASSES, NUM_CLASSES))
    for init_cls in range(NUM_CLASSES):
        if obs_class_counts[init_cls] >= 20:
            for final_cls in range(NUM_CLASSES):
                if prior_class_dist[init_cls][final_cls] > 0.01:
                    ratio = obs_class_dist[init_cls][final_cls] / prior_class_dist[init_cls][final_cls]
                    adjustments[init_cls][final_cls] = np.clip(ratio, 0.2, 5.0)

    # Log detected regime
    ss_obs = obs_class_dist[1][1] if obs_class_counts[1] > 0 else -1
    es_obs = obs_class_dist[0][1] if obs_class_counts[0] > 0 else -1
    print(f"  Regime detection: S→S={ss_obs:.3f}, E→S={es_obs:.3f} "
          f"(S_obs={int(obs_class_counts[1])}, E_obs={int(obs_class_counts[0])})")
    if ss_obs >= 0:
        if ss_obs < 0.15:
            print(f"  Detected regime: HARSH (settlements mostly die)")
        elif ss_obs < 0.40:
            print(f"  Detected regime: MODERATE")
        else:
            print(f"  Detected regime: EXPANSION (settlements thrive)")

    return adjustments


# ─── Phase 2: Cross-seed transition learning ─────────────────────────────────

def learn_transition_model(seed_plans, all_observations, width, height):
    """Pool observations across all seeds to learn P(final_class | initial_class)."""
    transitions = np.ones((NUM_CLASSES, NUM_CLASSES), dtype=np.float64)

    for seed_idx, (grid, _, _) in enumerate(seed_plans):
        for obs in all_observations.get(seed_idx, []):
            viewport = obs["viewport"]
            obs_grid = obs["grid"]
            vx, vy = viewport["x"], viewport["y"]

            for ry, row in enumerate(obs_grid):
                for rx, cell_val in enumerate(row):
                    gx = vx + rx
                    gy = vy + ry
                    if 0 <= gx < width and 0 <= gy < height:
                        initial_cls = TERRAIN_TO_CLASS.get(grid[gy][gx], 0)
                        final_cls = TERRAIN_TO_CLASS.get(cell_val, 0)
                        transitions[initial_cls][final_cls] += 1

    row_sums = transitions.sum(axis=1, keepdims=True)
    transition_probs = transitions / row_sums
    return transition_probs


# ─── Phase 3: Prediction building (v3) ───────────────────────────────────────

def build_prediction_v3(width, height, initial_grid, observations,
                        priors, adjustments, concentration=CONCENTRATION):
    """
    Build H×W×6 probability tensor using feature-based priors + global adjustment.

    For each cell:
    1. Start with feature-based prior (trained on 17 rounds of ground truth)
    2. Apply global adjustment (learned from this round's cross-seed observations)
    3. If cell has direct observations: Bayesian update with Dirichlet posterior
    """
    # Count per-cell observations
    obs_counts = np.zeros((height, width, NUM_CLASSES), dtype=np.float64)
    obs_total = np.zeros((height, width), dtype=np.float64)

    for obs in observations:
        viewport = obs["viewport"]
        grid = obs["grid"]
        vx, vy = viewport["x"], viewport["y"]

        for ry, row in enumerate(grid):
            for rx, cell_val in enumerate(row):
                gx = vx + rx
                gy = vy + ry
                if 0 <= gx < width and 0 <= gy < height:
                    cls = TERRAIN_TO_CLASS.get(cell_val, 0)
                    obs_counts[gy][gx][cls] += 1
                    obs_total[gy][gx] += 1

    prediction = np.zeros((height, width, NUM_CLASSES), dtype=np.float64)

    for y in range(height):
        for x in range(width):
            initial_cls = TERRAIN_TO_CLASS.get(initial_grid[y][x], 0)

            # Apply global adjustment to feature prior
            adjusted_prior = priors[y][x] * adjustments[initial_cls]
            adjusted_prior = np.maximum(adjusted_prior, 0.003)
            adjusted_prior /= adjusted_prior.sum()

            # Blend adjusted and unadjusted priors
            blended = OBS_WEIGHT * adjusted_prior + (1 - OBS_WEIGHT) * priors[y][x]

            n_obs = obs_total[y][x]
            if n_obs > 0:
                # Bayesian update: Dirichlet posterior = prior × concentration + observations
                alpha = blended * concentration
                posterior = obs_counts[y][x] + alpha
                prediction[y][x] = posterior / posterior.sum()
            else:
                prediction[y][x] = blended

    # Enforce probability floor
    prediction = np.maximum(prediction, MIN_FLOOR)
    prediction = prediction / prediction.sum(axis=-1, keepdims=True)

    return prediction


# ─── Main game loop ───────────────────────────────────────────────────────────

def play_round(session: requests.Session, round_id: str, detail: dict, round_number: int = 0):
    width = detail["map_width"]
    height = detail["map_height"]
    seeds_count = detail["seeds_count"]
    initial_states = detail["initial_states"]

    print(f"\nMap: {width}x{height}, Seeds: {seeds_count}")

    # Check budget
    budget = check_budget(session)
    queries_left = budget["queries_max"] - budget["queries_used"]
    print(f"Queries available: {queries_left}")

    # ── Load feature-based prior model ──
    print("\n=== Loading models ===")
    full_model, simple_model = load_feature_prior_model()

    # ── Phase 0: Analyze all seeds ──
    print("\n=== Phase 0: Analyzing initial states ===")
    seed_plans = []  # (grid, viewports, priority)
    grids = []
    all_priors = []

    for seed_idx in range(seeds_count):
        grid = initial_states[seed_idx]["grid"]
        priority = classify_cells(grid, width, height)
        viewports = compute_optimal_viewports(priority, width, height, max_viewports=5)
        seed_plans.append((grid, viewports, priority))
        grids.append(grid)

        # Compute feature priors for this seed
        priors = compute_all_priors(grid, height, width, full_model, simple_model)
        all_priors.append(priors)

        n_dynamic = int((priority > 0).sum())
        n_settlements = int((priority == 3).sum())
        print(f"  Seed {seed_idx}: {n_settlements} settlement cells, "
              f"{n_dynamic} dynamic cells, {len(viewports)} viewports needed")

    if queries_left == 0:
        print("\nNo queries left! Submitting with feature priors only.")
        identity_adj = np.ones((NUM_CLASSES, NUM_CLASSES))
        for seed_idx in range(seeds_count):
            pred = build_prediction_v3(width, height, grids[seed_idx], [],
                                        all_priors[seed_idx], identity_adj)
            save_predictions(round_number, seed_idx, pred)
            submit_prediction(session, round_id, seed_idx, pred)
        return

    # Distribute budget across seeds
    query_budgets = allocate_query_budget(seed_plans, queries_left)
    print(f"\nQuery budget per seed: {query_budgets}")

    # ── Phase 1: Observe all seeds ──
    print("\n=== Phase 1: Observing dynamic areas ===")
    all_observations = {}

    for seed_idx in range(seeds_count):
        grid, viewports, priority = seed_plans[seed_idx]
        seed_budget = query_budgets[seed_idx]
        print(f"\n--- Seed {seed_idx} (budget: {seed_budget}) ---")

        if not viewports:
            print("  No dynamic areas found, skipping observations")
            all_observations[seed_idx] = []
            continue

        allocation = allocate_queries_to_viewports(viewports, priority, seed_budget)
        observations = []
        query_count = 0

        for (vx, vy, vw, vh), count in allocation:
            print(f"  Viewport ({vx},{vy}) {vw}x{vh}: {count} observations")
            for i in range(count):
                result = observe(session, round_id, seed_idx, vx, vy, vw, vh)
                if result:
                    observations.append(result)
                    query_count += 1
                time.sleep(0.22)  # Stay under 5 req/s rate limit

        all_observations[seed_idx] = observations
        print(f"  Total: {query_count} queries used, {len(observations)} observations")

    # ── Phase 2: Cross-seed learning ──
    print("\n=== Phase 2: Cross-seed regime detection ===")
    transition_priors = learn_transition_model(seed_plans, all_observations, width, height)

    print("  Transition probabilities (initial → final):")
    for i, cls_name in enumerate(CLASSES):
        top3 = sorted(range(NUM_CLASSES), key=lambda c: transition_priors[i][c], reverse=True)[:3]
        top3_str = ", ".join(f"{CLASSES[c]}={transition_priors[i][c]:.2f}" for c in top3)
        print(f"    {cls_name:10s} → {top3_str}")

    # Compute global adjustments from pooled observations
    print("\n  Computing global adjustments...")
    adjustments = compute_global_adjustments(grids, all_observations, height, width, all_priors)

    # ── Save observation data ──
    print("\n=== Saving round data ===")
    save_round_data(round_number, round_id, detail, all_observations, transition_priors)

    # ── Phase 3: Build and submit predictions ──
    print("\n=== Phase 3: Building and submitting predictions ===")
    for seed_idx in range(seeds_count):
        pred = build_prediction_v3(
            width, height, grids[seed_idx],
            all_observations[seed_idx],
            all_priors[seed_idx],
            adjustments,
        )

        save_predictions(round_number, seed_idx, pred)
        submit_prediction(session, round_id, seed_idx, pred)

    print("\n=== All seeds submitted! ===")


# ─── CLI ───────────────────────────────────────────────────────────────────────

def load_env():
    """Load .env file from script directory."""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    os.environ.setdefault(key.strip(), val.strip())


def main():
    load_env()
    parser = argparse.ArgumentParser(description="Astar Island round player (v3)")
    parser.add_argument("--token", default=os.environ.get("ASTAR_TOKEN"),
                        help="JWT token (default: from .env)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Just check round status, don't play")
    args = parser.parse_args()

    if not args.token:
        print("Error: No token. Pass --token or set ASTAR_TOKEN in .env")
        return

    session = get_session(args.token)

    # Find active round
    active = get_active_round(session)
    if not active:
        print("\nNo active round. Wait for the next one!")
        return

    round_id = active["id"]
    print(f"Active round: #{active.get('round_number', '?')}")
    print(f"Round ID: {round_id}")
    if "closes_at" in active:
        print(f"Closes at: {active['closes_at']}")

    if args.dry_run:
        check_budget(session)
        return

    # Get full round details
    round_number = active.get("round_number", 0)
    detail = get_round_details(session, round_id)
    play_round(session, round_id, detail, round_number)


if __name__ == "__main__":
    main()
