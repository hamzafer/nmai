"""
Local Norse World Simulator — reverse-engineered from Astar Island docs.

Simulates 50 years of settlement growth, conflict, trade, winter, and
environmental change on a 40x40 grid. Uses configurable hidden parameters
so we can calibrate against API observations.

Usage:
    from simulator import Simulator, SimParams
    sim = Simulator(initial_grid, settlements, params=SimParams())
    final_grid = sim.run(years=50)

    # Run many simulations for probability distributions:
    from simulator import run_monte_carlo
    prob_tensor = run_monte_carlo(initial_grid, settlements, n_sims=500)
"""

import numpy as np
from dataclasses import dataclass, field
from typing import Optional


# Terrain codes (matching the API)
OCEAN = 10
PLAINS = 11
EMPTY = 0
SETTLEMENT = 1
PORT = 2
RUIN = 3
FOREST = 4
MOUNTAIN = 5

# Prediction class mapping
TERRAIN_TO_CLASS = {
    OCEAN: 0, PLAINS: 0, EMPTY: 0,
    SETTLEMENT: 1, PORT: 2, RUIN: 3, FOREST: 4, MOUNTAIN: 5,
}

NUM_CLASSES = 6


@dataclass
class SimParams:
    """Hidden parameters that change per round. Tunable for calibration."""

    # ── Growth ──
    food_per_forest: float = 2.0        # food gained per adjacent forest cell
    food_per_plains: float = 0.5        # food gained per adjacent plains cell
    food_consumption: float = 0.3       # food consumed per population unit per year
    growth_rate: float = 0.12           # population growth rate when food > 0
    max_population: float = 20.0        # population cap per settlement
    expansion_threshold: float = 10.0   # population needed to found new settlement
    expansion_prob: float = 0.25        # probability of expansion when above threshold
    port_development_prob: float = 0.15 # probability of developing port if coastal

    # ── Conflict ──
    raid_range: int = 5                 # maximum raiding distance (Manhattan)
    raid_range_longship: int = 12       # raid range with longships (over water)
    raid_prob: float = 0.1              # base probability of raiding per year
    desperate_raid_mult: float = 2.0    # raid probability multiplier when starving
    raid_damage: float = 2.0            # population/food damage per raid
    conquest_prob: float = 0.08         # probability of conquering after successful raid
    longship_build_prob: float = 0.1    # probability of building longship per year (ports)

    # ── Trade ──
    trade_range: int = 8                # maximum trading distance between ports
    trade_food: float = 1.5             # food generated per trade connection
    trade_wealth: float = 1.0           # wealth generated per trade connection
    tech_diffusion: float = 0.1         # tech level sharing between traders

    # ── Winter ──
    winter_base_severity: float = 1.0   # minimum food loss in winter
    winter_severity_range: float = 2.5  # additional random food loss (uniform)
    collapse_food_threshold: float = -8.0  # food level that triggers collapse to ruin

    # ── Environment ──
    ruin_reclaim_prob: float = 0.15     # probability of nearby settlement reclaiming a ruin
    ruin_reclaim_range: int = 4         # maximum distance for reclaim
    forest_regrowth_prob: float = 0.08  # probability of ruin becoming forest
    ruin_decay_prob: float = 0.05       # probability of ruin becoming plains


@dataclass
class SettlementState:
    """Internal state of a settlement."""
    x: int
    y: int
    population: float = 5.0
    food: float = 5.0
    wealth: float = 0.0
    defense: float = 1.0
    tech: float = 1.0
    has_port: bool = False
    has_longship: bool = False
    owner_id: int = 0  # faction
    alive: bool = True


