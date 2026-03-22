"""Robust poller — checks every 30s, plays unplayed rounds."""
import os, time, subprocess, sys, requests
from pathlib import Path

with open(Path(__file__).parent / '.env') as f:
    for line in f:
        if 'ASTAR_TOKEN' in line:
            token = line.split('=', 1)[1].strip()

s = requests.Session()
s.headers['Authorization'] = f'Bearer {token}'
BASE = 'https://api.ainm.no'
played = set()

print('Poller started', flush=True)
while True:
    try:
        rounds = s.get(f'{BASE}/astar-island/rounds', timeout=10).json()
        active = [r for r in rounds if r.get('status') == 'active']
        if active:
            r = active[0]
            rn = r.get('round_number', 0)
            budget = s.get(f'{BASE}/astar-island/budget', timeout=10).json()
            if budget['queries_used'] < budget['queries_max'] and rn not in played:
                print(f'ROUND {rn} UNPLAYED! Playing...', flush=True)
                result = subprocess.run(
                    [sys.executable, str(Path(__file__).parent / 'astar.py')],
                    capture_output=True, text=True, timeout=600,
                    cwd=str(Path(__file__).parent)
                )
                print(result.stdout[-500:], flush=True)
                if result.returncode != 0:
                    print(f'STDERR: {result.stderr[:300]}', flush=True)
                played.add(rn)
            elif rn not in played and budget['queries_used'] >= budget['queries_max']:
                played.add(rn)
    except Exception as e:
        print(f'Error: {e.__class__.__name__}: {e}', flush=True)
    time.sleep(30)
