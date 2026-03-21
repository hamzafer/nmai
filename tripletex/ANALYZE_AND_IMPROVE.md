# Analyze & Improve — For Claude with Full Bash Permissions

You are a Claude session with dangerous bash permissions. Your job is to monitor competition results and improve the Tripletex agent code.

## Context

- Agent server: `python -m tripletex` (running on port 8000)
- Tunnel: `npx cloudflared tunnel --url http://localhost:8000` (running)
- Another Claude session is submitting via Chrome every ~60 seconds
- Competition logs land in `tripletex/logs/` (processed) and `tripletex/logs/raw/` (raw requests)
- Only competition logs have `tx-proxy` in the `base_url` field

## Your Loop (run every 2-3 minutes)

### 1. Check latest results

```bash
for f in $(ls -t tripletex/logs/*.json | head -5); do
  python3 -c "
import json,sys
d=json.load(open('$f'))
if 'tx-proxy' not in d.get('base_url',''): sys.exit()
calls=d.get('api_calls',[])
plan=d.get('plan',[])
ok=sum(1 for c in calls if c.get('status') in (200,201))
fix=d.get('fix_api_calls',[])
print(f'$(basename $f) | {ok}/{len(calls)} | {d[\"prompt\"][:80]}')
for i,c in enumerate(calls):
    s=c.get('status','err')
    if s not in (200,201):
        desc=plan[i].get('description','')[:50] if i<len(plan) else ''
        err=c.get('error','')[:100] if c.get('error') else ''
        print(f'  FAIL [{i}] {s} {desc} | {err}')
" 2>/dev/null
done
```

### 2. Identify failures

Look at the error messages. Common patterns:
- `"Validering feilet"` = validation error — read the `validationMessages` for the field
- `"Request mapping failed"` = wrong field name or type
- `"bankkontonummer"` = company needs bank account before invoicing
- `"Det finnes allerede en bruker"` = email already exists, need to GET instead of POST
- `500` errors = usually wrong endpoint or invalid parameters
- `404` = wrong URL pattern

### 3. Fix the system prompt

The LLM's behavior is controlled by `SYSTEM_PROMPT` in `tripletex/agent.py`. Edit it to:
- Add missing required fields
- Add new endpoint documentation
- Add new patterns (examples the LLM can follow)
- Update the fix prompt common issues section

The server runs with `reload=True` so changes take effect automatically.

### 4. Test locally before it goes live

```bash
# Start mock on port 9999 (if not running)
python3 -c "
from http.server import HTTPServer
from tripletex.test_local import MockTripletexHandler
HTTPServer(('0.0.0.0', 9999), MockTripletexHandler).serve_forever()
" &

# Replay a failed task
curl -s -X POST http://localhost:8000/solve -H 'Content-Type: application/json' -d '{
  "prompt": "THE FAILED PROMPT HERE",
  "files": [],
  "tripletex_credentials": {"base_url": "http://localhost:9999/v2", "session_token": "mock"}
}'
```

### 5. Key files

- `tripletex/agent.py` — System prompt, API executor, solve loop (MAIN FILE TO EDIT)
- `tripletex/server.py` — FastAPI server (rarely needs changes)
- `tripletex/llm.py` — Claude Opus via Vertex AI (don't touch)
- `tripletex/test_local.py` — Local test suite

### 6. What NOT to do

- Don't restart the server (hot-reload handles it)
- Don't touch the tunnel
- Don't submit to the competition yourself
- Don't delete logs
- Don't change llm.py

## Known Issues (already in prompt, may still fail on edge cases)

1. **Invoice needs bank account** — prompt tells LLM to PUT /company with bankAccountNumber
2. **Payment needs paymentTypeId** — prompt tells LLM to GET /ledger/paymentType first
3. **Employee email exists** — prompt tells LLM to GET existing employee
4. **Project needs startDate** — already added as required field
5. **Invoice dates must be query params** — already documented with examples
