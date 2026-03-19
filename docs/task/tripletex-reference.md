# Tripletex Task Reference — NM i AI 2026

Full reference from the competition docs. Source URLs:
- https://app.ainm.no/docs/tripletex/overview
- https://app.ainm.no/docs/tripletex/sandbox
- https://app.ainm.no/docs/tripletex/endpoint
- https://app.ainm.no/docs/tripletex/scoring
- https://app.ainm.no/docs/tripletex/examples

---

## Overview

Competitors build an AI agent that completes accounting tasks in Tripletex by receiving task prompts (in 7 languages), using the Tripletex API to execute them, and earning scores based on correctness and efficiency.

### How It Works

1. Submit HTTPS endpoint URL on platform
2. Fresh Tripletex sandbox account provisioned
3. Random accounting task sent to `/solve` endpoint
4. Agent reads prompt, processes optional file attachments (PDFs, images)
5. Agent calls Tripletex API via proxy
6. Results verified field-by-field against expected values
7. Score updates on rolling leaderboard

*Each submission receives a brand new account — always starts from scratch.*

### Key Facts

| Aspect | Details |
|--------|---------|
| Task types | 30 different accounting tasks |
| Variants | 56 per task (7 languages x 8 data sets) |
| Languages | Norwegian, English, Spanish, Portuguese, Nynorsk, German, French |
| Timeout | 5 minutes per submission |
| API | Tripletex v2 REST API via authenticated proxy |
| Scoring | Field-by-field checks + efficiency bonus |
| Score range | 0.0 to 6.0 |
| Files | Some tasks include PDF or image attachments |

### Task Categories

- **Employees** — Create employees, set roles, update contact info
- **Customers & Products** — Register customers, create products
- **Invoicing** — Create invoices, register payments, issue credit notes
- **Travel Expenses** — Register or delete travel expense reports
- **Projects** — Create projects linked to customers
- **Corrections** — Delete or reverse incorrect entries
- **Departments** — Create departments, enable accounting modules

Tasks range from simple single-API-call operations to multi-step workflows requiring resource creation and linking.

---

## Sandbox

### Getting Your Sandbox

1. Navigate to the Tripletex submission page on the platform
2. Click "Get Sandbox Account"
3. Sandbox is provisioned instantly

Teams receive: a Tripletex UI URL, an API base URL, and a session token.

### Logging Into the Web UI

1. Enter email from the sandbox card
2. Click "Forgot password" on first login
3. Set up Visma Connect credentials
4. Use those same credentials for all Tripletex test accounts

### Sandbox vs Competition

| Aspect | Sandbox | Competition |
|--------|---------|------------|
| Account persistence | Yours to keep | Fresh per submission |
| API access | Direct to Tripletex | Via authenticated proxy |
| Data | Accumulates over time | Starts empty each time |
| Scoring | None | Automated field-by-field |

### Tips

- Create test data manually in the UI, then query via API
- Practice operations your agent will need
- Sandbox token expires March 31, 2026
- Teams share one sandbox across all members

---

## Endpoint Specification

### Core Requirements

Single HTTPS endpoint accepting POST to `/solve`:
- **Method:** POST
- **Content-Type:** application/json
- **Timeout:** 300 seconds (5 minutes)

### Request Format

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
|-------|------|-------------|
| `prompt` | string | Task in natural language |
| `files` | array | Attachments (PDFs, images) — may be empty |
| `files[].filename` | string | Original filename |
| `files[].content_base64` | string | Base64-encoded file content |
| `files[].mime_type` | string | MIME type (application/pdf, image/png, etc.) |
| `tripletex_credentials.base_url` | string | Proxy API URL |
| `tripletex_credentials.session_token` | string | Session token for authentication |

### Response Format

```json
{"status": "completed"}
```

Must return HTTP 200.

### Authentication

Basic Auth:
- **Username:** `0` (zero)
- **Password:** the `session_token` value

```python
response = requests.get(
    f"{base_url}/employee",
    auth=("0", session_token),
    params={"fields": "id,firstName,lastName,email"}
)
```

