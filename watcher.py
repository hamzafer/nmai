"""
Round Watcher — Polls for active rounds and triggers astar.py automatically.
Usage: python watcher.py [--interval 300] [--auto-play]
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

BASE = "https://api.ainm.no"


def get_session():
    import requests
    env_path = Path(__file__).parent / ".env"
    token = None
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if "ASTAR_TOKEN" in line and "=" in line:
                token = line.split("=", 1)[1].strip()
    if not token:
        token = os.environ.get("ASTAR_TOKEN")
    if not token:
        raise ValueError("No token found")
    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {token}"
    return s


def check_rounds(session):
    """Check for active rounds and return status."""
    resp = session.get(f"{BASE}/astar-island/rounds")
    if resp.status_code != 200:
        return None, "API error"
    rounds = resp.json()
    active = [r for r in rounds if r.get("status") == "active"]
    return active, "ok"


def check_budget(session):
    """Check query budget for active round."""
    resp = session.get(f"{BASE}/astar-island/budget")
    if resp.status_code == 200:
        return resp.json()
    return None


def run_bot():
    """Run astar.py to play the active round."""
    script = Path(__file__).parent / "astar.py"
    result = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True, text=True, timeout=600
    )
    return result


def run_analyzer():
    """Run analyze.py to pull ground truth for completed rounds."""
    script = Path(__file__).parent / "analyze.py"
    result = subprocess.run(
        [sys.executable, str(script), "--all"],
        capture_output=True, text=True, timeout=120
    )
    return result


def main():
    parser = argparse.ArgumentParser(description="Astar Island round watcher")
    parser.add_argument("--interval", type=int, default=300,
                        help="Poll interval in seconds (default: 300 = 5 min)")
    parser.add_argument("--auto-play", action="store_true",
                        help="Automatically run astar.py when a round is active")
    parser.add_argument("--once", action="store_true",
                        help="Check once and exit")
    args = parser.parse_args()

    session = get_session()
    played_rounds = set()  # Track which rounds we've already played
    analyzed_rounds = set()

    print(f"Astar Island Watcher")
    print(f"  Poll interval: {args.interval}s")
    print(f"  Auto-play: {args.auto_play}")
    print(f"  Press Ctrl+C to stop\n")

    while True:
        now = datetime.now().strftime("%H:%M:%S")
        try:
            active_rounds, status = check_rounds(session)
        except Exception as e:
            print(f"[{now}] Connection error: {e.__class__.__name__}, retrying in {args.interval}s...")
            time.sleep(args.interval)
            continue

        if status != "ok":
            print(f"[{now}] API error, retrying...")
        elif not active_rounds:
            print(f"[{now}] No active round")

            # Try to analyze completed rounds
            if not args.once:
                try:
                    resp = session.get(f"{BASE}/astar-island/my-rounds").json()
                    completed = [r for r in resp
                                 if r.get("status") == "completed"
                                 and r.get("round_number") not in analyzed_rounds
                                 and r.get("seeds_submitted", 0) > 0]
                    for r in completed:
                        rn = r.get("round_number", 0)
                        print(f"[{now}] Round {rn} completed — running analyzer...")
                        result = run_analyzer()
                        if result.returncode == 0:
                            print(result.stdout[-500:] if len(result.stdout) > 500 else result.stdout)
                        else:
                            print(f"  Analyzer error: {result.stderr[:200]}")
                        analyzed_rounds.add(rn)
                except Exception as e:
                    print(f"[{now}] Error checking completed rounds: {e.__class__.__name__}")
        else:
            for r in active_rounds:
                rn = r.get("round_number", 0)
                round_id = r["id"]
                closes = r.get("closes_at", "?")

                # Check budget
                budget = check_budget(session)
                queries_used = budget["queries_used"] if budget else "?"
                queries_max = budget["queries_max"] if budget else "?"

                print(f"[{now}] ROUND {rn} ACTIVE! "
                      f"Queries: {queries_used}/{queries_max} | Closes: {closes}")

                if args.auto_play and rn not in played_rounds:
                    if budget and budget["queries_used"] < budget["queries_max"]:
                        print(f"[{now}] >>> Auto-playing round {rn}...")
                        print("\a")  # Terminal bell
                        result = run_bot()
                        print(result.stdout)
                        if result.stderr:
                            print(f"STDERR: {result.stderr[:500]}")
                        played_rounds.add(rn)
                    else:
                        print(f"[{now}] Round {rn} already played (budget exhausted)")
                        played_rounds.add(rn)
                elif not args.auto_play and rn not in played_rounds:
                    print(f"\a[{now}] >>> Round {rn} is active! Run: python3 astar.py")

        if args.once:
            break

        time.sleep(args.interval)


if __name__ == "__main__":
    main()
