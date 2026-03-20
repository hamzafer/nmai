# Tripletex Agent — Local Testing

## Quick Start

```bash
# Terminal 1: Start the agent server
source .venv/bin/activate
python -m tripletex

# Terminal 2: Run all tests
source .venv/bin/activate
python -m tripletex.test_local
```

## What It Does

1. Starts a **mock Tripletex API** on port 9999 (in-memory, no external calls)
2. Sends test tasks to the agent running on port 8000
3. Agent calls Claude Opus → gets a plan → executes against the mock API
4. Verifies the mock DB has the right entities
5. Reports pass/fail per test

## Test Cases

| # | Name | What it checks |
|---|------|---------------|
| 1 | Create employee (simple) | POST /employee with correct firstName, lastName |
| 2 | Create employee with admin role | POST /employee + PUT to set admin flag |
| 3 | Create customer | POST /customer with correct name |

## Adding Tests

Edit `tripletex/test_local.py` — add to the `TESTS` list:

```python
{
    "name": "Your test name",
    "prompt": "The task prompt (any language)",
    "files": [],  # or [{"filename": "x.pdf", "content_base64": "...", "mime_type": "application/pdf"}]
    "check": lambda: any(
        e.get("someField") == "expectedValue"
        for e in MOCK_DB.get("entity_name", [])
    ),
},
```

## Logs

Every test run saves full traces to `tripletex/logs/`:

- **`logs/raw/<timestamp>.json`** — raw incoming request
- **`logs/<timestamp>.json`** — full trace:
  - Prompt received
  - LLM calls (prompt + response text)
  - Parsed plan (JSON array of API calls)
  - API execution results (status codes, response data, errors)
  - Fix attempts (if any calls failed)
  - Total duration

## Reviewing Logs

```bash
# Latest log
cat tripletex/logs/$(ls -t tripletex/logs/*.json | head -1)

# Or use jq
jq '.prompt, .plan, .api_calls' tripletex/logs/*.json

# See all prompts received
jq -r '.prompt' tripletex/logs/*.json

# See failed API calls
jq '.api_calls[] | select(.error)' tripletex/logs/*.json
```

## Competition Submission

Once tests pass:

```bash
./tripletex/launch.sh
# Paste the tunnel URL at https://app.ainm.no/submit/tripletex
```

Real competition submissions also get logged — same format, same directory. Use the logs to iterate.
