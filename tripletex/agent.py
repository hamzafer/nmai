"""
Tripletex AI Accounting Agent — /solve endpoint.

Receives a task prompt (in 7 languages), uses Claude Opus to interpret it,
then executes the appropriate Tripletex API calls.
"""

import base64
import json
import re
import requests
from pathlib import Path

from .llm import call_claude

# System prompt for the LLM
SYSTEM_PROMPT = """You are an expert accounting agent that completes tasks in the Tripletex accounting system.

You receive a task description (possibly in Norwegian, English, Spanish, Portuguese, Nynorsk, German, or French) and must determine which Tripletex API calls to make.

You have access to the Tripletex v2 REST API. The base URL and session token will be provided.

IMPORTANT RULES:
- Authenticate with Basic Auth: username="0", password=<session_token>
- All API calls go through the provided base_url (proxy)
- POST/PUT requests use JSON body
- List responses are wrapped: {"fullResultSize": N, "values": [...]}
- Use ?fields=* to see all available fields
- Use ?fields=id,firstName,lastName for specific fields
- Some operations require creating prerequisites first (e.g., customer before invoice)

COMMON ENDPOINTS:
- GET/POST /employee — Manage employees
- GET/POST /customer — Manage customers
- GET/POST /product — Manage products
- GET/POST /invoice — Create and query invoices
- GET/POST /order — Manage orders
- GET/POST/PUT/DELETE /travelExpense — Travel expense reports
- GET/POST /project — Manage projects
- GET/POST /department — Manage departments
- GET /ledger/account — Query chart of accounts
- GET /ledger/posting — Query ledger postings
- GET/POST/DELETE /ledger/voucher — Manage vouchers

When you receive a task, respond with a JSON array of API calls to make, in order:
```json
[
  {
    "method": "POST",
    "path": "/employee",
    "body": {"firstName": "Ola", "lastName": "Nordmann", "email": "ola@example.org"},
    "description": "Create employee Ola Nordmann"
  },
  {
    "method": "PUT",
    "path": "/employee/{prev_id}",
    "body": {"id": "{prev_id}", "isAdministrator": true},
    "description": "Set administrator role",
    "depends_on": 0
  }
]
```

Use "{prev_id}" to reference the ID from a previous call's response. The "depends_on" field (0-indexed) indicates which previous call's response ID to use.

If a task includes file attachments, I'll describe their contents. Use that information in your API calls.

Think step by step about:
1. What entity needs to be created/modified?
2. What prerequisites are needed?
3. What's the correct order of API calls?
4. What fields are required?

Be precise and minimal — fewer API calls = better score.
"""


def extract_file_content(files: list) -> str:
    """Extract text description of attached files."""
    if not files:
        return ""

    descriptions = []
    for f in files:
        filename = f.get("filename", "unknown")
        mime = f.get("mime_type", "")
        data = base64.b64decode(f.get("content_base64", ""))

        if "pdf" in mime:
            descriptions.append(f"[PDF file: {filename}, {len(data)} bytes]")
            # For PDFs, we'd need a PDF parser — for now describe it
            # The LLM can work with the filename context
        elif "image" in mime:
            descriptions.append(f"[Image file: {filename}, {len(data)} bytes]")
        else:
            try:
                text = data.decode("utf-8")
                descriptions.append(f"File {filename}:\n{text[:2000]}")
            except UnicodeDecodeError:
                descriptions.append(f"[Binary file: {filename}, {len(data)} bytes]")

    return "\n\n".join(descriptions)


