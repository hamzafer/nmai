# NM i AI 2026 — Tripletex Competition Reference

## Overview
Teams build AI agents that complete accounting tasks in the Tripletex system. The agent receives task
prompts in one of seven languages, makes API calls to complete the work, and receives scores based on
correctness and efficiency.

## Core Mechanics

**How It Works:**
1. Submit an HTTPS endpoint URL to the platform
2. System provisions a fresh Tripletex sandbox account for each submission
3. A randomly selected accounting task is sent to the agent's `/solve` endpoint
4. Agent interprets the prompt, processes any attached files (PDFs, images), and calls the Tripletex API
5. System verifies results field-by-field against expected values
6. Score updates on the leaderboard

Each submission starts with a brand new account—no persistent state between attempts.

## Key Facts

- **Task Types:** 30 different accounting operations
- **Variants:** 56 per task (7 languages × 8 data sets)
- **Languages:** Norwegian, English, Spanish, Portuguese, Nynorsk, German, French
- **Timeout:** 300 seconds (5 minutes) per submission
- **API:** Tripletex v2 REST API via authenticated proxy
- **Score Range:** 0.0 (failed) to 6.0 (perfect Tier 3 with best efficiency)

## Task Categories

Common task types include:

- **Employees:** Create, update contact info, assign roles
- **Customers & Products:** Register customers, create product catalogs
- **Invoicing:** Create invoices, register payments, issue credit notes
- **Travel Expenses:** Register or delete expense reports
- **Projects:** Create and link projects to customers
- **Corrections:** Delete or reverse incorrect entries
- **Departments:** Create departments, enable accounting modules

Tasks range from single-call operations to multi-step workflows requiring resource creation and linking.

## Endpoint Specification

**POST /solve**

**Request:**
- Content-Type: application/json
- Includes: task prompt (string), optional file attachments (base64-encoded), Tripletex session token, base URL for API proxy

**Request Body Fields:**
- `prompt`: Task description string
- `files`: Optional array of attachments
  - `filename`: Original filename
  - `content_base64`: Base64-encoded file content
  - `mime_type`: File type (e.g., "application/pdf", "image/png")
- `tripletex_credentials`: Authentication details
  - `base_url`: Proxy API URL (use instead of standard Tripletex URL)
  - `session_token`: Authentication token

**Response:**
Return `{"status": "completed"}` with HTTP 200.

**Authentication:**
- Basic Auth: username `0`, password = session token
- Optional API key as Bearer token for endpoint protection

## Scoring System

**Correctness (Field-by-Field Verification):**
Each task specifies which fields to verify. System checks created/modified entities against expected
values. Raw score = points earned / maximum points (0–1 range).

**Tier Multiplier:**
- Tier 1: 1× multiplier
- Tier 2: 2× multiplier
- Tier 3: 3× multiplier

**Efficiency Bonus:**
Available only for perfect correctness (1.0 score). Bonus depends on:
- **Call efficiency:** Fewer write calls (POST, PUT, DELETE, PATCH) compared to optimal solution = higher bonus. GET requests don't count.
- **Error cleanliness:** Fewer 4xx errors (400, 404, 422, etc.) = better score. Trial-and-error penalizes the bonus.

**Formula:** `final_score = correctness × tier × (1 + efficiency_bonus)`

**Examples (Tier 2):**
- 80% checks passed: 1.6 points
- Perfect + many errors: ~2.1 points
- Perfect + efficient + few errors: ~2.6 points
- Perfect + best efficiency + zero errors: 4.0 points

Benchmarks recalculate every 12 hours as teams improve.

**Best Score Per Task:**
Your all-time best score per task is kept. Bad runs never lower your score.

**Leaderboard:**
Total score = sum of best scores across all 30 task types.

## Tier Release Schedule

- **Tier 1 & 2:** Open at competition start
- **Tier 3:** Opens early Saturday (check documentation for updates)

This staged approach allows teams to build solid agents on simpler tasks before tackling complex scenarios.

## Rate Limits & Task Assignment

| Limit | Verified | Unverified |
|-------|----------|------------|
| Concurrent submissions | 3 | 1 |
| Per task per day | 10 | 3 |

- Tasks are weighted toward ones you've attempted less
- Over many submissions, you'll encounter all 30 task types
- Each task has 56 unique variants, so repetition is rare

## API Tips

- Use `fields` parameter to select specific fields: `?fields=id,firstName,lastName,*`
- Use `count` and `from` for pagination: `?from=0&count=100`
- POST/PUT requests take JSON bodies
- DELETE requests use ID in URL path: `DELETE /employee/123`
- List responses are wrapped: `{"fullResultSize": N, "values": [...]}`

## Common Task Patterns

| Pattern | Example | API Flow |
|---------|---------|----------|
| Single entity | "Create employee Ola Nordmann" | POST /employee |
| With linking | "Create invoice for customer" | GET /customer → POST /order → POST /invoice |
| Modify existing | "Add phone to contact" | GET /customer → PUT /customer/{id} |
| Delete/reverse | "Delete travel expense" | GET /travelExpense → DELETE /travelExpense/{id} |
| Multi-step setup | "Register payment" | POST /customer → POST /invoice → POST /payment |

## Agent Building Strategy

Effective agents should:
1. Parse the prompt to extract task type, entity names, and field values
2. Handle optional file attachments (PDFs with invoices, contracts, expense data)
3. Map requirements to Tripletex API endpoints in correct order
4. Verify work by querying back after creation
5. Handle error messages to retry with corrections
6. Minimize API calls and avoid trial-and-error approaches

## Optimization Tips

- **Plan before calling:** Fully parse prompt before making requests
- **Avoid trial-and-error:** Each 4xx error reduces efficiency bonus; validate inputs first
- **Minimize writes:** Don't create unnecessary entities; use response IDs immediately
- **Read error messages:** Tripletex specifies exactly what's wrong; fix in one retry
- **GETs are free:** Only write calls (POST/PUT/DELETE/PATCH) count against efficiency
