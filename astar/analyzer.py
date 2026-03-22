"""
Post-Round Analyzer — Pull ground truth, compare predictions, build cumulative models.
Usage: python analyze.py [--round N] [--all]
"""

import argparse
import json
import os
import numpy as np
from pathlib import Path

BASE = "https://api.ainm.no"
DATA_DIR = Path(__file__).parent.parent / "data"
MODELS_DIR = Path(__file__).parent.parent / "models"

NUM_CLASSES = 6
CLASSES = ["Empty", "Settlement", "Port", "Ruin", "Forest", "Mountain"]

TERRAIN_TO_CLASS = {
    0: 0, 10: 0, 11: 0,  # Empty/Ocean/Plains
    1: 1,  # Settlement
    2: 2,  # Port
    3: 3,  # Ruin
    4: 4,  # Forest
    5: 5,  # Mountain
}


def get_session():
    import requests
    env_path = Path(__file__).parent.parent / ".env"
    token = None
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if "ASTAR_TOKEN" in line and "=" in line:
                token = line.split("=", 1)[1].strip()
    if not token:
        token = os.environ.get("ASTAR_TOKEN")
    if not token:
        raise ValueError("No token found in .env or ASTAR_TOKEN env var")
    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {token}"
    return s


def get_completed_rounds(session):
    """Get all rounds with their status."""
    my_rounds = session.get(f"{BASE}/astar-island/my-rounds").json()
    return my_rounds


def pull_ground_truth(session, round_info):
    """Pull ground truth for a completed round and save to disk."""
    round_num = round_info.get("round_number", 0)
    round_id = round_info["id"]
    status = round_info.get("status", "")
    seeds_submitted = round_info.get("seeds_submitted", 0)

    round_dir = DATA_DIR / f"round_{round_num}"
    round_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n=== Round {round_num} (status: {status}, seeds submitted: {seeds_submitted}) ===")

    if status not in ("completed", "scoring"):
        print(f"  Round not completed yet (status: {status}), skipping ground truth pull")
        return None

    # Save round result
    (round_dir / "my_round_result.json").write_text(json.dumps(round_info, indent=2))

    score = round_info.get("round_score")
    rank = round_info.get("rank")
    total = round_info.get("total_teams")
    seed_scores = round_info.get("seed_scores")
    print(f"  Score: {score}")
    print(f"  Rank: {rank}/{total}")
    print(f"  Seed scores: {seed_scores}")

    # Pull ground truth analysis per seed
    seeds_count = round_info.get("seeds_count", 5)
    ground_truths = []

    for seed_idx in range(seeds_count):
        analysis_file = round_dir / f"analysis_seed_{seed_idx}.json"

        # Check if already downloaded
        if analysis_file.exists():
            data = json.loads(analysis_file.read_text())
            ground_truths.append(data)
            print(f"  Seed {seed_idx}: score={data.get('score', 'N/A')} (cached)")
            continue

        resp = session.get(f"{BASE}/astar-island/analysis/{round_id}/{seed_idx}")
        if resp.status_code == 200:
            data = resp.json()
            analysis_file.write_text(json.dumps(data, indent=2))
            ground_truths.append(data)

            # Save ground truth tensor as numpy
            if "ground_truth" in data:
                gt = np.array(data["ground_truth"])
                np.save(round_dir / f"ground_truth_seed_{seed_idx}.npy", gt)

            print(f"  Seed {seed_idx}: score={data.get('score', 'N/A')} (downloaded)")
        else:
            print(f"  Seed {seed_idx}: analysis not available ({resp.status_code})")
            ground_truths.append(None)

    # Pull initial states if not already saved
    for seed_idx in range(seeds_count):
        init_file = round_dir / f"initial_state_seed_{seed_idx}.json"
        if not init_file.exists():
            detail = session.get(f"{BASE}/astar-island/rounds/{round_id}").json()
            for i, state in enumerate(detail.get("initial_states", [])):
                (round_dir / f"initial_state_seed_{i}.json").write_text(
                    json.dumps(state, indent=2)
                )
            break  # All seeds saved at once

    return ground_truths


