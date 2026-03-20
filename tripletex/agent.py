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

POST /supplier — Create a supplier (use this instead of /customer with isSupplier)
  Required: name, email
  Optional: organizationNumber, phoneNumber
  NOTE: When task says "supplier" / "leverandør" / "Lieferant" / "fournisseur" / "proveedor",
  use POST /supplier — NOT POST /customer with isSupplier=true.
  The /customer endpoint auto-sets isCustomer=true even if you pass isCustomer=false.

POST /product — Create a product
  Required: name
  Optional: number, costExcludingVatCurrency, priceExcludingVatCurrency, vatType ({"id": N})
  NOTE: vatType.id must be an integer. GET /ledger/vatType to find valid IDs. Common: id=3 for 25% MVA (outgoing)
  NOTE: If product number already exists ("Produktnummeret er i bruk"):
  1. Try GET /product?number=NNNNN&fields=id to find existing product
  2. If GET returns 404, retry POST /product WITHOUT the "number" field (omit it, let Tripletex auto-assign)
  The key data is the name and price, not the product number.

POST /project — Create a project
  Required: name, projectManager ({"id": N}), isInternal (true/false), startDate (YYYY-MM-DD)
  Optional: customer ({"id": N}), endDate, number, description
  NOTE: projectManager must reference an employee ID
  NOTE: startDate IS required — use today's date if not specified in the task

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
  Alternative URL format if /:invoice gives 404: PUT /order/invoice/{id}?invoiceDate=...&invoiceDueDate=...
  NOTE: Do NOT put these in the JSON body — they MUST be query params.
  You can also add sendToCustomer=true as query param to send it immediately.

PUT /invoice/{id}/:send — Send an existing invoice
  Pass sendType as query param: PUT /invoice/123/:send?sendType=EMAIL
  Use this AFTER creating an invoice if the task says to "send" it.

POST /invoice — Create an invoice directly
  Required: invoiceDate (YYYY-MM-DD), invoiceDueDate (YYYY-MM-DD), orders (array of {"id": N})

POST /payment — Register payment on an invoice
  Required: date (YYYY-MM-DD), amount, amountCurrency
  Required reference: paymentType ({"id": N}), kid (string, can be empty "")
  The invoice ID may need to be passed via the endpoint or body.

  TRY THESE PATHS IN ORDER:
  1. PUT /invoice/{id}/:createPayment?paymentDate=YYYY-MM-DD&paymentTypeId=N&paidAmount=AMOUNT&paidAmountCurrency=AMOUNT
  2. POST /payment with body: {"date": "YYYY-MM-DD", "amount": AMOUNT, "amountCurrency": AMOUNT, "paymentType": {"id": N}, "invoice": {"id": INVOICE_ID}}
  3. PUT /invoice/{id}/:pay?paymentDate=YYYY-MM-DD&paymentTypeId=N&paidAmount=AMOUNT

  FOR paymentTypeId / paymentType: Try id=1 or id=2 first. If 500, try other IDs.
  DO NOT use id=0.

  IMPORTANT: Amount must be the TOTAL INCLUDING VAT (not the ex-VAT amount from the prompt).
  If task says "9400 NOK excl VAT" and product has 25% MVA, invoice total = 11750 NOK. Pay 11750.
  Use the "amount" field from the invoice creation response.

GET/POST/PUT/DELETE /travelExpense — Travel expense reports
  Required for POST: employee ({"id": N}), title, startDate, endDate

GET /salary/type — List salary types (needed for payroll)
  Returns list of salary types with IDs. Use these IDs in payslip specifications.

POST /salary/payslip — Create/run payroll for an employee
  Body includes employee reference, date, year, month, and payslipSpecifications array.
  Each specification needs: salaryType ({"id": N}), rate, count, amount.
  First GET /salary/type to find valid salary type IDs.

GET /salary/payslip — Query existing payslips

