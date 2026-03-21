"""
Backtest: compare current prediction approach vs feature-based prior model.
Uses ground truth from rounds 1-17.
"""
import numpy as np
import json
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(__file__).parent / "data"
MODELS_DIR = Path(__file__).parent / "models"
TERRAIN_TO_CLASS = {0:0, 10:0, 11:0, 1:1, 2:2, 3:3, 4:4, 5:5}
NUM_CLASSES = 6
CLASSES = ["Empty", "Settlement", "Port", "Ruin", "Forest", "Mountain"]
STATIC_CLASSES = {0, 4, 5}
DYNAMIC_RADIUS = 7


def score_prediction(gt, pred):
    """Compute entropy-weighted KL score (0-100)."""
    eps = 1e-10
    kl = np.sum(gt * np.log((gt + eps) / (pred + eps)), axis=-1)
    entropy = -np.sum(gt * np.log(gt + eps), axis=-1)
    total_entropy = entropy.sum()
    if total_entropy < eps:
        return 100.0
    weighted_kl = np.sum(entropy * kl) / total_entropy
    return max(0, min(100, 100 * np.exp(-3 * weighted_kl)))


def load_feature_prior_model():
    """Load the feature-based prior model."""
    data = np.load(MODELS_DIR / "feature_prior_model.npz")
    
    # Build lookup dicts
    full_model = {}
    for key, val in zip(data["full_keys"], data["full_vals"]):
        full_model[tuple(key)] = val
    
    simple_model = {}
    for key, val in zip(data["simple_keys"], data["simple_vals"]):
        simple_model[tuple(key)] = val
    
    return full_model, simple_model


def get_feature_prior(grid, y, x, H, W, settlement_positions, full_model, simple_model):
    """Get prior distribution for a cell using feature model."""
    initial_cls = TERRAIN_TO_CLASS.get(grid[y][x], 0)
    
    # Settlement neighbors (8-connected)
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
    
    min_dist = min(min_dist, 15)
    n_sn = min(n_sn, 3)
    n_r5 = min(n_r5, 6)
    n_forest_n = min(n_forest_n, 4)
    ocean_flag = 1 if has_ocean else 0
    
    # Try full model first
    key = (initial_cls, min_dist, n_sn, n_r5, ocean_flag, n_forest_n)
    if key in full_model:
        return full_model[key]
    
    # Fall back to simple model
    simple_key = (initial_cls, min_dist)
    if simple_key in simple_model:
        return simple_model[simple_key]
    
    # Last resort: uniform prior for this class
    prior = np.full(NUM_CLASSES, 0.005)
    prior[initial_cls] = 0.97
    prior /= prior.sum()
    return prior