class Simulator:
    """Norse world simulator with configurable parameters."""

    def __init__(self, initial_grid: list, settlements: list,
                 params: Optional[SimParams] = None, rng_seed: Optional[int] = None):
        """
        Args:
            initial_grid: 2D list of terrain codes (from API initial state)
            settlements: list of dicts with x, y, has_port, alive
            params: simulation parameters (hidden in competition)
            rng_seed: random seed for reproducibility
        """
        self.height = len(initial_grid)
        self.width = len(initial_grid[0])
        self.params = params or SimParams()
        self.rng = np.random.RandomState(rng_seed)

        # Copy grid
        self.grid = np.array(initial_grid, dtype=np.int32)

        # Initialize settlements
        self.settlements = []
        for i, s in enumerate(settlements):
            state = SettlementState(
                x=s["x"], y=s["y"],
                has_port=s.get("has_port", False),
                alive=s.get("alive", True),
                owner_id=i,  # each starts as its own faction
                population=3.0 + self.rng.uniform(0, 5),
                food=3.0 + self.rng.uniform(0, 4),
                wealth=self.rng.uniform(0, 2),
                defense=1.0 + self.rng.uniform(0, 1),
                tech=1.0,
            )
            self.settlements.append(state)

    def run(self, years: int = 50) -> np.ndarray:
        """Run the simulation for N years. Returns final grid."""
        for year in range(years):
            self._phase_growth()
            self._phase_conflict()
            self._phase_trade()
            self._phase_winter(year)
            self._phase_environment()
        return self.grid.copy()

    def get_class_grid(self) -> np.ndarray:
        """Convert terrain grid to 6-class prediction grid."""
        result = np.zeros((self.height, self.width), dtype=np.int32)
        for y in range(self.height):
            for x in range(self.width):
                result[y][x] = TERRAIN_TO_CLASS.get(self.grid[y][x], 0)
        return result

    # ── Helper methods ─────────────────────────────────────────────────────

    def _alive_settlements(self):
        return [s for s in self.settlements if s.alive]

    def _adjacent_terrain_count(self, x, y, terrain_type):
        """Count adjacent cells (4-connected) of a given terrain type."""
        count = 0
        for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
            nx, ny = x + dx, y + dy
            if 0 <= nx < self.width and 0 <= ny < self.height:
                if self.grid[ny][nx] == terrain_type:
                    count += 1
        return count

    def _adjacent_terrain_count_8(self, x, y, terrain_type):
        """Count adjacent cells (8-connected) of a given terrain type."""
        count = 0
        for dx in [-1, 0, 1]:
            for dy in [-1, 0, 1]:
                if dx == 0 and dy == 0:
                    continue
                nx, ny = x + dx, y + dy
                if 0 <= nx < self.width and 0 <= ny < self.height:
                    if self.grid[ny][nx] == terrain_type:
                        count += 1
        return count

    def _is_coastal(self, x, y):
        """Check if a cell is adjacent to ocean."""
        return self._adjacent_terrain_count_8(x, y, OCEAN) > 0

    def _manhattan_dist(self, x1, y1, x2, y2):
        return abs(x1 - x2) + abs(y1 - y2)

    def _empty_land_neighbors(self, x, y):
        """Find walkable empty land cells adjacent to (x, y)."""
        cells = []
        for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
            nx, ny = x + dx, y + dy
            if 0 <= nx < self.width and 0 <= ny < self.height:
                if self.grid[ny][nx] == PLAINS:
                    cells.append((nx, ny))
        return cells

    def _settlement_at(self, x, y):
        """Find alive settlement at position."""
        for s in self.settlements:
            if s.alive and s.x == x and s.y == y:
                return s
        return None

    # ── Phase 1: Growth ────────────────────────────────────────────────────

    def _phase_growth(self):
        p = self.params
        for s in self._alive_settlements():
            # Food production from adjacent terrain
            forest_adj = self._adjacent_terrain_count_8(s.x, s.y, FOREST)
            plains_adj = self._adjacent_terrain_count_8(s.x, s.y, PLAINS)
            food_production = forest_adj * p.food_per_forest + plains_adj * p.food_per_plains

            # Food consumption
            food_consumption = s.population * p.food_consumption
            s.food += food_production - food_consumption

            # Population growth when food is positive
            if s.food > 0:
                growth = s.population * p.growth_rate * (1 - s.population / p.max_population)
                s.population = min(s.population + max(0, growth), p.max_population)
                s.defense = 1.0 + s.population * 0.2 + s.tech * 0.3

            # Port development: coastal settlements can develop ports
            if not s.has_port and self._is_coastal(s.x, s.y):
                if s.population > 5 and self.rng.random() < p.port_development_prob:
                    s.has_port = True
                    self.grid[s.y][s.x] = PORT

            # Longship building (ports only)
            if s.has_port and not s.has_longship:
                if s.population > 7 and self.rng.random() < p.longship_build_prob:
                    s.has_longship = True

            # Expansion: found new settlement on adjacent empty land
            if s.population >= p.expansion_threshold:
                if self.rng.random() < p.expansion_prob:
                    candidates = self._empty_land_neighbors(s.x, s.y)
                    # Also check 2-cell radius
                    for dx in range(-2, 3):
                        for dy in range(-2, 3):
                            if abs(dx) + abs(dy) > 2:
                                continue
                            nx, ny = s.x + dx, s.y + dy
                            if 0 <= nx < self.width and 0 <= ny < self.height:
                                if self.grid[ny][nx] == PLAINS:
                                    candidates.append((nx, ny))

                    # Remove duplicates and occupied cells
                    candidates = list(set(candidates))
                    candidates = [(cx, cy) for cx, cy in candidates
                                  if self._settlement_at(cx, cy) is None]

                    if candidates:
                        cx, cy = candidates[self.rng.randint(len(candidates))]
                        new_pop = s.population * 0.3
                        s.population -= new_pop

                        new_settlement = SettlementState(
                            x=cx, y=cy,
                            population=new_pop,
                            food=s.food * 0.3,
                            wealth=0,
                            defense=1.0,
                            tech=s.tech * 0.8,
                            has_port=False,
                            owner_id=s.owner_id,
                        )
                        # Check if coastal → might be port
                        if self._is_coastal(cx, cy) and self.rng.random() < 0.3:
                            new_settlement.has_port = True
                            self.grid[cy][cx] = PORT
                        else:
                            self.grid[cy][cx] = SETTLEMENT

                        self.settlements.append(new_settlement)

    # ── Phase 2: Conflict ──────────────────────────────────────────────────

    def _phase_conflict(self):
        p = self.params
        alive = self._alive_settlements()

        for attacker in alive:
            if not attacker.alive:
                continue

            # Determine raid probability
            raid_prob = p.raid_prob
            if attacker.food < 0:
                raid_prob *= p.desperate_raid_mult

            if self.rng.random() > raid_prob:
                continue

            # Find targets within range
            raid_range = p.raid_range_longship if attacker.has_longship else p.raid_range
            targets = []
            for defender in alive:
                if defender is attacker or not defender.alive:
                    continue
                if defender.owner_id == attacker.owner_id:
                    continue
                dist = self._manhattan_dist(attacker.x, attacker.y, defender.x, defender.y)
                if dist <= raid_range:
                    targets.append(defender)

            if not targets:
                continue

            # Attack weakest target
            target = min(targets, key=lambda t: t.defense)

            # Raid outcome
            attack_power = attacker.population * (1 + attacker.tech * 0.2)
            defend_power = target.defense * target.population

            if attack_power > defend_power * 0.5:
                # Successful raid
                loot = min(target.food * 0.3, p.raid_damage)
                attacker.food += max(0, loot)
                attacker.wealth += target.wealth * 0.2
                target.food -= p.raid_damage
                target.population = max(1, target.population - p.raid_damage * 0.3)
                target.defense *= 0.8

                # Conquest chance
                if self.rng.random() < p.conquest_prob:
                    target.owner_id = attacker.owner_id

    # ── Phase 3: Trade ─────────────────────────────────────────────────────

    def _phase_trade(self):
        p = self.params
        alive = self._alive_settlements()
        ports = [s for s in alive if s.has_port]

        for i, port_a in enumerate(ports):
            for port_b in ports[i + 1:]:
                # Can trade if within range and not at war
                dist = self._manhattan_dist(port_a.x, port_a.y, port_b.x, port_b.y)
                if dist > p.trade_range:
                    continue
                # Trade between factions (even if different factions — trade creates peace)
                port_a.food += p.trade_food
                port_b.food += p.trade_food
                port_a.wealth += p.trade_wealth
                port_b.wealth += p.trade_wealth

                # Tech diffusion
                avg_tech = (port_a.tech + port_b.tech) / 2
                port_a.tech += (avg_tech - port_a.tech) * p.tech_diffusion
                port_b.tech += (avg_tech - port_b.tech) * p.tech_diffusion

    # ── Phase 4: Winter ────────────────────────────────────────────────────

    def _phase_winter(self, year: int):
        p = self.params
        # Winter severity varies each year
        severity = p.winter_base_severity + self.rng.uniform(0, p.winter_severity_range)

        for s in self._alive_settlements():
            s.food -= severity

            # Collapse check
            if s.food < p.collapse_food_threshold:
                self._collapse_settlement(s)
            elif s.population < 1:
                self._collapse_settlement(s)

    def _collapse_settlement(self, s: SettlementState):
        """Settlement collapses into a ruin."""
        s.alive = False
        self.grid[s.y][s.x] = RUIN

        # Disperse population to nearby friendly settlements
        alive = self._alive_settlements()
        friendly = [f for f in alive
                    if f.owner_id == s.owner_id
                    and self._manhattan_dist(f.x, f.y, s.x, s.y) <= 6]

        if friendly and s.population > 0:
            per_settlement = s.population * 0.5 / len(friendly)
            for f in friendly:
                f.population += per_settlement

    # ── Phase 5: Environment ───────────────────────────────────────────────

    def _phase_environment(self):
        p = self.params

        # Find all ruins
        ruins = []
        for y in range(self.height):
            for x in range(self.width):
                if self.grid[y][x] == RUIN:
                    ruins.append((x, y))

        for rx, ry in ruins:
            # Check if a nearby settlement reclaims it
            reclaimed = False
            alive = self._alive_settlements()
            nearby = [s for s in alive
                      if self._manhattan_dist(s.x, s.y, rx, ry) <= p.ruin_reclaim_range
                      and s.population > 5]

            if nearby and self.rng.random() < p.ruin_reclaim_prob:
                patron = max(nearby, key=lambda s: s.population)
                # Reclaim as new settlement
                new_pop = patron.population * 0.2
                patron.population -= new_pop

                is_coastal = self._is_coastal(rx, ry)
                new_settlement = SettlementState(
                    x=rx, y=ry,
                    population=new_pop,
                    food=patron.food * 0.2,
                    wealth=0,
                    defense=1.0,
                    tech=patron.tech * 0.7,
                    has_port=is_coastal and self.rng.random() < 0.4,
                    owner_id=patron.owner_id,
                )
                self.grid[ry][rx] = PORT if new_settlement.has_port else SETTLEMENT
                self.settlements.append(new_settlement)
                reclaimed = True

            if not reclaimed:
                # Forest regrowth or decay to plains
                if self.rng.random() < p.forest_regrowth_prob:
                    self.grid[ry][rx] = FOREST
                elif self.rng.random() < p.ruin_decay_prob:
                    self.grid[ry][rx] = PLAINS