GET /ledger/account — Query chart of accounts
GET /ledger/posting — Query ledger postings
GET/POST/DELETE /ledger/voucher — Manage vouchers
GET /ledger/paymentTypeCategory — List payment type categories (try this for finding paymentTypeId)

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
  {"method": "POST", "path": "/project", "body": {"name": "Project X", "projectManager": {"id": "{prev_id}"}, "customer": {"id": "{result_1_id}"}, "isInternal": false, "startDate": "2026-03-20"}, "description": "Create project", "depends_on": 2}
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
NOTE: No bank account setup needed — invoice works through the competition proxy.

Pattern 5 — Create invoice + register payment:
```json
[
  {"method": "POST", "path": "/department", "body": {"name": "General", "departmentNumber": 1}, "description": "Create department"},
  {"method": "POST", "path": "/employee", "body": {"firstName": "Admin", "lastName": "User", "email": "admin@example.org", "userType": "STANDARD", "department": {"id": "{prev_id}"}}, "description": "Create employee", "depends_on": 0},
  {"method": "POST", "path": "/customer", "body": {"name": "Client AS", "email": "client@example.org", "isCustomer": true, "organizationNumber": "123456789"}, "description": "Create customer"},
  {"method": "POST", "path": "/product", "body": {"name": "Service", "priceExcludingVatCurrency": 10000}, "description": "Create product"},
  {"method": "POST", "path": "/order", "body": {"customer": {"id": "{result_2_id}"}, "deliveryDate": "2026-01-15", "orderDate": "2026-01-15", "orderLines": [{"product": {"id": "{result_3_id}"}, "count": 1}]}, "description": "Create order"},
  {"method": "PUT", "path": "/order/{prev_id}/:invoice?invoiceDate=2026-01-15&invoiceDueDate=2026-02-15", "body": {}, "description": "Convert order to invoice", "depends_on": 4},
  {"method": "PUT", "path": "/invoice/{prev_id}/:createPayment?paymentDate=2026-01-20&paymentTypeId=1&paidAmount=12500&paidAmountCurrency=12500", "body": {}, "description": "Try payment via PUT createPayment (if 404, fix round will try POST /payment)", "depends_on": 5}
]
```
NOTE: No bank account setup needed — invoice creation works through the competition proxy.
NOTE: paidAmount must be the invoice total INCLUDING VAT. Calculate: excl_vat * 1.25 for 25% MVA.
NOTE: Use paymentTypeId=1 as default. If it fails, the fix round will try alternatives.

