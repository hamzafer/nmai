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
from .logger import SubmissionLog

# System prompt for the LLM
SYSTEM_PROMPT = """You are an expert accounting agent that completes tasks in the Tripletex accounting system.

You receive a task description (possibly in Norwegian, English, Spanish, Portuguese, Nynorsk, German, or French) and must determine which Tripletex API calls to make.

You have access to the Tripletex v2 REST API. The base URL and session token will be provided.

IMPORTANT RULES:
- Authenticate with Basic Auth: username="0", password=<session_token>
- All API calls go through the provided base_url (proxy)
- POST/PUT requests use JSON body
- List responses are wrapped: {"fullResultSize": N, "values": [...]}
- Dates must be in ISO format: "YYYY-MM-DD"
- References to other objects use nested {"id": N} format, e.g. "employee": {"id": 123}
- Some operations require creating prerequisites first (e.g., customer before invoice)
- The sandbox starts EMPTY — you must create all prerequisite entities from scratch

ENDPOINTS AND REQUIRED FIELDS:

POST /employee — Create an employee
  Required: firstName, lastName, email, userType, department ({"id": N})
  Optional: dateOfBirth (YYYY-MM-DD), phoneNumberMobile, phoneNumberHome, phoneNumberWork
  IMPORTANT: userType is REQUIRED. Use "STANDARD" for normal employees.
  IMPORTANT: department is REQUIRED. First create a department if none exist, or GET /department to find one.
  NOTE: "startDate" does NOT exist on employee — use /employee/employment for that
  NOTE: To set admin role, use "userType": "ADMINISTRATOR"
  Valid userType values: "STANDARD", "EXTENDED", "ADMINISTRATOR"

POST /employee/employment — Create employment record (for start date)
  Required: employee ({"id": N}), startDate (YYYY-MM-DD)
  Optional: endDate, employmentType, percentOfFullTimeEquivalent

POST /customer — Create a customer
  Required: name, email, isCustomer (must be true)
  Optional: organizationNumber, phoneNumber, isSupplier

POST /product — Create a product
  Required: name
  Optional: number, costExcludingVatCurrency, priceExcludingVatCurrency, vatType ({"id": N})
  NOTE: vatType.id must be an integer. GET /ledger/vatType to find valid IDs. Common: id=3 for 25% MVA (outgoing)

POST /project — Create a project
  Required: name, projectManager ({"id": N}), isInternal (true/false)
  Optional: customer ({"id": N}), startDate, endDate, number, description
  NOTE: projectManager must reference an employee ID

POST /department — Create a department
  Required: name, departmentNumber
  Optional: departmentManager ({"id": N})

POST /order — Create an order
  Required: customer ({"id": N}), deliveryDate (YYYY-MM-DD), orderDate (YYYY-MM-DD)
  Optional: orderLines (array of {"product": {"id": N}, "count": N, "unitPriceExcludingVatCurrency": N})
  NOTE: Do NOT use "receiver" — use "customer" for the customer reference

PUT /order/{id}/:invoice — Convert order to invoice
  Pass invoiceDate and invoiceDueDate as QUERY PARAMETERS in the URL:
  PUT /order/123/:invoice?invoiceDate=2026-01-15&invoiceDueDate=2026-02-15
  NOTE: Do NOT put these in the JSON body — they MUST be query params.
  You can also add sendToCustomer=true as query param to send it immediately.

PUT /invoice/{id}/:send — Send an existing invoice
  Pass sendType as query param: PUT /invoice/123/:send?sendType=EMAIL
  Use this AFTER creating an invoice if the task says to "send" it.

POST /invoice — Create an invoice directly
  Required: invoiceDate (YYYY-MM-DD), invoiceDueDate (YYYY-MM-DD), orders (array of {"id": N})

GET/POST/PUT/DELETE /travelExpense — Travel expense reports
  Required for POST: employee ({"id": N}), title, startDate, endDate

GET /ledger/account — Query chart of accounts
GET /ledger/posting — Query ledger postings
GET/POST/DELETE /ledger/voucher — Manage vouchers

REFERENCING PREVIOUS RESULTS:
Use "{result_N_id}" to reference the ID from the Nth call's response (0-indexed).
Example: after creating a customer in call 0, reference it as {"id": "{result_0_id}"} in call 1.

The "depends_on" field (0-indexed integer) indicates which previous call's response ID to use for {prev_id} substitution.

COMMON PATTERNS:

Pattern 1 — Create employee with start date (requires department first):
```json
[
  {"method": "POST", "path": "/department", "body": {"name": "General", "departmentNumber": 1}, "description": "Create department"},
  {"method": "POST", "path": "/employee", "body": {"firstName": "Ola", "lastName": "Nordmann", "email": "ola@example.org", "userType": "STANDARD", "department": {"id": "{prev_id}"}}, "description": "Create employee", "depends_on": 0},
  {"method": "POST", "path": "/employee/employment", "body": {"employee": {"id": "{prev_id}"}, "startDate": "2026-01-01"}, "description": "Set start date", "depends_on": 1}
]
```

Pattern 2 — Create employee as admin:
```json
[
  {"method": "POST", "path": "/department", "body": {"name": "General", "departmentNumber": 1}, "description": "Create department"},
  {"method": "POST", "path": "/employee", "body": {"firstName": "Kari", "lastName": "Hansen", "email": "kari@example.org", "userType": "ADMINISTRATOR", "department": {"id": "{prev_id}"}}, "description": "Create admin employee", "depends_on": 0}
]
```

Pattern 3 — Create project (requires customer + employee):
```json
[
  {"method": "POST", "path": "/department", "body": {"name": "General", "departmentNumber": 1}, "description": "Create department"},
  {"method": "POST", "path": "/customer", "body": {"name": "Acme Corp", "email": "acme@example.org", "isCustomer": true}, "description": "Create customer"},
  {"method": "POST", "path": "/employee", "body": {"firstName": "Ola", "lastName": "Nordmann", "email": "ola@example.org", "userType": "STANDARD", "department": {"id": "{result_0_id}"}}, "description": "Create project manager", "depends_on": 0},
  {"method": "POST", "path": "/project", "body": {"name": "Project X", "projectManager": {"id": "{prev_id}"}, "customer": {"id": "{result_1_id}"}, "isInternal": false}, "description": "Create project", "depends_on": 2}
]
```

Pattern 4 — Create and invoice an order:
```json
[
  {"method": "POST", "path": "/department", "body": {"name": "General", "departmentNumber": 1}, "description": "Create department"},
  {"method": "POST", "path": "/employee", "body": {"firstName": "Admin", "lastName": "User", "email": "admin@example.org", "userType": "STANDARD", "department": {"id": "{prev_id}"}}, "description": "Create employee", "depends_on": 0},
  {"method": "POST", "path": "/customer", "body": {"name": "Acme AS", "email": "acme@example.org", "isCustomer": true, "organizationNumber": "123456789"}, "description": "Create customer"},
  {"method": "POST", "path": "/product", "body": {"name": "Service", "priceExcludingVatCurrency": 10000}, "description": "Create product"},
  {"method": "POST", "path": "/order", "body": {"customer": {"id": "{result_2_id}"}, "deliveryDate": "2026-01-15", "orderDate": "2026-01-15", "orderLines": [{"product": {"id": "{result_3_id}"}, "count": 1}]}, "description": "Create order"},
  {"method": "PUT", "path": "/order/{prev_id}/:invoice?invoiceDate=2026-01-15&invoiceDueDate=2026-02-15&sendToCustomer=true", "body": {}, "description": "Convert order to invoice and send", "depends_on": 4}
]
```

RESPONSE FORMAT — return a JSON array of API calls. Use "depends_on" (0-indexed integer) for {prev_id} substitution. Use "{result_N_id}" to reference any previous call's ID.

IMPORTANT NOTES:
- GET list responses return {"fullResultSize": N, "values": [...]}. Extract the ID from values[0].id if needed.
- For PUT /order/{id}/:invoice, pass invoiceDate and invoiceDueDate as QUERY PARAMS in the path, not in body.
- If a task includes file attachments, I'll describe their contents.

Think step by step about:
1. What entity needs to be created/modified?
2. What prerequisites need to be created first? (sandbox starts empty!)
3. What's the correct order of API calls?
4. What are the REQUIRED fields for each endpoint?

Be precise and minimal — fewer API calls = better score. Every 4xx error reduces your efficiency bonus.
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
        # Normalize depends_on: LLM may return a list like [0] instead of 0
        if isinstance(depends_on, list):
            depends_on = depends_on[0] if depends_on else None
        if isinstance(depends_on, str) and depends_on.isdigit():
            depends_on = int(depends_on)

        # Replace {prev_id} from depends_on
        if isinstance(depends_on, int) and depends_on < len(results):
            prev_result = results[depends_on]
            prev_id = prev_result.get("id")
            if prev_id is not None:
                path = path.replace("{prev_id}", str(prev_id))
                if body:
                    body_str = json.dumps(body).replace('"{prev_id}"', str(prev_id))
                    body_str = body_str.replace("{prev_id}", str(prev_id))
                    body = json.loads(body_str)

        # Replace {result_N_id} references to any previous result
        def _replace_result_refs(text):
            for match in re.finditer(r'\{result_(\d+)_id\}', text):
                idx = int(match.group(1))
                if idx < len(results) and results[idx].get("id") is not None:
                    text = text.replace(match.group(0), str(results[idx]["id"]))
            return text

        path = _replace_result_refs(path)
        if body:
            body_str = json.dumps(body)
            # Replace "{result_N_id}" (quoted string) with integer in JSON
            body_str = re.sub(
                r'"\{result_(\d+)_id\}"',
                lambda m: str(results[int(m.group(1))]["id"])
                if int(m.group(1)) < len(results) and results[int(m.group(1))].get("id")
                else m.group(0),
                body_str,
            )
            # Also replace unquoted {result_N_id} inside strings
            body_str = _replace_result_refs(body_str)
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
                # Extract ID from response — handle both single and list responses
                value = data.get("value", data)
                if isinstance(value, dict):
                    result_id = value.get("id")
                elif isinstance(value, list):
                    # List response (from GET) — no single ID
                    result_id = None
                elif "values" in data:
                    # Wrapped list: {"values": [...]}
                    values = data["values"]
                    result_id = values[0].get("id") if values else None
                    value = data
                else:
                    result_id = None
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
    Runs up to 3 rounds: plan → execute → fix → execute → fix → execute.
    """
    log = SubmissionLog()
    log.set_request(prompt, files, base_url)

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
    log.add_llm_call("plan", full_prompt, llm_response)

    # Parse the plan
    plan = parse_llm_plan(llm_response)
    if not plan:
        print("  No valid plan from LLM, trying to recover...")
        retry_prompt = f"""The task is: {prompt}

Return ONLY a JSON array of Tripletex API calls. No explanation. Example format:
[{{"method": "POST", "path": "/employee", "body": {{"firstName": "Ola"}}, "description": "Create employee"}}]"""
        llm_response = call_claude(retry_prompt, system=SYSTEM_PROMPT)
        plan = parse_llm_plan(llm_response)
        log.add_llm_call("retry", retry_prompt, llm_response)

    log.set_plan(plan)
    print(f"  Plan: {len(plan)} API calls")

    # Execute with up to 3 rounds of fix attempts
    all_plans = []
    all_results = []
    current_plan = plan
    max_rounds = 3

    for round_num in range(max_rounds):
        if not current_plan:
            break

        round_label = "initial" if round_num == 0 else f"fix_{round_num}"
        print(f"  --- Round {round_num + 1}/{max_rounds} ---")

        results = execute_api_calls(current_plan, base_url, session_token)
        all_plans.append(current_plan)
        all_results.append(results)

        if round_num == 0:
            log.set_api_results(results)

        success = sum(1 for r in results if r.get("status") in (200, 201))
        failed = [r for r in results if r.get("status") not in (200, 201, None) or r.get("error")]
        print(f"  Results: {success}/{len(results)} successful")

        # All succeeded — done
        if not failed:
            print("  All calls successful!")
            break

        # Last round — no more retries
        if round_num >= max_rounds - 1:
            print("  Max retries reached.")
            break

        # Build fix prompt with full history
        history = ""
        for r, (p, res) in enumerate(zip(all_plans, all_results)):
            history += f"\n--- Round {r + 1} ---\nCalls made:\n{json.dumps(p, indent=2)}\nResults:\n{json.dumps(res, indent=2)}\n"

        fix_prompt = f"""Original task: {prompt}

{history}

Some calls failed. The Tripletex error messages tell you exactly what's wrong.
Common issues:
- Employee requires: firstName, lastName, email, userType ("STANDARD"/"ADMINISTRATOR"), department ({{"id": N}})
- If department is needed, create one first: POST /department with name and departmentNumber
- startDate goes on /employee/employment, NOT on /employee
- References use {{"id": N}} format where N is the integer ID from a previous response

Provide a COMPLETE corrected JSON array of ALL calls needed (including ones that already succeeded if they're prerequisites).
The previous results may have created some entities — use their IDs if available.
Return [] if the task is already complete."""

        print(f"  Asking LLM to fix (round {round_num + 2})...")
        fix_response = call_claude(fix_prompt, system=SYSTEM_PROMPT)
        fix_plan = parse_llm_plan(fix_response)
        log.add_llm_call(f"fix_{round_num + 1}", fix_prompt, fix_response)

        if not fix_plan:
            print("  No fix plan returned.")
            break

        # Store fix info
        if round_num == 0:
            log.set_fix_plan(fix_plan)

        current_plan = fix_plan

    # Store final fix results if we had retries
    if len(all_results) > 1:
        log.set_fix_results(all_results[-1])

    log.save()
    return {"status": "completed"}
