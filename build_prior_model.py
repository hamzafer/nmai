"""
Build a prior model from ground truth data.
For each cell, predict P(final_class | initial_class, features) using:
  - initial_class
  - distance_to_nearest_settlement
  - n_settlement_neighbors_8conn
  - n_settlements_within_radius_3
  - n_settlements_within_radius_5
  - has_ocean_neighbor (coastal)
  - n_forest_neighbors
  
Uses binned lookup tables (fast, interpretable, no sklearn needed).
Saves as models/feature_prior_model.npz
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

def compute_cell_features(grid, y, x, H, W, settlement_positions):
    """Compute features for a single cell."""
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
    
    # Distance to nearest settlement
    min_dist = 999
    n_r3 = 0
    n_r5 = 0
    for sy, sx in settlement_positions:
        dist = abs(y - sy) + abs(x - sx)
        if dist < min_dist:
            min_dist = dist
        if dist <= 3:
            n_r3 += 1
        if dist <= 5:
            n_r5 += 1
    
    return (initial_cls, min(min_dist, 15), min(n_sn, 3), min(n_r5, 6),
            1 if has_ocean else 0, min(n_forest_n, 4))


def build_model():
    """Build lookup tables from ground truth."""
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    
    # Key: (initial_cls, dist_bucket, n_sn, n_r5, has_ocean, n_forest_n)
    # Value: accumulated ground truth distributions + count
    accumulator = defaultdict(lambda: np.zeros(NUM_CLASSES + 1))  # +1 for count
    
    # Simpler model: (initial_cls, dist_bucket) for cells far from settlements
    simple_accumulator = defaultdict(lambda: np.zeros(NUM_CLASSES + 1))
    
    n_samples = 0
    rounds_used = []
    
    for round_num in range(1, 100):
        round_dir = DATA_DIR / f"round_{round_num}"
        if not round_dir.exists():
            continue
        
        has_data = False
        for seed_idx in range(5):
            gt_file = round_dir / f"ground_truth_seed_{seed_idx}.npy"
            init_file = round_dir / f"initial_state_seed_{seed_idx}.json"
            if not gt_file.exists() or not init_file.exists():
                continue
            
            has_data = True
            gt = np.load(gt_file)
            init = json.loads(init_file.read_text())
            grid = init["grid"]
            H, W = len(grid), len(grid[0])
            
            # Find settlement positions
            settlement_positions = []
            for sy in range(H):
                for sx in range(W):
                    if TERRAIN_TO_CLASS.get(grid[sy][sx], 0) in (1, 2):
                        settlement_positions.append((sy, sx))
            
            for y in range(H):
                for x in range(W):
                    features = compute_cell_features(grid, y, x, H, W, settlement_positions)
                    gt_dist = gt[y][x]
                    
                    key = features
                    accumulator[key][:NUM_CLASSES] += gt_dist
                    accumulator[key][NUM_CLASSES] += 1
                    
                    # Simple key: (initial_cls, dist_bucket)
                    simple_key = (features[0], features[1])
                    simple_accumulator[simple_key][:NUM_CLASSES] += gt_dist
                    simple_accumulator[simple_key][NUM_CLASSES] += 1
                    
                    n_samples += 1
        
        if has_data:
            rounds_used.append(round_num)
    
    print(f"Built model from {n_samples} cells across rounds {rounds_used}")
    
    # Convert to normalized probabilities
    # For the full model, if a bucket has too few samples, fall back to simple model
    MIN_SAMPLES = 10  # Need at least this many for reliable estimate
    
    full_model = {}
    for key, acc in accumulator.items():
        count = acc[NUM_CLASSES]
        if count >= MIN_SAMPLES:
            probs = acc[:NUM_CLASSES] / count
            probs = np.maximum(probs, 0.003)
            probs /= probs.sum()
            full_model[key] = probs
    
    simple_model = {}
    for key, acc in simple_accumulator.items():
        count = acc[NUM_CLASSES]
        probs = acc[:NUM_CLASSES] / count
        probs = np.maximum(probs, 0.003)
        probs /= probs.sum()
        simple_model[key] = probs
    
    print(f"Full model: {len(full_model)} buckets (min {MIN_SAMPLES} samples)")
    print(f"Simple model: {len(simple_model)} buckets")
    
    # Save as numpy arrays for fast loading
    # Pack into arrays: keys as tuples, values as probability arrays
    full_keys = np.array(list(full_model.keys()), dtype=np.int32)
    full_vals = np.array(list(full_model.values()), dtype=np.float64)
    simple_keys = np.array(list(simple_model.keys()), dtype=np.int32)
    simple_vals = np.array(list(simple_model.values()), dtype=np.float64)
    
    np.savez(MODELS_DIR / "feature_prior_model.npz",
             full_keys=full_keys, full_vals=full_vals,
             simple_keys=simple_keys, simple_vals=simple_vals)
    
    print(f"Saved to models/feature_prior_model.npz")
    
    # Print some diagnostics
    print("\n=== Key distributions ===")
    for init_cls in range(6):
        print(f"\n{CLASSES[init_cls]}:")
        for dist in [0, 1, 2, 3, 5, 7, 10, 15]:
            key = (init_cls, dist)
            if key in simple_model:
                p = simple_model[key]
                parts = ", ".join(f"{CLASSES[c]}={p[c]:.3f}" for c in range(6) if p[c] > 0.01)
                print(f"  dist={dist:2d}: {parts}")
    
    return full_model, simple_model


if __name__ == "__main__":
    build_model()