def predict_old(grid, H, W, observations, cumulative_priors, neighborhood_model, concentration=20.0):
    """Current prediction method from astar.py."""
    # Count observations
    obs_counts = np.zeros((H, W, NUM_CLASSES), dtype=np.float64)
    obs_total = np.zeros((H, W), dtype=np.float64)
    for obs in observations:
        viewport = obs["viewport"]
        obs_grid = obs["grid"]
        vx, vy = viewport["x"], viewport["y"]
        for ry, row in enumerate(obs_grid):
            for rx, cell_val in enumerate(row):
                gx, gy = vx + rx, vy + ry
                if 0 <= gx < W and 0 <= gy < H:
                    cls = TERRAIN_TO_CLASS.get(cell_val, 0)
                    obs_counts[gy][gx][cls] += 1
                    obs_total[gy][gx] += 1
    
    prediction = np.zeros((H, W, NUM_CLASSES), dtype=np.float64)
    
    for y in range(H):
        for x in range(W):
            initial_cls = TERRAIN_TO_CLASS.get(grid[y][x], 0)
            n_obs = obs_total[y][x]
            
            if initial_cls in STATIC_CLASSES and n_obs == 0:
                prediction[y][x][initial_cls] = 0.97
                leftover = 0.03 / (NUM_CLASSES - 1)
                for c in range(NUM_CLASSES):
                    if c != initial_cls:
                        prediction[y][x][c] = leftover
            elif n_obs > 0:
                if cumulative_priors is not None:
                    alpha = cumulative_priors[initial_cls] * concentration
                else:
                    alpha = np.full(NUM_CLASSES, 1.0 / NUM_CLASSES) * concentration
                posterior = obs_counts[y][x] + alpha
                prediction[y][x] = posterior / posterior.sum()
            else:
                if neighborhood_model is not None:
                    n_neighbors = 0
                    for dy in [-1, 0, 1]:
                        for dx in [-1, 0, 1]:
                            if dy == 0 and dx == 0:
                                continue
                            ny, nx = y + dy, x + dx
                            if 0 <= ny < H and 0 <= nx < W:
                                ncls = TERRAIN_TO_CLASS.get(grid[ny][nx], 0)
                                if ncls in (1, 2):
                                    n_neighbors += 1
                    n_neighbors = min(n_neighbors, 8)
                    prediction[y][x] = neighborhood_model[initial_cls][n_neighbors]
                elif cumulative_priors is not None:
                    prediction[y][x] = cumulative_priors[initial_cls]
                else:
                    prediction[y][x][initial_cls] = 0.97
                    leftover = 0.03 / (NUM_CLASSES - 1)
                    for c in range(NUM_CLASSES):
                        if c != initial_cls:
                            prediction[y][x][c] = leftover
    
    prediction = np.maximum(prediction, 0.005)
    prediction /= prediction.sum(axis=-1, keepdims=True)
    return prediction


def predict_new(grid, H, W, observations, full_model, simple_model, 
                concentration=8.0):
    """New prediction method using feature-based priors."""
    # Count observations
    obs_counts = np.zeros((H, W, NUM_CLASSES), dtype=np.float64)
    obs_total = np.zeros((H, W), dtype=np.float64)
    for obs in observations:
        viewport = obs["viewport"]
        obs_grid = obs["grid"]
        vx, vy = viewport["x"], viewport["y"]
        for ry, row in enumerate(obs_grid):
            for rx, cell_val in enumerate(row):
                gx, gy = vx + rx, vy + ry
                if 0 <= gx < W and 0 <= gy < H:
                    cls = TERRAIN_TO_CLASS.get(cell_val, 0)
                    obs_counts[gy][gx][cls] += 1
                    obs_total[gy][gx] += 1
    
    # Find settlement positions
    settlement_positions = []
    for sy in range(H):
        for sx in range(W):
            if TERRAIN_TO_CLASS.get(grid[sy][sx], 0) in (1, 2):
                settlement_positions.append((sy, sx))
    
    prediction = np.zeros((H, W, NUM_CLASSES), dtype=np.float64)
    
    for y in range(H):
        for x in range(W):
            # Get feature-based prior for EVERY cell
            prior = get_feature_prior(grid, y, x, H, W, settlement_positions,
                                       full_model, simple_model)
            
            n_obs = obs_total[y][x]
            if n_obs > 0:
                # Bayesian update: observations + prior
                alpha = prior * concentration
                posterior = obs_counts[y][x] + alpha
                prediction[y][x] = posterior / posterior.sum()
            else:
                # No observations — use the feature-based prior directly
                prediction[y][x] = prior
    
    prediction = np.maximum(prediction, 0.005)
    prediction /= prediction.sum(axis=-1, keepdims=True)
    return prediction