def compute_analysis(round_num):
    """Compare our predictions against ground truth for a completed round."""
    round_dir = DATA_DIR / f"round_{round_num}"
    analysis_dir = round_dir / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n--- Analyzing Round {round_num} ---")

    transition_counts = np.zeros((NUM_CLASSES, NUM_CLASSES), dtype=np.float64)
    # Neighborhood-aware: [initial_cls][n_settlement_neighbors][final_cls]
    # n_settlement_neighbors: 0-8
    neighborhood_counts = np.zeros((NUM_CLASSES, 9, NUM_CLASSES), dtype=np.float64)

    for seed_idx in range(5):
        gt_file = round_dir / f"ground_truth_seed_{seed_idx}.npy"
        init_file = round_dir / f"initial_state_seed_{seed_idx}.json"
        pred_file = round_dir / f"prediction_seed_{seed_idx}.npy"

        if not gt_file.exists() or not init_file.exists():
            print(f"  Seed {seed_idx}: missing data, skipping")
            continue

        gt = np.load(gt_file)  # H x W x 6 probability tensor
        initial_state = json.loads(init_file.read_text())
        initial_grid = initial_state["grid"]
        height, width = len(initial_grid), len(initial_grid[0])

        # Count transitions from initial → ground truth (most likely class)
        for y in range(height):
            for x in range(width):
                initial_cls = TERRAIN_TO_CLASS.get(initial_grid[y][x], 0)
                # Ground truth is a probability distribution, use it directly
                gt_dist = gt[y][x]

                # Weighted transitions (fractional counts from probability distributions)
                for final_cls in range(NUM_CLASSES):
                    transition_counts[initial_cls][final_cls] += gt_dist[final_cls]

                # Count settlement neighbors in initial state
                n_settlement_neighbors = 0
                for dy in [-1, 0, 1]:
                    for dx in [-1, 0, 1]:
                        if dy == 0 and dx == 0:
                            continue
                        ny, nx = y + dy, x + dx
                        if 0 <= ny < height and 0 <= nx < width:
                            ncls = TERRAIN_TO_CLASS.get(initial_grid[ny][nx], 0)
                            if ncls in (1, 2):  # Settlement or Port
                                n_settlement_neighbors += 1

                n_settlement_neighbors = min(n_settlement_neighbors, 8)
                for final_cls in range(NUM_CLASSES):
                    neighborhood_counts[initial_cls][n_settlement_neighbors][final_cls] += gt_dist[final_cls]

        # Per-cell KL divergence if we have predictions
        if pred_file.exists():
            pred = np.load(pred_file)
            # Compute KL divergence per cell
            eps = 1e-10
            kl = np.sum(gt * np.log((gt + eps) / (pred + eps)), axis=-1)
            np.save(analysis_dir / f"kl_per_cell_seed_{seed_idx}.npy", kl)

            mean_kl = np.mean(kl)
            max_kl = np.max(kl)
            # Entropy per cell
            entropy = -np.sum(gt * np.log(gt + eps), axis=-1)
            weighted_kl = np.sum(entropy * kl) / (np.sum(entropy) + eps)
            score = max(0, min(100, 100 * np.exp(-3 * weighted_kl)))

            print(f"  Seed {seed_idx}: mean_KL={mean_kl:.4f}, max_KL={max_kl:.4f}, "
                  f"weighted_KL={weighted_kl:.4f}, est_score={score:.1f}")
        else:
            print(f"  Seed {seed_idx}: no prediction file, skipping KL analysis")

    # Save transition counts
    np.save(analysis_dir / "transition_counts.npy", transition_counts)
    np.save(analysis_dir / "neighborhood_counts.npy", neighborhood_counts)

    # Print transition probabilities
    row_sums = transition_counts.sum(axis=1, keepdims=True)
    row_sums = np.where(row_sums == 0, 1, row_sums)
    transition_probs = transition_counts / row_sums

    print(f"\n  Transition probabilities (from ground truth):")
    for i, cls_name in enumerate(CLASSES):
        top = sorted(range(NUM_CLASSES), key=lambda c: transition_probs[i][c], reverse=True)[:3]
        top_str = ", ".join(f"{CLASSES[c]}={transition_probs[i][c]:.3f}" for c in top)
        print(f"    {cls_name:10s} → {top_str}")

    return transition_counts, neighborhood_counts


