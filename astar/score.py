"""Quick check: scores, rank, and leaderboard position."""
import os
import requests
from pathlib import Path

env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

token = os.environ.get("ASTAR_TOKEN")
if not token:
    print("No token found in .env")
    exit(1)

s = requests.Session()
s.headers["Authorization"] = f"Bearer {token}"
BASE = "https://api.ainm.no"

my_rounds = s.get(f"{BASE}/astar-island/my-rounds").json()
print("=== Your Rounds ===")
for r in sorted(my_rounds, key=lambda x: x.get("round_number", 0)):
    rn = r.get("round_number", "?")
    status = r.get("status", "?")
    score = r.get("round_score", "-")
    rank = r.get("rank", "-")
    total = r.get("total_teams", "-")
    seeds = r.get("seeds_submitted", 0)
    queries = r.get("queries_used", 0)
    print(f"  Round {rn}: {status:10s} | score={str(score):>6s} | rank={rank}/{total} | seeds={seeds}/5 | queries={queries}/50")

lb = s.get(f"{BASE}/astar-island/leaderboard").json()
print(f"\n=== Leaderboard (top 15 of {len(lb)}) ===")
for entry in lb[:15]:
    name = entry["team_name"]
    score = entry.get("weighted_score", "?")
    streak = entry.get("hot_streak_score", "?")
    print(f"  #{entry['rank']:3d} {name:30s} score={score:<10} streak={streak}")

ours = next((e for e in lb if "hamza" in e.get("team_name", "").lower()
             or "hamza" in e.get("team_slug", "").lower()), None)
if ours:
    print(f"\n>>> You: #{ours['rank']} — score={ours.get('weighted_score', '?')}")
else:
    print("\n>>> Not on leaderboard yet (no scored rounds)")