# ─── Monte Carlo simulation ───────────────────────────────────────────────────

def run_monte_carlo(initial_grid: list, settlements: list,
                    params: Optional[SimParams] = None,
                    n_sims: int = 500, years: int = 50,
                    base_seed: int = 42) -> np.ndarray:
    """
    Run N simulations and compute per-cell probability distributions.

    Returns: H x W x 6 probability tensor.
    """
    height = len(initial_grid)
    width = len(initial_grid[0])
    counts = np.zeros((height, width, NUM_CLASSES), dtype=np.float64)

    for i in range(n_sims):
        sim = Simulator(initial_grid, settlements, params=params, rng_seed=base_seed + i)
        sim.run(years=years)
        class_grid = sim.get_class_grid()

        for y in range(height):
            for x in range(width):
                counts[y][x][class_grid[y][x]] += 1

        if (i + 1) % 100 == 0:
            print(f"  Sim {i + 1}/{n_sims}")

    # Normalize to probabilities
    prob = counts / n_sims

    # Apply safety floor
    prob = np.maximum(prob, 0.005)
    prob = prob / prob.sum(axis=-1, keepdims=True)

    return prob


# ─── Parameter calibration via ABC ────────────────────────────────────────────

def calibrate_params(initial_grid: list, settlements: list,
                     observations: list, width: int, height: int,
                     n_candidates: int = 50, n_sims_per: int = 20,
                     base_seed: int = 42) -> SimParams:
    """
    Approximate Bayesian Computation (ABC) for parameter estimation.

    Given observations from the API, find parameters that best explain them.
    """
    # Parse observations into a target distribution
    obs_counts = np.zeros((height, width, NUM_CLASSES), dtype=np.float64)
    obs_total = np.zeros((height, width), dtype=np.float64)

    for obs in observations:
        viewport = obs["viewport"]
        grid = obs["grid"]
        vx, vy = viewport["x"], viewport["y"]
        for ry, row in enumerate(grid):
            for rx, cell_val in enumerate(row):
                gx, gy = vx + rx, vy + ry
                if 0 <= gx < width and 0 <= gy < height:
                    cls = TERRAIN_TO_CLASS.get(cell_val, 0)
                    obs_counts[gy][gx][cls] += 1
                    obs_total[gy][gx] += 1

    # Observed cells mask
    observed = obs_total > 0

    if not np.any(observed):
        print("  No observations to calibrate against, using defaults")
        return SimParams()

    # Normalize observations
    obs_probs = np.zeros_like(obs_counts)
    for y in range(height):
        for x in range(width):
            if obs_total[y][x] > 0:
                obs_probs[y][x] = obs_counts[y][x] / obs_total[y][x]

    rng = np.random.RandomState(base_seed)

    # Generate candidate parameter sets
    best_params = SimParams()
    best_distance = float("inf")

    for c in range(n_candidates):
        # Randomly perturb parameters
        candidate = SimParams(
            food_per_forest=rng.uniform(0.5, 3.0),
            food_per_plains=rng.uniform(0.1, 1.0),
            food_consumption=rng.uniform(0.5, 2.0),
            growth_rate=rng.uniform(0.05, 0.3),
            max_population=rng.uniform(10, 30),
            expansion_threshold=rng.uniform(6, 18),
            expansion_prob=rng.uniform(0.1, 0.5),
            port_development_prob=rng.uniform(0.05, 0.4),
            raid_range=rng.randint(3, 8),
            raid_prob=rng.uniform(0.05, 0.3),
            desperate_raid_mult=rng.uniform(1.5, 4.0),
            raid_damage=rng.uniform(1.0, 6.0),
            conquest_prob=rng.uniform(0.02, 0.2),
            longship_build_prob=rng.uniform(0.03, 0.2),
            trade_range=rng.randint(4, 15),
            trade_food=rng.uniform(0.5, 3.0),
            trade_wealth=rng.uniform(0.3, 2.0),
            tech_diffusion=rng.uniform(0.02, 0.2),
            winter_base_severity=rng.uniform(0.5, 4.0),
            winter_severity_range=rng.uniform(1.0, 8.0),
            collapse_food_threshold=rng.uniform(-8.0, -2.0),
            ruin_reclaim_prob=rng.uniform(0.05, 0.3),
            ruin_reclaim_range=rng.randint(2, 6),
            forest_regrowth_prob=rng.uniform(0.02, 0.15),
            ruin_decay_prob=rng.uniform(0.02, 0.1),
        )

        # Run simulations with this candidate
        sim_counts = np.zeros((height, width, NUM_CLASSES), dtype=np.float64)
        for s in range(n_sims_per):
            sim = Simulator(initial_grid, settlements, params=candidate,
                            rng_seed=base_seed + c * 1000 + s)
            sim.run()
            class_grid = sim.get_class_grid()
            for y in range(height):
                for x in range(width):
                    sim_counts[y][x][class_grid[y][x]] += 1

        sim_probs = sim_counts / n_sims_per
        sim_probs = np.maximum(sim_probs, 0.001)
        sim_probs = sim_probs / sim_probs.sum(axis=-1, keepdims=True)

        # Compute distance: KL divergence on observed cells only
        eps = 1e-10
        distance = 0
        n_cells = 0
        for y in range(height):
            for x in range(width):
                if observed[y][x]:
                    p = obs_probs[y][x]
                    q = sim_probs[y][x]
                    # Symmetric KL
                    kl = 0.5 * np.sum(p * np.log((p + eps) / (q + eps))) + \
                         0.5 * np.sum(q * np.log((q + eps) / (p + eps)))
                    distance += kl
                    n_cells += 1

        if n_cells > 0:
            distance /= n_cells

        if distance < best_distance:
            best_distance = distance
            best_params = candidate
            print(f"  Candidate {c + 1}/{n_candidates}: distance={distance:.4f} (new best)")

    print(f"  Best distance: {best_distance:.4f}")
    return best_params