def main():
    # Load models
    full_model, simple_model = load_feature_prior_model()
    
    # Load cumulative priors (for old method)
    cumulative_priors = None
    neighborhood_model = None
    tp_file = MODELS_DIR / "transition_model.npy"
    nm_file = MODELS_DIR / "neighborhood_model.npy"
    if tp_file.exists():
        cumulative_priors = np.load(tp_file)
    if nm_file.exists():
        neighborhood_model = np.load(nm_file)
    
    print("=== BACKTEST: Old vs New Prediction Method ===")
    print(f"Old: static cells=97%, dynamic=Bayesian(cumulative priors, conc=20)")
    print(f"New: feature-based priors for ALL cells, Bayesian(conc=8)")
    print()
    
    old_scores = []
    new_scores = []
    oracle_scores = []  # What if we used perfect priors?
    
    by_round_old = defaultdict(list)
    by_round_new = defaultdict(list)
    
    for round_num in range(1, 18):
        round_dir = DATA_DIR / f"round_{round_num}"
        if not round_dir.exists():
            continue
        
        for seed_idx in range(5):
            gt_file = round_dir / f"ground_truth_seed_{seed_idx}.npy"
            init_file = round_dir / f"initial_state_seed_{seed_idx}.json"
            obs_file = round_dir / f"observations_seed_{seed_idx}.json"
            
            if not gt_file.exists() or not init_file.exists():
                continue
            
            gt = np.load(gt_file)
            init = json.loads(init_file.read_text())
            grid = init["grid"]
            H, W = len(grid), len(grid[0])
            
            # Load observations if available
            observations = []
            if obs_file.exists():
                observations = json.loads(obs_file.read_text())
            
            # Old prediction
            pred_old = predict_old(grid, H, W, observations, cumulative_priors,
                                    neighborhood_model, concentration=20.0)
            score_old = score_prediction(gt, pred_old)
            
            # New prediction (NO observations — pure prior test)
            pred_new_no_obs = predict_new(grid, H, W, [], full_model, simple_model,
                                           concentration=8.0)
            score_new_no_obs = score_prediction(gt, pred_new_no_obs)
            
            # New prediction WITH observations
            pred_new = predict_new(grid, H, W, observations, full_model, simple_model,
                                    concentration=8.0)
            score_new = score_prediction(gt, pred_new)
            
            old_scores.append(score_old)
            new_scores.append(score_new)
            
            by_round_old[round_num].append(score_old)
            by_round_new[round_num].append(score_new)
            
            if seed_idx == 0:
                print(f"Round {round_num:2d} seed 0: old={score_old:5.1f}  "
                      f"new(no obs)={score_new_no_obs:5.1f}  "
                      f"new(+obs)={score_new:5.1f}  "
                      f"delta={score_new - score_old:+5.1f}")
    
    print()
    print("=== Per-Round Average Scores ===")
    print(f"{'Round':>6s}  {'Old':>6s}  {'New':>6s}  {'Delta':>7s}")
    for rn in sorted(by_round_old):
        old_avg = np.mean(by_round_old[rn])
        new_avg = np.mean(by_round_new[rn])
        delta = new_avg - old_avg
        print(f"{rn:6d}  {old_avg:6.1f}  {new_avg:6.1f}  {delta:+7.1f}")
    
    print()
    print(f"Overall: old={np.mean(old_scores):.1f}  new={np.mean(new_scores):.1f}  "
          f"delta={np.mean(new_scores) - np.mean(old_scores):+.1f}")
    
    # Test different concentration values
    print()
    print("=== Concentration Sweep (new model, no observations) ===")
    for conc in [0.5, 1.0, 2.0, 4.0, 8.0, 12.0, 20.0, 50.0]:
        scores = []
        for round_num in range(1, 18):
            round_dir = DATA_DIR / f"round_{round_num}"
            if not round_dir.exists():
                continue
            # Just test seed 0 for speed
            gt_file = round_dir / f"ground_truth_seed_0.npy"
            init_file = round_dir / f"initial_state_seed_0.json"
            obs_file = round_dir / f"observations_seed_0.json"
            if not gt_file.exists() or not init_file.exists():
                continue
            gt = np.load(gt_file)
            init_data = json.loads(init_file.read_text())
            grid = init_data["grid"]
            H, W = len(grid), len(grid[0])
            observations = []
            if obs_file.exists():
                observations = json.loads(obs_file.read_text())
            pred = predict_new(grid, H, W, observations, full_model, simple_model,
                                concentration=conc)
            scores.append(score_prediction(gt, pred))
        print(f"  conc={conc:5.1f}: mean={np.mean(scores):5.1f}, "
              f"min={np.min(scores):5.1f}, max={np.max(scores):5.1f}")


if __name__ == "__main__":
    main()