### Optional API Key

If set during submission, sent as `Authorization: Bearer <your-api-key>`.

### Hard Requirements

- Endpoint must be **HTTPS**
- Must respond within **5 minutes** (300 seconds)
- Must return `{"status": "completed"}` with HTTP 200
- All Tripletex API calls must route through the provided `base_url` (proxy)

---

## Tripletex API Reference

All standard Tripletex v2 endpoints available through proxy:

| Endpoint | Methods | Description |
|----------|---------|-------------|
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

- Use `fields` parameter: `?fields=id,firstName,lastName,*`
- Pagination: `?from=0&count=100`
- POST/PUT use JSON body
- DELETE uses ID in path: `DELETE /employee/123`
- List responses wrapped: `{"fullResultSize": N, "values": [...]}`

---

## Scoring

### Field-by-Field Verification (Correctness)

The system verifies agent-created/modified data through API queries. Tasks have specific checks with point values. Example — "Create employee" task (max 10 points):

- 2 points: employee found
- 1 point each: correct first name, last name, email
- 5 points: administrator role assignment

Normalized: `correctness = points_earned / max_points`

### Tier Multiplier System

| Tier | Multiplier | Examples | Opens |
|------|-----------|----------|-------|
| Tier 1 | x1 | Create employee, create customer | From start |
| Tier 2 | x2 | Create invoice, register payment | Early Friday |
| Tier 3 | x3 | Complex multi-step workflows | Early Saturday |

Perfect Tier 2: 1.0 x 2 = 2.0 base score.

### Efficiency Bonus

Perfect correctness (1.0) receives bonuses up to **double** the tier score, based on:
- **Call efficiency**: API calls relative to optimal solutions
- **Error cleanliness**: Fewer 4xx errors = less penalty

Tier 2 range: 0.0 (failed) to 4.0 (perfect efficiency, zero errors). Benchmarks recalculate every 12 hours.

### Best Score Per Task

All-time best per task. Bad runs never lower your score — only improvements count. 30 independent tasks.

### Rate Limits

- Verified teams: 3 concurrent submissions, 4 per task daily
- Unverified teams: 1 concurrent submission, 2 per task daily

---

## Examples

### Minimal /solve Endpoint

```python
import base64
from pathlib import Path
import requests
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI()

@app.post("/solve")
async def solve(request: Request):
    body = await request.json()
    prompt = body["prompt"]
    files = body.get("files", [])
    creds = body["tripletex_credentials"]

    base_url = creds["base_url"]
    token = creds["session_token"]
    auth = ("0", token)

    for f in files:
        data = base64.b64decode(f["content_base64"])
        Path(f["filename"]).write_bytes(data)

    # TODO: Use an LLM to interpret the prompt and execute
    # the appropriate Tripletex API calls

    return JSONResponse({"status": "completed"})
```

**Deployment:** `pip install fastapi uvicorn requests` then `uvicorn main:app --host 0.0.0.0 --port 8000`. Expose via `npx cloudflared tunnel --url http://localhost:8000`.

### Common Task Patterns

- Create single entity (e.g., employee)
- Create with linking (customer -> order -> invoice)
- Modify existing records
- Delete/reverse operations
- Multi-step setup workflows

### Building an Effective Agent

1. **Parse the prompt** — Use an LLM to extract task type, entity names, field values, and relationships
2. **Handle files** — Extract data from base64-encoded PDFs or documents
3. **Map to API calls** — Sequence requests properly (prerequisites must exist first)
4. **Verify your work** — Query back to confirm successful creation
5. **Handle errors** — Parse them to retry with corrections

### Optimization Tips (scoring above 1.0)

- **Plan before calling** — fully parse prompts before API requests
- **Avoid trial-and-error** — every 4xx error reduces efficiency bonuses
- **Minimize GET calls** — don't fetch unnecessary data
- **Batch where possible** — use endpoints accepting lists
- **Read error messages** — Tripletex provides specific correction guidance

Maximum efficiency bonus applies only to perfect correctness submissions. Higher-tier tasks allow scores up to 6.0.
