# Tripletex — AI Accounting Agent

## Overview

Build an AI agent that completes accounting tasks in Tripletex via their REST API.

- **Type**: Live HTTPS endpoint (`/solve`)
- **LLM**: Claude Opus 4.6 via Vertex AI
- **30 task types** x 56 variants (7 languages x 8 data sets)
- **Scoring**: Field-by-field verification + tier multiplier + efficiency bonus

## How It Works

1. Submit your HTTPS endpoint URL on the platform
2. Competition provisions a fresh Tripletex sandbox account
3. Sends a task prompt to your `/solve` endpoint
4. Your agent interprets the prompt, calls Tripletex API
5. Competition verifies the result field-by-field
6. Score updates on leaderboard

## Our System

```
tripletex/
├── __init__.py
├── __main__.py     # Entry point: python -m tripletex
├── server.py       # FastAPI /solve endpoint
├── agent.py        # Task solver: LLM planning + API execution
└── llm.py          # Claude Opus 4.6 via Vertex AI
```

### Running

```bash
# Start the server
python -m tripletex

# Expose via Cloudflare Tunnel
npx cloudflared tunnel --url http://localhost:8000

# Submit the tunnel URL at https://app.ainm.no/submit/tripletex
```

### LLM Setup

Uses Claude Opus 4.6 via Vertex AI (project: `ai-nm26osl-1717`, region: `us-east5`).
Requires `gcplab` gcloud config active:

```bash
gcloud config configurations activate gcplab
```

## Endpoint Spec

### POST /solve

**Request:**
```json
{
  "prompt": "Opprett en ansatt med navn Ola Nordmann...",
  "files": [{"filename": "faktura.pdf", "content_base64": "...", "mime_type": "application/pdf"}],
  "tripletex_credentials": {
    "base_url": "https://tx-proxy.ainm.no/v2",
    "session_token": "abc123..."
  }
}
```

**Response:**
```json
{"status": "completed"}
```

### Auth

Tripletex API: Basic Auth with username `0`, password = session_token.

## Scoring

- **Correctness**: Field-by-field checks (0-1)
- **Tier multiplier**: x1 (simple), x2 (multi-step), x3 (complex)
- **Efficiency bonus**: Perfect score + minimal API calls + zero errors = up to 2x tier score
- **Best per task**: Only improvements count, bad runs never lower score
- **Max score per task**: 6.0 (Tier 3, perfect + best efficiency)

## Task Categories

| Category | Examples |
|---|---|
| Employees | Create, set roles, update contact info |
| Customers & Products | Register customers, create products |
| Invoicing | Create invoices, register payments, credit notes |
| Travel Expenses | Register or delete travel expense reports |
| Projects | Create projects linked to customers |
| Corrections | Delete or reverse incorrect entries |
| Departments | Create departments, enable accounting modules |

## Rate Limits

- 3 concurrent submissions (verified teams)
- 5 per task per day
- 5 minute timeout per submission