Pattern 6 — Register a supplier:
```json
[
  {"method": "POST", "path": "/supplier", "body": {"name": "Acme Supplier AS", "email": "faktura@acme.no", "organizationNumber": "123456789"}, "description": "Register supplier"}
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

        # Validate: skip calls with unresolved placeholders
        unresolved = re.findall(r'\{(?:prev_id|result_\d+_id)\}', path)
        if body:
            unresolved += re.findall(r'\{(?:prev_id|result_\d+_id)\}', json.dumps(body))
        if unresolved:
            print(f"  [{i}] {method} {path} — {desc}")
            print(f"    SKIP: unresolved refs {unresolved}")
            results.append({"error": f"Unresolved references: {unresolved}", "id": None})
            continue

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
                # Extract ID from response — handle single value, wrapped list, etc.
                if "value" in data and isinstance(data["value"], dict):
                    # Single entity: {"value": {"id": 123, ...}}
                    value = data["value"]
                    result_id = value.get("id")
                elif "values" in data:
                    # List response: {"fullResultSize": N, "values": [...]}
                    values_list = data["values"]
                    value = data
                    result_id = values_list[0].get("id") if values_list else None
                    if values_list:
                        print(f"    (list: {len(values_list)} results, first id={result_id})")
                else:
                    value = data
                    result_id = data.get("id") if isinstance(data, dict) else None
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


def _try_payment_fallbacks(results: list, plan: list, base_url: str, token: str) -> bool:
    """
    When :createPayment returns 404, brute-force try alternative payment endpoints.
    Returns True if payment succeeded on any path.
    """
    auth = ("0", token)

    # Find the failed payment call and successful invoice call
    invoice_id = None
    invoice_amount = None
    payment_date = None
    payment_failed = False

    for i, (p, r) in enumerate(zip(plan, results)):
        path = p.get("path", "")
        # Find successful invoice creation
        if r.get("status") in (200, 201) and r.get("id"):
            data = r.get("data", {})
            if isinstance(data, dict) and "amount" in data:
                invoice_id = r["id"]
                invoice_amount = data["amount"]
        # Find failed payment call
        if ("createPayment" in path or "payment" in path.lower() or ":pay" in path) \
                and r.get("status") in (404, 500, None) or ("payment" in path.lower() and r.get("error")):
            payment_failed = True
            # Extract payment date from the path query params
            import urllib.parse
            parsed = urllib.parse.urlparse(path)
            params = urllib.parse.parse_qs(parsed.query)
            payment_date = params.get("paymentDate", [None])[0]

    if not payment_failed or not invoice_id or not invoice_amount:
        return False

    if not payment_date:
        payment_date = "2026-01-15"  # fallback date

    print(f"\n  [PAYMENT FALLBACK] invoice_id={invoice_id}, amount={invoice_amount}, date={payment_date}")

    # Try multiple endpoint patterns × multiple paymentTypeIds
    endpoints = [
        ("PUT", f"/invoice/{invoice_id}/:createPayment", True),   # query params
        ("PUT", f"/invoice/{invoice_id}/:pay", True),             # query params
        ("POST", f"/invoice/{invoice_id}/payment", False),        # body
        ("POST", "/payment", False),                               # body with invoice ref
    ]

    for type_id in [1, 2, 3]:
        for method, path, use_query in endpoints:
            if use_query:
                url = f"{base_url}{path}?paymentDate={payment_date}&paymentTypeId={type_id}&paidAmount={invoice_amount}&paidAmountCurrency={invoice_amount}"
                body = {}
            else:
                url = f"{base_url}{path}"
                body = {
                    "date": payment_date,
                    "paymentDate": payment_date,
                    "amount": invoice_amount,
                    "amountCurrency": invoice_amount,
                    "paidAmount": invoice_amount,
                    "paidAmountCurrency": invoice_amount,
                    "paymentType": {"id": type_id},
                    "paymentTypeId": type_id,
                }
                if "POST" == method and path == "/payment":
                    body["invoice"] = {"id": invoice_id}

            try:
                if method == "PUT":
                    resp = requests.put(url, auth=auth, json=body if not use_query else {}, timeout=15)
                else:
                    resp = requests.post(url, auth=auth, json=body, timeout=15)

                print(f"    {method} {path} typeId={type_id} → {resp.status_code}")

                if resp.status_code in (200, 201):
                    print(f"    ✓ PAYMENT SUCCESS! endpoint={method} {path}, typeId={type_id}")
                    return True
                elif resp.status_code == 422:
                    # Validation error means the endpoint EXISTS but params are wrong
                    print(f"    → 422 (endpoint exists!): {resp.text[:150]}")
            except Exception as e:
                print(f"    → Exception: {e}")

    print(f"    ✗ All payment fallbacks failed")
    return False


def parse_llm_plan(response: str) -> list:
    """Extract JSON array of API calls from LLM response. Prefers last valid block."""
    # Try ALL ```json blocks, prefer the last valid one (LLM often self-corrects)
    json_blocks = re.findall(r'```(?:json)?\s*\n?(\[[\s\S]*?\])\s*\n?```', response)
    for block in reversed(json_blocks):
        try:
            return json.loads(block)
        except json.JSONDecodeError:
            continue

    # Fallback: find all JSON arrays, prefer the last valid one
    arrays = re.findall(r'\[[\s\S]*?\]', response)
    for arr in reversed(arrays):
        try:
            parsed = json.loads(arr)
            if isinstance(parsed, list) and parsed:
                return parsed
        except json.JSONDecodeError:
            continue

    print(f"  Failed to parse LLM response as JSON")
    return []


