# Tripletex — AI Accounting Agent

## Overview

Build an AI agent that completes accounting tasks in Tripletex via their REST API.

- **Task type**: Live HTTPS endpoint (`/solve`)
- **Platform**: [app.ainm.no](https://app.ainm.no)
- **LLM**: Claude Opus 4.6 via Vertex AI
- **30 task types** x 56 variants (7 languages x 8 data sets)
- **Languages**: Norwegian, English, Spanish, Portuguese, Nynorsk, German, French
- **Timeout**: 5 minutes per submission
- **Score range**: 0.0 to 6.0 (perfect Tier 3 + best efficiency)

## How It Works

1. Submit your HTTPS endpoint URL on the platform
2. Competition provisions a **fresh** Tripletex sandbox account
3. Sends a randomly selected accounting task to your `/solve` endpoint
4. Your agent reads the prompt, optionally processes attached files (PDFs, images)
5. Your agent calls the Tripletex API via a proxy to complete the task
6. Competition verifies the result field-by-field against expected values
7. Score updates on leaderboard

Each submission gets a brand new Tripletex account — always starting from scratch.

## Endpoint Specification

### POST /solve

**Timeout**: 300 seconds (5 minutes)

**Request:**
```json
{
  "prompt": "Opprett en ansatt med navn Ola Nordmann, ola@example.org. Han skal være kontoadministrator.",
  "files": [
    {
      "filename": "faktura.pdf",
      "content_base64": "JVBERi0xLjQg...",
      "mime_type": "application/pdf"
    }
  ],
  "tripletex_credentials": {
    "base_url": "https://tx-proxy.ainm.no/v2",
    "session_token": "abc123..."
  }
}
```

| Field | Type | Description |
|---|---|---|
| `prompt` | string | The task in natural language (one of 7 languages) |
| `files` | array | Attachments (PDFs, images) — may be empty |
| `files[].filename` | string | Original filename |
| `files[].content_base64` | string | Base64-encoded file content |
| `files[].mime_type` | string | MIME type |
| `tripletex_credentials.base_url` | string | Proxy API URL — use this, not standard Tripletex URL |
| `tripletex_credentials.session_token` | string | Session token for authentication |

**Response:**
```json
{"status": "completed"}
```

### Authentication

Tripletex API uses **Basic Auth**:
- **Username**: `0` (zero)
- **Password**: the `session_token` value

```python
import requests
response = requests.get(
    f"{base_url}/employee",
    auth=("0", session_token),
    params={"fields": "id,firstName,lastName,email"}
)
```

### Optional API Key

If you set an API key when submitting, competition sends it as `Authorization: Bearer <your-api-key>`.

## Tripletex API Reference

All standard Tripletex v2 endpoints available through the proxy:

| Endpoint | Methods | Description |
|---|---|---|
| `/employee` | GET, POST, PUT | Manage employees |
| `/customer` | GET, POST, PUT | Manage customers |
| `/product` | GET, POST | Manage products |
| `/invoice` | GET, POST | Create and query invoices |
| `/order` | GET, POST | Manage orders |
| `/travelExpense` | GET, POST, PUT, DELETE | Travel expense reports |
| `/project` | GET, POST | Manage projects |
| `/department` | GET, POST | Manage departments |
| `/ledger/account` | GET | Query chart of accounts |
| `/ledger/posting` | GET | Query ledger postings |
| `/ledger/voucher` | GET, POST, DELETE | Manage vouchers |

### API Tips

- Use `?fields=*` to see all available fields
- Use `?fields=id,firstName,lastName` for specific fields
- Pagination: `?from=0&count=100`
- POST/PUT take JSON body
- DELETE uses ID in URL path: `DELETE /employee/123`
- List responses wrapped: `{"fullResultSize": N, "values": [...]}`

## Scoring

### Correctness (field-by-field)

Each task has specific checks with point values. Example for "Create employee" (max 10 points):

| Check | Points |
|---|---|
| Employee found | 2 |
| Correct first name | 1 |
| Correct last name | 1 |
| Correct email | 1 |
| Administrator role assigned | 5 |

Normalized: `correctness = points_earned / max_points`

### Tier Multiplier

| Tier | Multiplier | Examples |
|---|---|---|
| Tier 1 | x1 | Create employee, create customer |
| Tier 2 | x2 | Create invoice, register payment |
| Tier 3 | x3 | Complex multi-step workflows |

### Efficiency Bonus (perfect scores only)

If correctness = 1.0, you get a bonus based on:
- **Call efficiency**: fewer API calls vs best known solution = higher bonus
- **Error cleanliness**: fewer 4xx errors = higher bonus

Can up to **double** your tier score.

| Scenario (Tier 2) | Score |
|---|---|
| Failed all checks | 0.0 |
| 80% passed | 1.6 |
| Perfect, many errors | ~2.1 |
| Perfect, efficient, few errors | ~2.6 |
| Perfect, best efficiency, zero errors | 4.0 |

**Benchmarks recalculate every 12 hours.**

### Tier Release Schedule

- **Tier 1**: Available from competition start
- **Tier 2**: Opens early Friday
- **Tier 3**: Opens early Saturday

### Leaderboard

Total score = sum of best scores across all 30 task types.

## Task Categories & Patterns

| Pattern | Example | API Flow |
|---|---|---|
| Single entity | "Create employee Ola" | POST /employee |
| Create with linking | "Create invoice for customer" | GET /customer → POST /order → POST /invoice |
| Modify existing | "Add phone to contact" | GET /customer → PUT /customer/{id} |
| Delete/reverse | "Delete travel expense" | GET /travelExpense → DELETE /travelExpense/{id} |
| Multi-step | "Register payment" | POST /customer → POST /invoice → POST /payment |

## Sandbox Account

Free sandbox for exploration:

| | Sandbox | Competition |
|---|---|---|
| URL | `https://kkpqfuj-amager.tripletex.dev` | Via proxy |
| Account | Persistent, yours to keep | Fresh per submission |
| Data | Accumulates | Starts empty each time |
| Scoring | None | Automated |

Get your sandbox at the Tripletex submission page. Token expires 2026-03-31.

## Common Errors

| Error | Cause | Fix |
|---|---|---|
| 401 Unauthorized | Wrong auth format | Basic Auth: username `0`, password = session token |
| 404 Not Found | Wrong endpoint | Check Tripletex v2 API docs |
| 422 Validation Error | Missing required fields | Read error message — it specifies which fields |
| Empty `values` array | No results | Broader search params |
| Timeout (5 min) | Agent too slow | Optimize API calls |

## Rate Limits

| Limit | Verified | Unverified |
|---|---|---|
| Concurrent submissions | 3 | 1 |
| Per task per day | 4 | 2 |

## Optimization Tips

- **Plan before calling** — parse prompt fully before making API calls
- **Avoid trial-and-error** — every 4xx error reduces efficiency bonus
- **Minimize GET calls** — use response IDs from creation, don't refetch
- **Read error messages** — Tripletex tells you exactly what's wrong
- **Norwegian chars** (æ, ø, å) work fine as UTF-8
- **Sandbox starts empty** — create prerequisites before dependent entities
- **All API calls logged** — check submissions view for debugging

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
# 1. Activate gcloud config
gcloud config configurations activate gcplab

# 2. Start the server
python -m tripletex

# 3. Expose via Cloudflare Tunnel (another terminal)
npx cloudflared tunnel --url http://localhost:8000

# 4. Submit the tunnel URL at https://app.ainm.no/submit/tripletex
```
