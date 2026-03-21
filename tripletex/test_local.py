"""
Local end-to-end test for the Tripletex agent.

Spins up a mock Tripletex API on port 9999, then sends a test task
to the agent on port 8000. Verifies the agent creates the right entities.

Usage:
    1. Start the agent:   python -m tripletex
    2. Run this test:     python -m tripletex.test_local
"""

import json
import threading
import time
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler

# ── Mock Tripletex API ────────────────────────────────────────────

MOCK_DB: dict[str, list] = {}
MOCK_ID = 0
MOCK_PORT = 9999


class MockTripletexHandler(BaseHTTPRequestHandler):
    """Mimics Tripletex v2 API responses, including action URLs."""

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length)) if length else {}

    def _respond(self, code, data):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def _parse_path(self):
        """Parse /v2/entity/id/:action?params into components."""
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(self.path)
        parts = parsed.path.lstrip("/").replace("v2/", "").split("/")
        query = parse_qs(parsed.query)

        entity = parts[0] if parts else ""
        eid = None
        action = None

        if len(parts) >= 2:
            if parts[1].isdigit():
                eid = int(parts[1])
                if len(parts) >= 3 and parts[2].startswith(":"):
                    action = parts[2]  # e.g. ":invoice", ":createPayment"
            elif parts[1].startswith(":"):
                action = parts[1]

        return entity, eid, action, {k: v[0] for k, v in query.items()}

    def do_GET(self):
        entity, eid, action, params = self._parse_path()

        # Handle sub-entities like ledger/vatType
        full_path = self.path.split("?")[0].lstrip("/").replace("v2/", "")
        if "/" in full_path and not any(p.isdigit() for p in full_path.split("/")):
            # e.g. ledger/vatType, ledger/account, salary/type
            sub_entity = full_path.replace("/", "_")
            items = MOCK_DB.get(sub_entity, [
                {"id": 3, "number": 3, "name": "25% MVA outgoing", "percentage": 25},
            ])
            self._respond(200, {"fullResultSize": len(items), "values": items})
            return

        items = MOCK_DB.get(entity, [])
        if eid is not None:
            item = next((e for e in items if e["id"] == eid), None)
            if item:
                self._respond(200, {"value": item})
            else:
                self._respond(404, {"error": "not found"})
        else:
            # Filter by query params if provided
            filtered = items
            for key, val in params.items():
                if key in ("fields", "count", "from"):
                    continue
                filtered = [e for e in filtered if str(e.get(key, "")) == val]
            self._respond(200, {"fullResultSize": len(filtered), "values": filtered})

    def do_POST(self):
        global MOCK_ID
        entity, _, action, params = self._parse_path()
        body = self._read_body()
        MOCK_ID += 1
        body["id"] = MOCK_ID

        # Handle sub-entities
        full_path = self.path.split("?")[0].lstrip("/").replace("v2/", "")
        if "/" in full_path:
            sub_entity = full_path.split("?")[0].replace("/", "_")
            MOCK_DB.setdefault(sub_entity, []).append(body)
            self._respond(201, {"value": body})
            return

        MOCK_DB.setdefault(entity, []).append(body)
        self._respond(201, {"value": body})

    def do_PUT(self):
        global MOCK_ID
        entity, eid, action, params = self._parse_path()

        # Handle action URLs: /order/5/:invoice, /invoice/5/:createPayment
        if action and eid:
            if action == ":invoice":
                # Convert order to invoice
                MOCK_ID += 1
                invoice_data = {
                    "id": MOCK_ID,
                    "invoiceDate": params.get("invoiceDate", "2026-01-01"),
                    "invoiceDueDate": params.get("invoiceDueDate", "2026-02-01"),
                    "amount": 12500.0,
                    "amountCurrency": 12500.0,
                    "amountExcludingVat": 10000.0,
                    "orders": [{"id": eid}],
                }
                MOCK_DB.setdefault("invoice", []).append(invoice_data)
                self._respond(200, {"value": invoice_data})
                return
            elif action in (":createPayment", ":pay", ":payment"):
                # Register payment
                MOCK_ID += 1
                payment_data = {
                    "id": MOCK_ID,
                    "paymentDate": params.get("paymentDate", "2026-01-20"),
                    "paidAmount": float(params.get("paidAmount", 0)),
                }
                MOCK_DB.setdefault("payment", []).append(payment_data)
                self._respond(200, {"value": payment_data})
                return
            elif action == ":send":
                self._respond(200, {"value": {"id": eid, "sent": True}})
                return

        # Regular PUT
        body = self._read_body()
        eid_from_body = body.get("id", eid)
        items = MOCK_DB.get(entity, [])
        for i, item in enumerate(items):
            if item["id"] == eid_from_body:
                items[i] = {**item, **body}
                self._respond(200, {"value": items[i]})
                return
        # If not found but has an id, just accept it (company updates etc)
        if eid_from_body:
            body["id"] = eid_from_body
            MOCK_DB.setdefault(entity, []).append(body)
            self._respond(200, {"value": body})
            return
        self._respond(404, {"error": "not found"})

    def do_DELETE(self):
        entity, eid, _, _ = self._parse_path()
        if eid and entity in MOCK_DB:
            MOCK_DB[entity] = [e for e in MOCK_DB[entity] if e["id"] != eid]
            self._respond(200, {"status": "deleted"})
        else:
            self._respond(400, {"error": "missing id"})

    def log_message(self, format, *args):
        print(f"  [MOCK] {args[0]}")