def _shift_plan_refs(plan: list, offset: int) -> list:
    """Shift all depends_on and {result_N_id} references by offset."""
    import copy
    shifted = copy.deepcopy(plan)
    for call in shifted:
        # Shift depends_on
        if call.get("depends_on") is not None:
            dep = call["depends_on"]
            if isinstance(dep, int):
                call["depends_on"] = dep + offset
            elif isinstance(dep, list):
                call["depends_on"] = [d + offset for d in dep]

        # Shift {result_N_id} in path and body
        def shift_refs(text):
            return re.sub(
                r'\{result_(\d+)_id\}',
                lambda m: f'{{result_{int(m.group(1)) + offset}_id}}',
                text,
            )

        call["path"] = shift_refs(call.get("path", ""))
        if call.get("body"):
            body_str = json.dumps(call["body"])
            call["body"] = json.loads(shift_refs(body_str))
    return shifted


def _is_invoice_task(prompt: str) -> bool:
    """Detect if this is an invoice/payment task (not just a mention of 'faktura' in an email)."""
    p = prompt.lower()
    # Supplier/vendor tasks are NOT invoice tasks even if email contains "faktura"
    supplier_keywords = ["supplier", "leverandør", "lieferant", "fournisseur", "proveedor",
                         "fornecedor", "leverandor"]
    if any(kw in p for kw in supplier_keywords):
        return False
    # Check for actual invoice action words (not just the word in an email address)
    invoice_action = [
        "faktura ", "fakturaen", "send faktura", "opprett faktura",
        "invoice ", "create invoice", "send invoice",
        "rechnung ", "erstellen sie eine rechnung", "senden sie eine rechnung",
        "factura ", "facture ",
        "zahlung", "betaling", "payment", "paiement", "pago",
    ]
    return any(kw in p for kw in invoice_action)


def inject_prerequisites(plan: list, prompt: str) -> list:
    """Inject known prerequisite API calls that the LLM often forgets."""
    if not plan:
        return plan

    # NOTE: Bank account injection removed — invoice works without it through the proxy.
    # The GET /company and PUT /company endpoints return 404/405 through the proxy anyway.
    return plan


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

    # Inject prerequisites for known task types
    plan = inject_prerequisites(plan, prompt)
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

        # Try payment fallbacks if a payment call failed with 404
        if any("payment" in p.get("path", "").lower() or "createPayment" in p.get("path", "")
               for p, r in zip(current_plan, results)
               if r.get("status") in (404, 500)):
            payment_ok = _try_payment_fallbacks(results, current_plan, base_url, session_token)
            if payment_ok:
                # Recount — payment succeeded via fallback
                failed = [r for r in results if r.get("status") not in (200, 201, None) or r.get("error")]
                print(f"  After payment fallback: {len(failed)} remaining failures")

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
- Project requires startDate (YYYY-MM-DD) — use today's date if not specified
- References use {{"id": N}} format where N is the integer ID from a previous response
- "Det finnes allerede en bruker med denne e-postadressen" = email already exists. GET /employee?email=X to find their ID.
- "Produktnummeret NNNN er i bruk" = product number exists. Try GET /product?number=NNNN to find it. If GET returns 404, retry POST /product WITHOUT the number field.
- If GET returns a list, the ID is in values[0].id — use that integer directly.
- If PUT /order/ID/:invoice returns 404, try PUT /order/:invoice/ID or POST /invoice with orders: [{{"id": ORDER_ID}}]
- If PUT /invoice/{id}/:createPayment returns 404, try these alternatives:
  1. POST /payment with body: {{"date": "YYYY-MM-DD", "amount": N, "amountCurrency": N, "paymentType": {{"id": 1}}, "invoice": {{"id": INVOICE_ID}}}}
  2. PUT /invoice/{id}/:pay?paymentDate=YYYY-MM-DD&paymentTypeId=1&paidAmount=N
- paymentTypeId=0 is INVALID — use 1 or 2
- paidAmount must be INCLUDING VAT (use the "amount" field from invoice response)

Provide a COMPLETE corrected JSON array of ONLY the calls that still need to succeed.
DO NOT repeat calls that already returned 200/201 — those entities exist and their IDs are in the results above.
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
