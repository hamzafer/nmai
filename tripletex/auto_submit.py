"""
Auto-submit loop for Tripletex competition.

Submits to the platform, waits for results, logs everything.
Run this locally while the agent server + tunnel are running.

Usage:
    export AINM_COOKIE="your-session-cookie-from-browser"
    python -m tripletex.auto_submit --url https://your-tunnel.trycloudflare.com/solve --count 32
"""

import argparse
import json
import os
import re
import time
import requests
from datetime import datetime, timezone
from pathlib import Path

LOGS_DIR = Path(__file__).parent / "logs"
RESULTS_DIR = Path(__file__).parent / "logs" / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

PLATFORM_URL = "https://app.ainm.no"


def get_session():
    """Get session from cookie or env."""
    cookie = os.environ.get("AINM_COOKIE", "")
    if not cookie:
        print("ERROR: Set AINM_COOKIE env var.")
        print("  1. Open https://app.ainm.no in Chrome")
        print("  2. Open DevTools → Application → Cookies")
        print("  3. Copy the full cookie string")
        print("  4. export AINM_COOKIE='...'")
        exit(1)
    return cookie


def submit(endpoint_url: str, cookie: str, api_key: str = ""):
    """Trigger a submission on the platform."""
    session = requests.Session()
    session.headers["Cookie"] = cookie

    # Try to submit via the platform
    # First, get the page to find any CSRF token
    page = session.get(f"{PLATFORM_URL}/submit/tripletex", timeout=30)

    # Try direct API submission
    resp = session.post(
        f"{PLATFORM_URL}/api/tripletex/submit",
        json={"endpoint_url": endpoint_url, "api_key": api_key},
        headers={"Content-Type": "application/json"},
        timeout=30,
    )

    if resp.status_code == 200:
        data = resp.json()
        print(f"  Submitted! Response: {data}")
        return data

    # Try alternate endpoint formats
    for path in ["/api/submit/tripletex", "/tripletex/submit", "/api/submissions/tripletex"]:
        resp = session.post(
            f"{PLATFORM_URL}{path}",
            json={"endpoint_url": endpoint_url, "api_key": api_key},
            headers={"Content-Type": "application/json"},
            timeout=30,
        )
        if resp.status_code == 200:
            data = resp.json()
            print(f"  Submitted via {path}! Response: {data}")
            return data
        print(f"  Tried {path}: {resp.status_code}")

    print(f"  Could not find submission API endpoint.")
    print(f"  Status: {resp.status_code}")
    print(f"  Body: {resp.text[:500]}")
    return None


def wait_for_new_log(last_log_time: str, timeout: int = 300) -> dict | None:
    """Wait for a new processed log file to appear."""
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        logs = sorted(LOGS_DIR.glob("20*.json"), reverse=True)
        if logs:
            latest = logs[0]
            if latest.stem > last_log_time:
                time.sleep(2)  # Wait for file to finish writing
                return json.loads(latest.read_text())
        time.sleep(3)
    return None


def get_latest_log_time() -> str:
    """Get timestamp of the most recent log."""
    logs = sorted(LOGS_DIR.glob("20*.json"), reverse=True)
    return logs[0].stem if logs else "00000000_000000"


def analyze_result(log_data: dict) -> dict:
    """Analyze a submission log and return a summary."""
    prompt = log_data.get("prompt", "")[:100]
    duration = log_data.get("duration_s", 0)

    # Count successes/failures across all rounds
    api_calls = log_data.get("api_calls", [])
    fix_calls = log_data.get("fix_api_calls", [])

    all_calls = api_calls + fix_calls
    successes = sum(1 for c in all_calls if c.get("status") in (200, 201))
    failures = sum(1 for c in all_calls if c.get("status") not in (200, 201, None) or c.get("error"))

    errors = []
    for c in all_calls:
        if c.get("error"):
            errors.append(c["error"][:200])

    return {
        "prompt": prompt,
        "duration_s": duration,
        "total_calls": len(all_calls),
        "successes": successes,
        "failures": failures,
        "errors": errors,
    }


def run_loop(endpoint_url: str, count: int, api_key: str = ""):
    cookie = get_session()

    print(f"=== Tripletex Auto-Submit Loop ===")
    print(f"  Endpoint: {endpoint_url}")
    print(f"  Submissions: {count}")
    print(f"  Platform: {PLATFORM_URL}")
    print()

    for i in range(count):
        print(f"\n{'='*60}")
        print(f"  Submission {i+1}/{count} — {datetime.now().strftime('%H:%M:%S')}")
        print(f"{'='*60}")

        last_log = get_latest_log_time()

        # Submit
        result = submit(endpoint_url, cookie, api_key)
        if result is None:
            print("  Submission failed. Waiting 30s before retry...")
            time.sleep(30)
            continue

        # Wait for the agent to process
        print("  Waiting for agent to process...")
        log_data = wait_for_new_log(last_log, timeout=300)

        if log_data:
            summary = analyze_result(log_data)
            print(f"\n  --- Result ---")
            print(f"  Prompt: {summary['prompt']}...")
            print(f"  Duration: {summary['duration_s']}s")
            print(f"  API calls: {summary['successes']}/{summary['total_calls']} OK")
            if summary['errors']:
                print(f"  Errors:")
                for e in summary['errors'][:3]:
                    print(f"    - {e[:100]}")

            # Save result summary
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            (RESULTS_DIR / f"{ts}.json").write_text(
                json.dumps({"submission": i+1, **summary}, indent=2)
            )
        else:
            print("  Timed out waiting for log.")

        # Brief pause between submissions
        if i < count - 1:
            print(f"\n  Next submission in 10s...")
            time.sleep(10)

    print(f"\n\n=== Done! {count} submissions completed ===")
    print(f"Results in: {RESULTS_DIR}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True, help="Your tunnel URL (with /solve)")
    parser.add_argument("--count", type=int, default=32, help="Number of submissions")
    parser.add_argument("--api-key", default="", help="Optional API key")
    args = parser.parse_args()

    run_loop(args.url, args.count, args.api_key)
