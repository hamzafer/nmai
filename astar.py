"""
Astar Island — Round Player (v2: Bayesian + Cross-Seed Learning)
Usage: python astar.py [--token YOUR_JWT_TOKEN] [--dry-run]
       Reads ASTAR_TOKEN from .env if --token not provided.
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

# Static terrain types that won't change
STATIC_CLASSES = {0, 4, 5}  # Empty/Ocean/Plains, Forest, Mountain

DYNAMIC_RADIUS = 7  # cells around settlements considered dynamic

DATA_DIR = Path(__file__).parent / "data"
MODELS_DIR = Path(__file__).parent / "models"


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

        # Try every possible viewport position
        for vy in range(max(1, height - 14)):
            for vx in range(max(1, width - 14)):
                vw = min(15, width - vx)
                vh = min(15, height - vy)
                score = mask[vy:vy + vh, vx:vx + vw].sum()
                if score > best_score:
                    best_score = score
                    best_pos = (vx, vy, vw, vh)

        if best_score == 0 or best_pos is None:
            break  # No more dynamic cells to cover

        viewports.append(best_pos)
        vx, vy, vw, vh = best_pos
        mask[vy:vy + vh, vx:vx + vw] = 0  # Zero out covered cells

    return viewports


def allocate_query_budget(seed_plans, total_budget):
    """
    Distribute queries across seeds proportional to dynamic cell count.
    Minimum 7 per seed, rest distributed proportionally.
    """
    n_seeds = len(seed_plans)
    min_per_seed = max(1, total_budget // (n_seeds * 2))  # at least ~5
    remaining = total_budget - min_per_seed * n_seeds

    # Weight by number of dynamic cells
    dynamic_counts = []
    for _, _, priority in seed_plans:
        dynamic_counts.append(int((priority > 0).sum()))

    total_dynamic = sum(dynamic_counts) or 1
    budgets = []
    for i in range(n_seeds):
        extra = int(remaining * dynamic_counts[i] / total_dynamic)
        budgets.append(min_per_seed + extra)

    # Distribute any rounding remainder
    leftover = total_budget - sum(budgets)
    for i in range(leftover):
        budgets[i % n_seeds] += 1

    return budgets


def allocate_queries_to_viewports(viewports, priority, budget):
    """
    Distribute a seed's query budget across its viewports,
    weighted by the priority score each viewport covers.
    """
    if not viewports:
        return []

    # Score each viewport by its dynamic cell coverage
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

    # Distribute remainder to highest-scoring viewports
    leftover = budget - assigned
    sorted_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
    for i in range(max(0, leftover)):
        idx = sorted_indices[i % len(sorted_indices)]
        vp, count = allocation[idx]
        allocation[idx] = (vp, count + 1)

    return allocation


# ─── Phase 2: Cross-seed learning ─────────────────────────────────────────────

def learn_transition_model(seed_plans, all_observations, width, height):
    """
    Pool observations across all seeds to learn P(final_class | initial_class).
    Returns a NUM_CLASSES x NUM_CLASSES matrix where row = initial, col = final.
    """
    # Count transitions: transitions[initial_cls][final_cls] = count
    transitions = np.ones((NUM_CLASSES, NUM_CLASSES), dtype=np.float64)  # Laplace smoothing

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

    # Normalize rows to get probabilities
    row_sums = transitions.sum(axis=1, keepdims=True)
    transition_probs = transitions / row_sums

    return transition_probs


# ─── Phase 3: Bayesian prediction building ────────────────────────────────────

def count_observations(observations, width, height):
    """Count terrain class occurrences per cell from observations."""
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

    return obs_counts, obs_total


def load_cumulative_priors():
    """Load cumulative models from past rounds if available."""
    transition_file = MODELS_DIR / "transition_model.npy"
    neighborhood_file = MODELS_DIR / "neighborhood_model.npy"

    cumulative_transition = None
    neighborhood_model = None

    if transition_file.exists():
        cumulative_transition = np.load(transition_file)
        print(f"  Loaded cumulative transition model from {len(list(MODELS_DIR.glob('*.npy')))} model files")
    if neighborhood_file.exists():
        neighborhood_model = np.load(neighborhood_file)
        print(f"  Loaded neighborhood model")

    return cumulative_transition, neighborhood_model


def count_settlement_neighbors(initial_grid, x, y, width, height):
    """Count settlement/port neighbors (8-connected) for a cell."""
    count = 0
    for dy in [-1, 0, 1]:
        for dx in [-1, 0, 1]:
            if dy == 0 and dx == 0:
                continue
            ny, nx = y + dy, x + dx
            if 0 <= ny < height and 0 <= nx < width:
                ncls = TERRAIN_TO_CLASS.get(initial_grid[ny][nx], 0)
                if ncls in (1, 2):  # Settlement or Port
                    count += 1
    return min(count, 8)


def build_prediction_bayesian(width, height, initial_grid, observations,
                               transition_priors, concentration=20.0,
                               cumulative_priors=None, neighborhood_model=None):
    """
    Build H x W x 6 probability tensor using Bayesian estimation.

    - Static cells: high confidence on initial class
    - Dynamic cells with observations: Dirichlet-multinomial posterior
    - Dynamic cells without observations: neighborhood-aware or cross-seed priors
    """
    obs_counts, obs_total = count_observations(observations, width, height)
    prediction = np.zeros((height, width, NUM_CLASSES), dtype=np.float64)

    for y in range(height):
        for x in range(width):
            initial_cls = TERRAIN_TO_CLASS.get(initial_grid[y][x], 0)
            n_obs = obs_total[y][x]

            if initial_cls in STATIC_CLASSES and n_obs == 0:
                # Static cell, no observations needed — high confidence
                prediction[y][x][initial_cls] = 0.97
                leftover = 0.03 / (NUM_CLASSES - 1)
                for c in range(NUM_CLASSES):
                    if c != initial_cls:
                        prediction[y][x][c] = leftover

            elif n_obs > 0:
                # Bayesian: Dirichlet posterior = prior + observations
                # Use best available prior
                if cumulative_priors is not None:
                    alpha = cumulative_priors[initial_cls] * concentration
                else:
                    alpha = transition_priors[initial_cls] * concentration
                posterior = obs_counts[y][x] + alpha
                prediction[y][x] = posterior / posterior.sum()

            else:
                # Dynamic cell with no observations
                # Use neighborhood model if available (best), else transition priors
                if neighborhood_model is not None:
                    n_neighbors = count_settlement_neighbors(
                        initial_grid, x, y, width, height
                    )
                    prediction[y][x] = neighborhood_model[initial_cls][n_neighbors]
                elif cumulative_priors is not None:
                    prediction[y][x] = cumulative_priors[initial_cls]
                else:
                    prediction[y][x] = transition_priors[initial_cls]

    # Enforce minimum probability floor to avoid infinite KL divergence
    min_floor = 0.005
    prediction = np.maximum(prediction, min_floor)
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

    # ── Load cumulative priors from past rounds ──
    cumulative_priors, neighborhood_model = load_cumulative_priors()

    # ── Phase 0: Analyze all seeds ──
    print("\n=== Phase 0: Analyzing initial states ===")
    seed_plans = []  # (grid, viewports, priority)
    for seed_idx in range(seeds_count):
        grid = initial_states[seed_idx]["grid"]
        priority = classify_cells(grid, width, height)
        viewports = compute_optimal_viewports(priority, width, height, max_viewports=5)
        seed_plans.append((grid, viewports, priority))

        n_dynamic = int((priority > 0).sum())
        n_settlements = int((priority == 3).sum())
        print(f"  Seed {seed_idx}: {n_settlements} settlement cells, "
              f"{n_dynamic} dynamic cells, {len(viewports)} viewports needed")

    if queries_left == 0:
        print("\nNo queries left! Submitting with cross-seed priors only.")
        transition_priors = np.full((NUM_CLASSES, NUM_CLASSES), 1.0 / NUM_CLASSES)
        for seed_idx in range(seeds_count):
            grid = seed_plans[seed_idx][0]
            pred = build_prediction_bayesian(width, height, grid, [],
                                              transition_priors,
                                              cumulative_priors=cumulative_priors,
                                              neighborhood_model=neighborhood_model)
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
    print("\n=== Phase 2: Learning transition model ===")
    transition_priors = learn_transition_model(seed_plans, all_observations, width, height)

    print("  Transition probabilities (initial → final):")
    for i, cls_name in enumerate(CLASSES):
        top3 = sorted(range(NUM_CLASSES), key=lambda c: transition_priors[i][c], reverse=True)[:3]
        top3_str = ", ".join(f"{CLASSES[c]}={transition_priors[i][c]:.2f}" for c in top3)
        print(f"    {cls_name:10s} → {top3_str}")

    # ── Save observation data ──
    print("\n=== Saving round data ===")
    save_round_data(round_number, round_id, detail, all_observations, transition_priors)

    # NOTE: Local simulator blending disabled — it was degrading scores
    # (Round 3-4 scored ~62 without it, Round 5-7 scored ~20 with it)
    # TODO: Re-enable once simulator is properly calibrated against ground truth
    sim_predictions = {}

    # ── Phase 3: Build and submit predictions ──
    print("\n=== Phase 3: Building and submitting predictions ===")
    for seed_idx in range(seeds_count):
        grid = seed_plans[seed_idx][0]

        # Build Bayesian prediction from observations
        pred = build_prediction_bayesian(
            width, height, grid,
            all_observations[seed_idx],
            transition_priors,
            cumulative_priors=cumulative_priors,
            neighborhood_model=neighborhood_model,
        )

        # Blend with simulator predictions if available
        if seed_idx in sim_predictions:
            sim_pred = sim_predictions[seed_idx]
            # Weight: observations are more trustworthy for observed cells,
            # simulator fills in unobserved cells
            obs_counts, obs_total = count_observations(
                all_observations[seed_idx], width, height
            )
            for y in range(height):
                for x in range(width):
                    if obs_total[y][x] == 0:
                        # No observations — use 70% sim, 30% prior
                        pred[y][x] = 0.7 * sim_pred[y][x] + 0.3 * pred[y][x]
                    else:
                        # Have observations — use 30% sim, 70% observation-based
                        pred[y][x] = 0.3 * sim_pred[y][x] + 0.7 * pred[y][x]

            # Re-floor and renormalize
            pred = np.maximum(pred, 0.005)
            pred = pred / pred.sum(axis=-1, keepdims=True)

        save_predictions(round_number, seed_idx, pred)
        submit_prediction(session, round_id, seed_idx, pred)

    print("\n=== All seeds submitted! ===")


# ─── CLI ───────────────────────────────────────────────────────────────────────

def load_env():
    """Load .env file from script directory."""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    os.environ.setdefault(key.strip(), val.strip())


def main():
    load_env()
    parser = argparse.ArgumentParser(description="Astar Island round player (v2)")
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