# ─── CLI for testing ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json
    import sys
    from pathlib import Path

    # Load a saved initial state for testing
    data_dir = Path(__file__).parent / "data"
    test_file = None

    for round_dir in sorted(data_dir.glob("round_*"), reverse=True):
        f = round_dir / "initial_state_seed_0.json"
        if f.exists():
            test_file = f
            break

    if not test_file:
        print("No initial state data found. Run astar.py first to save round data.")
        sys.exit(1)

    print(f"Loading: {test_file}")
    state = json.loads(test_file.read_text())
    grid = state["grid"]
    settlements = state["settlements"]
    height, width = len(grid), len(grid[0])

    print(f"Grid: {width}x{height}, Settlements: {len(settlements)}")

    # Run a single simulation
    print("\n=== Single simulation ===")
    sim = Simulator(grid, settlements, rng_seed=42)
    final = sim.run(years=50)

    # Count results
    from collections import Counter
    names = {0: "Empty", 10: "Ocean", 11: "Plains", 1: "Settlement",
             2: "Port", 3: "Ruin", 4: "Forest", 5: "Mountain"}
    before = Counter()
    after = Counter()
    for y in range(height):
        for x in range(width):
            before[grid[y][x]] += 1
            after[int(final[y][x])] += 1

    print(f"\n{'Terrain':<12s} {'Before':>8s} {'After':>8s} {'Change':>8s}")
    for code in sorted(set(list(before.keys()) + list(after.keys()))):
        b, a = before[code], after[code]
        print(f"{names.get(code, str(code)):<12s} {b:>8d} {a:>8d} {a - b:>+8d}")

    alive = sum(1 for s in sim.settlements if s.alive)
    total = len(sim.settlements)
    print(f"\nSettlements: {len(settlements)} initial → {total} total ({alive} alive, {total - alive} collapsed)")

    # Run Monte Carlo
    n_sims = int(sys.argv[1]) if len(sys.argv) > 1 else 50
    print(f"\n=== Monte Carlo ({n_sims} simulations) ===")
    prob = run_monte_carlo(grid, settlements, n_sims=n_sims, base_seed=42)

    # Stats on dynamic cells
    dynamic_mask = prob.max(axis=-1) < 0.95  # cells where no class has >95% probability
    print(f"Dynamic cells (max prob < 0.95): {dynamic_mask.sum()} of {width * height}")

    # Show some dynamic cell distributions
    dynamic_coords = list(zip(*np.where(dynamic_mask)))[:5]
    for y, x in dynamic_coords:
        dist = prob[y][x]
        cls_str = ", ".join(f"{TERRAIN_TO_CLASS.get(c, c)}={dist[TERRAIN_TO_CLASS.get(c, c)]:.2f}"
                            for c in [PLAINS, SETTLEMENT, PORT, RUIN, FOREST]
                            if dist[TERRAIN_TO_CLASS.get(c, c)] > 0.02)
        print(f"  ({x:2d},{y:2d}): {cls_str}")