# ── Test Cases ────────────────────────────────────────────────────

TESTS = [
    {
        "name": "Create employee (simple)",
        "prompt": "Opprett en ansatt med navn Ola Nordmann, e-post ola@nordmann.no.",
        "files": [],
        "check": lambda: any(
            e.get("firstName") == "Ola" and e.get("lastName") == "Nordmann"
            for e in MOCK_DB.get("employee", [])
        ),
    },
    {
        "name": "Create employee with admin role",
        "prompt": "Opprett en ansatt med navn Kari Hansen, e-post kari@hansen.no. Hun skal være kontoadministrator.",
        "files": [],
        "check": lambda: any(
            e.get("firstName") == "Kari" and e.get("lastName") == "Hansen"
            for e in MOCK_DB.get("employee", [])
        ),
    },
    {
        "name": "Create customer",
        "prompt": "Create a customer named Acme Corp with email acme@corp.com.",
        "files": [],
        "check": lambda: any(
            "Acme" in e.get("name", "")
            for e in MOCK_DB.get("customer", [])
        ),
    },
    {
        "name": "Create employee with start date",
        "prompt": "We have a new employee named Per Hansen, born 15. March 1990. Please create them as an employee with email per.hansen@example.org and start date 1. June 2026.",
        "files": [],
        "check": lambda: (
            any(e.get("firstName") == "Per" for e in MOCK_DB.get("employee", []))
            and len(MOCK_DB.get("employee_employment", [])) > 0
        ),
    },
    {
        "name": "Create project",
        "prompt": "Create the project \"Alpha Launch\" linked to the customer Beta Corp (org no. 123456789). The project manager is Ola Test (ola.test@example.org).",
        "files": [],
        "check": lambda: any(
            "Alpha" in p.get("name", "")
            for p in MOCK_DB.get("project", [])
        ),
    },
    {
        "name": "Create and send invoice",
        "prompt": "Opprett og send ein faktura til kunden TestKunde AS (org.nr 999888777) på 10000 kr eksklusiv MVA. Fakturaen gjeld Konsultering.",
        "files": [],
        "check": lambda: len(MOCK_DB.get("invoice", [])) > 0,
    },
    {
        "name": "Invoice with full payment",
        "prompt": "The customer PayTest Ltd (org no. 111222333) has an outstanding invoice for 5000 NOK excluding VAT for \"Services\". Register full payment on this invoice.",
        "files": [],
        "check": lambda: (
            len(MOCK_DB.get("invoice", [])) > 0
            and len(MOCK_DB.get("payment", [])) > 0
        ),
    },
    {
        "name": "Register supplier",
        "prompt": "Register the supplier Nordic Parts AS with organization number 555666777. Email: faktura@nordicparts.no.",
        "files": [],
        "check": lambda: (
            any("Nordic" in e.get("name", "") for e in MOCK_DB.get("supplier", []))
            or any("Nordic" in e.get("name", "") for e in MOCK_DB.get("customer", []))
        ),
    },
    {
        "name": "Create three departments",
        "prompt": "Opprett tre avdelingar i Tripletex: \"Salg\", \"IT\" og \"HR\".",
        "files": [],
        "check": lambda: len(MOCK_DB.get("department", [])) >= 3,
    },
]


# ── Runner ────────────────────────────────────────────────────────

def run_tests():
    agent_url = "http://localhost:8000/solve"
    mock_base = f"http://localhost:{MOCK_PORT}/v2"

    # Check agent is running
    try:
        r = requests.get("http://localhost:8000/health", timeout=5)
        assert r.status_code == 200
    except Exception:
        print("ERROR: Agent not running. Start it first: python -m tripletex")
        return

    print(f"\nRunning {len(TESTS)} tests against mock API on port {MOCK_PORT}\n")
    print("=" * 60)

    passed = 0
    for i, test in enumerate(TESTS):
        # Reset mock DB for each test
        MOCK_DB.clear()
        global MOCK_ID
        MOCK_ID = 0

        print(f"\n[{i+1}/{len(TESTS)}] {test['name']}")
        print(f"  Prompt: {test['prompt'][:80]}...")

        try:
            resp = requests.post(agent_url, json={
                "prompt": test["prompt"],
                "files": test["files"],
                "tripletex_credentials": {
                    "base_url": mock_base,
                    "session_token": "mock-token",
                },
            }, timeout=120)

            print(f"  Agent response: {resp.status_code} {resp.json()}")
            print(f"  Mock DB state: {json.dumps({k: len(v) for k, v in MOCK_DB.items()})}")

            if test["check"]():
                print(f"  ✓ PASSED")
                passed += 1
            else:
                print(f"  ✗ FAILED — expected entity not found in mock DB")
                print(f"  DB contents: {json.dumps(MOCK_DB, indent=2)}")

        except Exception as e:
            print(f"  ✗ ERROR: {e}")

    print("\n" + "=" * 60)
    print(f"Results: {passed}/{len(TESTS)} passed")
    print(f"Logs saved in: tripletex/logs/")


if __name__ == "__main__":
    # Start mock server in background
    server = HTTPServer(("0.0.0.0", MOCK_PORT), MockTripletexHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"Mock Tripletex API running on port {MOCK_PORT}")

    run_tests()
    server.shutdown()