def execute_api_calls(plan: list, base_url: str, token: str) -> list:
    """Execute a sequence of API calls against the Tripletex API."""
    auth = ("0", token)
    results = []

    for i, call in enumerate(plan):
        method = call.get("method", "GET").upper()
        path = call.get("path", "")
        body = call.get("body")
        desc = call.get("description", "")
        depends_on = call.get("depends_on")

        # Replace {prev_id} references
        if depends_on is not None and depends_on < len(results):
            prev_result = results[depends_on]
            prev_id = prev_result.get("id")
            if prev_id is not None:
                path = path.replace("{prev_id}", str(prev_id))
                if body:
                    body_str = json.dumps(body).replace('"{prev_id}"', str(prev_id))
                    body_str = body_str.replace("{prev_id}", str(prev_id))
                    body = json.loads(body_str)

        url = f"{base_url}{path}"
        print(f"  [{i}] {method} {path} — {desc}")

        try:
            if method == "GET":
                resp = requests.get(url, auth=auth, timeout=30)
            elif method == "POST":
                resp = requests.post(url, auth=auth, json=body, timeout=30)
            elif method == "PUT":
                resp = requests.put(url, auth=auth, json=body, timeout=30)
            elif method == "DELETE":
                resp = requests.delete(url, auth=auth, timeout=30)
            else:
                print(f"    Unknown method: {method}")
                results.append({"error": f"Unknown method: {method}"})
                continue

            if resp.status_code in (200, 201):
                data = resp.json()
                # Extract ID from response
                value = data.get("value", data)
                result_id = value.get("id") if isinstance(value, dict) else None
                results.append({"status": resp.status_code, "id": result_id, "data": value})
                print(f"    OK ({resp.status_code}), id={result_id}")
            else:
                error_text = resp.text[:300]
                results.append({"status": resp.status_code, "error": error_text})
                print(f"    Error {resp.status_code}: {error_text}")

        except Exception as e:
            results.append({"error": str(e)})
            print(f"    Exception: {e}")

    return results


def parse_llm_plan(response: str) -> list:
    """Extract JSON array of API calls from LLM response."""
    # Try to find JSON array in the response
    # Look for ```json blocks first
    json_match = re.search(r'```(?:json)?\s*\n?(\[[\s\S]*?\])\s*\n?```', response)
    if json_match:
        try:
            return json.loads(json_match.group(1))
        except json.JSONDecodeError:
            pass

    # Try to find raw JSON array
    json_match = re.search(r'\[[\s\S]*\]', response)
    if json_match:
        try:
            return json.loads(json_match.group(0))
        except json.JSONDecodeError:
            pass

    print(f"  Failed to parse LLM response as JSON")
    return []


def solve_task(prompt: str, files: list, base_url: str, session_token: str) -> dict:
    """
    Main entry point: interpret the task prompt and execute API calls.
    """
    print(f"\n=== Solving task ===")
    print(f"  Prompt: {prompt[:200]}...")

    # Build the full prompt for the LLM
    file_context = extract_file_content(files)
    full_prompt = f"Task prompt:\n{prompt}"
    if file_context:
        full_prompt += f"\n\nAttached files:\n{file_context}"

    full_prompt += f"""

Base URL: {base_url}
Authentication: Basic Auth with username "0" and the session token.

Analyze this task and provide the JSON array of API calls needed to complete it.
Remember: be precise and minimal. Each unnecessary call or error hurts the score."""

    # Get LLM plan
    print("  Calling Claude Opus for task plan...")
    llm_response = call_claude(full_prompt, system=SYSTEM_PROMPT)
    print(f"  LLM response length: {len(llm_response)} chars")

    # Parse the plan
    plan = parse_llm_plan(llm_response)
    if not plan:
        print("  No valid plan from LLM, trying to recover...")
        # Try again with a simpler prompt
        retry_prompt = f"""The task is: {prompt}

Return ONLY a JSON array of Tripletex API calls. No explanation. Example format:
[{{"method": "POST", "path": "/employee", "body": {{"firstName": "Ola"}}, "description": "Create employee"}}]"""
        llm_response = call_claude(retry_prompt, system=SYSTEM_PROMPT)
        plan = parse_llm_plan(llm_response)

    print(f"  Plan: {len(plan)} API calls")

    # Execute the plan
    if plan:
        results = execute_api_calls(plan, base_url, session_token)
        success = sum(1 for r in results if r.get("status") in (200, 201))
        print(f"  Results: {success}/{len(results)} successful")

        # If any calls failed, try to fix with LLM
        failed = [r for r in results if r.get("error")]
        if failed and len(plan) < 10:
            print("  Some calls failed, asking LLM to fix...")
            fix_prompt = f"""Original task: {prompt}

The following API calls were made:
{json.dumps(plan, indent=2)}

These results came back:
{json.dumps(results, indent=2)}

Some calls failed. Provide a corrected JSON array of ONLY the calls that need to be retried/fixed.
Return [] if no fixes are needed."""

            fix_response = call_claude(fix_prompt, system=SYSTEM_PROMPT)
            fix_plan = parse_llm_plan(fix_response)
            if fix_plan:
                print(f"  Retrying {len(fix_plan)} fixed calls...")
                execute_api_calls(fix_plan, base_url, session_token)

    return {"status": "completed"}
