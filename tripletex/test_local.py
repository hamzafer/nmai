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
    """Mimics Tripletex v2 API responses."""

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length)) if length else {}

    def _respond(self, code, data):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def _entity_and_id(self):
        # /v2/employee/123 → ("employee", 123)
        parts = self.path.lstrip("/").replace("v2/", "").split("?")[0].split("/")
        entity = parts[0] if parts else ""
        eid = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
        return entity, eid

    def do_GET(self):
        entity, eid = self._entity_and_id()
        items = MOCK_DB.get(entity, [])
        if eid is not None:
            item = next((e for e in items if e["id"] == eid), None)
            if item:
                self._respond(200, {"value": item})
            else:
                self._respond(404, {"error": "not found"})
        else:
            self._respond(200, {"fullResultSize": len(items), "values": items})

    def do_POST(self):
        global MOCK_ID
        entity, _ = self._entity_and_id()
        body = self._read_body()
        MOCK_ID += 1
        body["id"] = MOCK_ID
        MOCK_DB.setdefault(entity, []).append(body)
        self._respond(201, {"value": body})

    def do_PUT(self):
        entity, _ = self._entity_and_id()
        body = self._read_body()
        eid = body.get("id")
        items = MOCK_DB.get(entity, [])
        for i, item in enumerate(items):
            if item["id"] == eid:
                items[i] = {**item, **body}
                self._respond(200, {"value": items[i]})
                return
        self._respond(404, {"error": "not found"})

    def do_DELETE(self):
        entity, eid = self._entity_and_id()
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
