"""
Explore the Tripletex sandbox API to discover exact field requirements.

Usage:
    python -m tripletex.explore_api

Requires sandbox credentials. Set env vars or edit below.
"""

import json
import os
import requests

# Sandbox credentials — update these or use env vars
BASE_URL = os.environ.get("TX_BASE_URL", "https://kkpqfuj-amager.tripletex.dev/v2")
SESSION_TOKEN = os.environ.get("TX_SESSION_TOKEN", "")

if not SESSION_TOKEN:
    print("Set TX_SESSION_TOKEN env var to your sandbox session token.")
    print("Find it at https://app.ainm.no → Tripletex → Sandbox Account")
    exit(1)

AUTH = ("0", SESSION_TOKEN)
OUT_DIR = "tripletex/api_schema"
os.makedirs(OUT_DIR, exist_ok=True)


def get(path, params=None):
    r = requests.get(f"{BASE_URL}{path}", auth=AUTH, params=params, timeout=30)
    return r.status_code, r.json() if r.status_code == 200 else r.text


def post(path, body):
    r = requests.post(f"{BASE_URL}{path}", auth=AUTH, json=body, timeout=30)
    return r.status_code, r.json() if r.status_code in (200, 201) else r.text


def explore_endpoint(name, path, test_body=None):
    """Try to POST with minimal body to discover required fields from error messages."""
    print(f"\n=== {name} ({path}) ===")

    # First, GET to see existing data and field names
    status, data = get(path, {"fields": "*", "count": 1})
    print(f"  GET {path}?fields=*&count=1 → {status}")
    if status == 200:
        if isinstance(data, dict) and "values" in data:
            print(f"  Count: {data.get('fullResultSize', '?')}")
            if data["values"]:
                print(f"  Sample fields: {list(data['values'][0].keys())}")
                with open(f"{OUT_DIR}/{name}_sample.json", "w") as f:
                    json.dump(data["values"][0], f, indent=2, ensure_ascii=False)
        elif isinstance(data, dict) and "value" in data:
            print(f"  Sample fields: {list(data['value'].keys())}")

    # Try POST with empty body to get required fields
    if test_body is not None:
        status2, data2 = post(path, test_body)
        print(f"  POST {path} with {json.dumps(test_body)[:100]} → {status2}")
        if status2 in (200, 201):
            print(f"  Created! ID: {data2.get('value', {}).get('id')}")
        else:
            print(f"  Error: {str(data2)[:500]}")
            with open(f"{OUT_DIR}/{name}_error.json", "w") as f:
                json.dump({"status": status2, "body_sent": test_body, "error": data2}, f, indent=2, ensure_ascii=False)

    return data


# Explore all endpoints
print("Exploring Tripletex sandbox API...")
print(f"Base URL: {BASE_URL}")
print()

# Department (usually needed first)
explore_endpoint("department", "/department", {})
explore_endpoint("department_minimal", "/department", {"name": "Test", "departmentNumber": 999})

# Employee
explore_endpoint("employee", "/employee", {})
explore_endpoint("employee_minimal", "/employee", {"firstName": "Test", "lastName": "User", "email": "test@test.org"})
explore_endpoint("employee_with_type", "/employee", {"firstName": "Test2", "lastName": "User2", "email": "test2@test.org", "userType": "STANDARD"})

# Employee employment
explore_endpoint("employment", "/employee/employment", {})

# Customer
explore_endpoint("customer", "/customer", {})
explore_endpoint("customer_minimal", "/customer", {"name": "Test Customer", "email": "cust@test.org", "isCustomer": True})

# Product
explore_endpoint("product", "/product", {})
explore_endpoint("product_minimal", "/product", {"name": "Test Product"})

# VAT types
explore_endpoint("vatType", "/ledger/vatType")

# Order
explore_endpoint("order", "/order", {})

# Invoice
explore_endpoint("invoice", "/invoice", {})

# Project
explore_endpoint("project", "/project", {})

# Travel expense
explore_endpoint("travelExpense", "/travelExpense", {})

# Department list
explore_endpoint("department_list", "/department")

# Activity
explore_endpoint("activity", "/activity")

# Contact
explore_endpoint("contact", "/contact")

print(f"\n\nSchema files saved to {OUT_DIR}/")
print("Check *_sample.json for field names and *_error.json for required field errors.")