def build_cumulative_models():
    """Merge transition data from ALL completed rounds into cumulative models."""
    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    cumulative_transitions = np.ones((NUM_CLASSES, NUM_CLASSES), dtype=np.float64)  # Laplace
    cumulative_neighborhood = np.ones((NUM_CLASSES, 9, NUM_CLASSES), dtype=np.float64)
    rounds_used = []

    # Find all rounds with analysis
    for round_dir in sorted(DATA_DIR.glob("round_*")):
        analysis_dir = round_dir / "analysis"
        tc_file = analysis_dir / "transition_counts.npy"
        nc_file = analysis_dir / "neighborhood_counts.npy"

        if tc_file.exists():
            round_num = round_dir.name.split("_")[1]
            rounds_used.append(int(round_num))
            cumulative_transitions += np.load(tc_file)
            if nc_file.exists():
                cumulative_neighborhood += np.load(nc_file)

    if not rounds_used:
        print("\nNo analyzed rounds found. Run analysis first.")
        return

    # Normalize and save
    row_sums = cumulative_transitions.sum(axis=1, keepdims=True)
    row_sums = np.where(row_sums == 0, 1, row_sums)
    transition_model = cumulative_transitions / row_sums
    np.save(MODELS_DIR / "transition_model.npy", transition_model)

    # Normalize neighborhood model
    # Shape: [initial_cls, n_neighbors, final_cls]
    neighbor_sums = cumulative_neighborhood.sum(axis=2, keepdims=True)
    neighbor_sums = np.where(neighbor_sums == 0, 1, neighbor_sums)
    neighborhood_model = cumulative_neighborhood / neighbor_sums
    np.save(MODELS_DIR / "neighborhood_model.npy", neighborhood_model)

    print(f"\n=== Cumulative models built from rounds: {rounds_used} ===")
    print(f"  Saved: models/transition_model.npy")
    print(f"  Saved: models/neighborhood_model.npy")

    print(f"\n  Cumulative transition probabilities:")
    for i, cls_name in enumerate(CLASSES):
        top = sorted(range(NUM_CLASSES), key=lambda c: transition_model[i][c], reverse=True)[:3]
        top_str = ", ".join(f"{CLASSES[c]}={transition_model[i][c]:.3f}" for c in top)
        print(f"    {cls_name:10s} → {top_str}")

    # Show neighborhood effect for plains (class 0) — how settlement neighbors affect outcome
    print(f"\n  Neighborhood effect (Plains → Settlement probability):")
    for n in range(6):
        p = neighborhood_model[0][n][1]  # Plains, n neighbors, → Settlement
        bar = "█" * int(p * 50)
        print(f"    {n} settlement neighbors: {p:.3f} {bar}")


def main():
    parser = argparse.ArgumentParser(description="Post-round analyzer")
    parser.add_argument("--round", type=int, help="Analyze specific round number")
    parser.add_argument("--all", action="store_true", help="Analyze all completed rounds")
    parser.add_argument("--pull-only", action="store_true", help="Just pull ground truth, don't analyze")
    args = parser.parse_args()

    session = get_session()

    # Get all rounds
    my_rounds = get_completed_rounds(session)
    completed = [r for r in my_rounds if r.get("status") in ("completed", "scoring")]
    all_rounds = sorted(my_rounds, key=lambda r: r.get("round_number", 0))

    print("=== All Rounds ===")
    for r in all_rounds:
        rn = r.get("round_number", "?")
        status = r.get("status", "?")
        score = r.get("round_score", "-")
        seeds = r.get("seeds_submitted", 0)
        print(f"  Round {rn}: {status} | score={score} | seeds={seeds}/5")

    # Pull ground truth
    targets = []
    if args.round:
        target = next((r for r in all_rounds if r.get("round_number") == args.round), None)
        if target:
            targets = [target]
        else:
            print(f"Round {args.round} not found")
            return
    elif args.all:
        targets = completed
    else:
        # Default: analyze all completed rounds
        targets = completed

    for r in targets:
        pull_ground_truth(session, r)

    if args.pull_only:
        return

    # Compute analysis for each round
    for r in targets:
        rn = r.get("round_number", 0)
        gt_exists = any(
            (DATA_DIR / f"round_{rn}" / f"ground_truth_seed_{i}.npy").exists()
            for i in range(5)
        )
        if gt_exists:
            compute_analysis(rn)

    # Build cumulative models
    build_cumulative_models()


if __name__ == "__main__":
    main()
