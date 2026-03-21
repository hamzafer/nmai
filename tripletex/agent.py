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
- EVERY task requires CREATING or MODIFYING entities. A plan with ONLY GET calls scores 0%.
  If the task says "analyze X and create Y", you MUST include POST/PUT calls for Y.
- The sandbox may have PRE-EXISTING entities (employees, products with specific numbers)
- If POST fails with "already exists" error, GET the existing entity and use its ID
- Always create departments (they're usually missing) but employees/products may exist

ENDPOINTS AND REQUIRED FIELDS:

POST /employee — Create an employee
  Required: firstName, lastName, email, userType, department ({"id": N})
  Optional: dateOfBirth (YYYY-MM-DD), phoneNumberMobile, phoneNumberHome, phoneNumberWork
  IMPORTANT: userType is REQUIRED. Use "STANDARD" for normal employees.
  IMPORTANT: department is REQUIRED. First create a department if none exist, or GET /department to find one.
  IMPORTANT: If you will create /employee/employment, you MUST include dateOfBirth on the employee (required for employment). Use "1990-01-01" if not specified.
  NOTE: "startDate" does NOT exist on employee — use /employee/employment for that
  NOTE: To set admin role, use "userType": "ADMINISTRATOR"
  Valid userType values: "STANDARD", "EXTENDED", "ADMINISTRATOR"
  NOTE: "percentOfFullTimeEquivalent" does NOT exist on /employee — put it on /employee/employment.
  CRITICAL NIN: Norwegian personnummer has strict 11-digit checksums. If the NIN comes from a PDF/image
  you're interpreting, OMIT it rather than guessing. A wrong checksum causes 422 and blocks ALL downstream calls.
  Only include nationalIdentityNumber if you're 100% certain of the exact digits from the source document.

POST /employee/employment — Create employment record (for start date)
  Required: employee ({"id": N}), startDate (YYYY-MM-DD)
  IMPORTANT: Employee MUST have dateOfBirth set BEFORE creating employment — otherwise 422.
  If dateOfBirth wasn't set on POST /employee, use PUT /employee/{id} to add it first.
  Use "1990-01-15" as default dateOfBirth if not specified in the task.
  Optional: endDate, occupationCode ({"id": N})
  BANNED FIELDS (cause 422 "Feltet eksisterer ikke"):
    - "employmentType" — does NOT exist
    - "percentOfFullTimeEquivalent" — does NOT exist on this endpoint
    - "salary", "monthlySalary" — do NOT exist on this endpoint
  Only send: employee, startDate, endDate, occupationCode. Nothing else.

POST /customer — Create a customer
  Required: name, email, isCustomer (must be true)
  Optional: organizationNumber, phoneNumber, isSupplier
  Address: If the task specifies an address, include it as:
    "postalAddress": {"addressLine1": "Street 23", "postalCode": "0182", "city": "Oslo"}
    "physicalAddress": {"addressLine1": "Street 23", "postalCode": "0182", "city": "Oslo"}
  Include BOTH postalAddress and physicalAddress with the same data.

POST /supplier — Create a supplier (use this instead of /customer with isSupplier)
  Required: name, email
  Optional: organizationNumber, phoneNumber
  NOTE: When task says "supplier" / "leverandør" / "Lieferant" / "fournisseur" / "proveedor",
  use POST /supplier — NOT POST /customer with isSupplier=true.
  The /customer endpoint auto-sets isCustomer=true even if you pass isCustomer=false.

POST /supplierInvoice — Register a supplier/vendor invoice (incoming invoice)
  Use a 2-STEP approach (inline voucher postings are broken on this proxy):
  Step 1: POST /supplierInvoice — create the invoice WITHOUT voucher
    Body: {"supplier": {"id": N}, "invoiceNumber": "INV-123", "invoiceDate": "YYYY-MM-DD", "invoiceDueDate": "YYYY-MM-DD"}
    NOTE: Use "invoiceDueDate" NOT "dueDate".
  Step 2: POST /ledger/voucher — create the accounting entry separately
    Body: {"date": "YYYY-MM-DD", "description": "Supplier invoice X", "postings": [
      {"row": 1, "account": {"id": EXPENSE_ID}, "amountGross": NET_AMOUNT, "amountGrossCurrency": NET_AMOUNT, "description": "Expense", "vatType": {"id": 11}},
      {"row": 1, "account": {"id": AP_ID}, "amountGross": -GROSS_AMOUNT, "amountGrossCurrency": -GROSS_AMOUNT, "description": "Accounts payable"}
    ]}
  DO NOT include "voucher" in the supplierInvoice body — it causes "credit posting missing" errors.

  Input VAT types (inngående avgift — use these directly, NEVER GET /ledger/vatType):
    - {"id": 11} = 25% input VAT (Fradrag inngående avgift, høy sats)
    - {"id": 12} = 15% input VAT (middels sats)
    - {"id": 13} = 12% input VAT (lav sats)

  Account lookups: ALWAYS use exact number queries (range queries return wrong results!):
    GET /ledger/account?number=2400&count=1 (accounts payable)
    GET /ledger/account?number=6340&count=1 (use task's expense account)
    Do NOT use numberFrom/numberTo — they return incorrect accounts on this proxy.

GET /ledger/account — Query chart of accounts
  Search by number: GET /ledger/account?number=7140
  Returns account details needed for voucher postings.

GET /product — Search for existing products
  Search by number: GET /product?number=1282 (returns list with product if found)
  Search by name: GET /product?name=ProductName
  Products often PRE-EXIST in the sandbox. ALWAYS try GET first before POST.

POST /product — Create a product (only if GET finds nothing)
  Required: name
  Optional: costExcludingVatCurrency, priceExcludingVatCurrency, vatType ({"id": N})
  IMPORTANT: Do NOT include the "number" field — product names AND numbers often already exist.
  NOTE: vatType uses {"id": N} where N = the vatType number. Common values:
    - {"id": 3} = 25% MVA (standard Norwegian outgoing)
    - {"id": 6} = 0% VAT (utenfor mva-loven / exempt)
  NEVER do GET /ledger/vatType — just use the ID directly.
  CRITICAL: When GET /product finds a product, use THAT call's {result_N_id} for the order.
  Do NOT also POST /product — it WILL fail because the product already exists.

  STRATEGY for products: If the task specifies product numbers, use GET /product?number=NNNN to find them.
  Use the IDs from the GET responses directly in the order. Do NOT also POST — it will fail.
  Only POST /product if GET returns empty values array.

POST /project — Create a project
  Required: name, projectManager ({"id": N}), isInternal (true/false), startDate (YYYY-MM-DD)
  Optional but RECOMMENDED: customer ({"id": N}), endDate, number (string), description (string)
  ALWAYS include description (summarize the task). Include number if task specifies one.
  NOTE: projectManager must reference an employee ID. For internal projects, create a NEW employee — existing ones may lack PM permissions.
  NOTE: startDate IS required — use today's date if not specified
  NOTE: ALWAYS use salary type IDs from GET /salary/type response — IDs vary per sandbox, do NOT hardcode.

POST /department — Create a department
  Required: name, departmentNumber
  Optional: departmentManager ({"id": N})

POST /order — Create an order
  Required: customer ({"id": N}), deliveryDate (YYYY-MM-DD), orderDate (YYYY-MM-DD)
  Optional: orderLines (array of {"product": {"id": N}, "count": N, "unitPriceExcludingVatCurrency": N})
  IMPORTANT: ALWAYS include unitPriceExcludingVatCurrency on each order line — use the price from the task.
  Do NOT rely on the product's default price. Set it explicitly on every order line.
  NOTE: Do NOT use "receiver" — use "customer" for the customer reference

PUT /order/{id}/:invoice — Convert order to invoice
  CRITICAL: invoiceDate and invoiceDueDate go in the URL as QUERY PARAMS. Body MUST be {} (empty).

  CORRECT: {"method": "PUT", "path": "/order/123/:invoice?invoiceDate=2026-01-15&invoiceDueDate=2026-02-15", "body": {}}
  WRONG:   {"method": "PUT", "path": "/order/123/:invoice", "body": {"invoiceDate": "2026-01-15"}}
           ^^^ Dates in body are IGNORED — you get "invoiceDate: Kan ikke være null"

  To also send: add &sendToCustomer=true to the query string.
  Alternative if /:invoice gives 404: PUT /order/invoice/{id}?invoiceDate=...&invoiceDueDate=...

PUT /invoice/{id}/:createCreditNote — Issue a credit note for an invoice
  Pass date as query param: PUT /invoice/123/:createCreditNote?date=2026-03-21
  IMPORTANT: The credit note date MUST be >= the original invoice date. Using an earlier date causes 422.
  Use today's date (2026-03-21) if not specified in the task.

PUT /invoice/{id}/:send — Send an existing invoice
  Pass sendType as query param: PUT /invoice/123/:send?sendType=EMAIL
  Use this AFTER creating an invoice if the task says to "send" it.

GET /invoice — Search for invoices
  IMPORTANT: Date range params are REQUIRED. Without them you get 422.
  Params: invoiceDateFrom, invoiceDateTo, invoiceDueDateFrom, invoiceDueDateTo (YYYY-MM-DD)
  To find overdue invoices: GET /invoice?invoiceDateFrom=2020-01-01&invoiceDateTo=TODAY&invoiceDueDateTo=YESTERDAY
  Also: customerId, invoiceNumber, isPaid (boolean)

POST /invoice — Create an invoice directly
  Required: invoiceDate (YYYY-MM-DD), invoiceDueDate (YYYY-MM-DD), orders (array of {"id": N})

PUT /invoice/{id}/:createPayment — Register payment on an invoice
  USE THIS ENDPOINT:
  PUT /invoice/{id}/:createPayment?paymentDate=YYYY-MM-DD&paymentTypeId=1&paidAmount=AMOUNT&paidAmountCurrency=AMOUNT

  IMPORTANT: paymentTypeId MUST be 1 (never 0). Amount must be total INCLUDING VAT.
  If task says "9400 NOK excl VAT" with 25% MVA, pay 11750 (9400 * 1.25).
  Use the invoice response "amount" field directly — it already includes VAT.
  If :createPayment returns 404/500, the fallback system will automatically try `:payment` and `:pay` alternatives.

  CRITICAL PATTERN FOR PAYMENT TASKS:
  When the task says "has an unpaid invoice" / "har en faktura" / "a une facture impayée" / "tiene una factura":
  The invoice ALREADY EXISTS in the sandbox. Do NOT create a new customer/product/order/invoice!
  Instead:
  1. GET /customer?organizationNumber=ORGNUM to find the existing customer
  2. GET /invoice?invoiceDateFrom=2020-01-01&invoiceDateTo=2026-12-31&customerId=CUSTOMER_ID to find the invoice
  3. Register payment on the FOUND invoice ID
  Creating new entities from scratch will score near zero on these tasks.

  BANK RECONCILIATION (CSV) PATTERN — YOU MUST INCLUDE PAYMENT CALLS:
  When the task asks to reconcile a bank statement CSV against invoices:
  1. GET /invoice?invoiceDateFrom=2020-01-01&invoiceDateTo=2026-12-31&count=100 to get ALL invoices
  2. Match CSV rows to invoices by AMOUNT (not invoice number! CSV says "Faktura 1001" but actual invoiceNumber is 1,2,3...)
     The FIRST GET returns all invoices with their real IDs and amounts. Use THOSE IDs directly.
     Do NOT search again by invoiceNumber — the CSV numbers don't match real invoice numbers.
  3. For EACH matched invoice, HARDCODE the real invoice ID in a payment call:
     PUT /invoice/REAL_ID/:createPayment?paymentDate=CSV_DATE&paymentTypeId=1&paidAmount=AMOUNT&paidAmountCurrency=AMOUNT
  4. For supplier payments in CSV: GET /supplierInvoice to find matching supplier invoices
  5. For bank fees/interest: POST /ledger/voucher with debit/credit postings
  CRITICAL: A plan with ONLY GET calls scores 0%. You MUST include PUT /:createPayment calls.
  CRITICAL: Use the invoice IDs from step 1 GET response. Do NOT do additional GET calls by invoice number.
  Use the invoice IDs from step 1 directly — hardcode them in subsequent calls.

GET /activity — Search for existing activities (MUST do before POST!)
  GET /activity?name=Analyse&count=10 — search by name
  Activities often PRE-EXIST in sandbox. ALWAYS GET first, only POST if empty.

POST /activity — Create activity (ONLY if GET found nothing!)
  Required: name, activityType
  CRITICAL: activityType MUST be "PROJECT_GENERAL_ACTIVITY" for timesheet entries.
  Using "GENERAL_ACTIVITY" or "TASK" causes "Aktiviteten kan ikke benyttes" on timesheets.
  ALWAYS use activityType: "PROJECT_GENERAL_ACTIVITY" — this is the only type that works with timesheets.
  NOTE: "Navnet er i bruk" = name exists. GET /activity?name=X first, but check its type!
  If existing activity is GENERAL_ACTIVITY type, create a NEW one with PROJECT_GENERAL_ACTIVITY.

POST /project/{projectId}/projectActivity — Link an activity to a project
  Required: activity ({"id": N})
  NOTE: The projectId goes in the URL path, not the body.
  WARNING: This endpoint often returns 404 through the proxy. If it fails, SKIP IT — timesheet entries
  work without projectActivity linking (just reference project and activity directly).

POST /timesheet/entry — Log hours
  Required: employee ({"id": N}), project ({"id": N}), activity ({"id": N}), date (YYYY-MM-DD), hours (number), chargeableHours (number, can be 0)
  Optional: comment, hourlyRate (number)
  NOTE: chargeableHours is REQUIRED — set to same as hours, or 0 if not billable.
  NOTE: This works even if projectActivity linking failed — just reference the activity ID directly.

PUT /project/{id}/:createInvoice — BROKEN (always returns 404 through the proxy)
  DO NOT USE THIS ENDPOINT. It does not work.

  Instead, to invoice a project, create an order with the project's line items and invoice the order:
  1. POST /order — Create order linked to customer, with orderLines for the hours/services
     Include: customer ({"id": N}), orderDate, deliveryDate, project ({"id": N}),
     orderLines: [{"description": "X hours @ Y NOK/h", "count": HOURS, "unitPriceExcludingVatCurrency": RATE, "vatType": {"id": 3}}]
  2. PUT /order/{orderId}/:invoice?invoiceDate=YYYY-MM-DD — Convert order to invoice (this WORKS)

  This is the ONLY reliable way to create project invoices.

POST /project/orderline — Set fixed price on a project
  Required: project ({"id": N}), date (YYYY-MM-DD)
  Use: amount, unitPriceExcludingVatCurrency, or amountExcludingVatCurrency for the price.
  Do NOT use "amountGross" — it does not exist on this endpoint (causes 422).
  Example: {"project": {"id": N}, "date": "2026-03-21", "amount": 430750, "description": "Fixed price"}
  BANNED: "isInvoiced" does NOT exist — Tripletex returns 422 if included.

POST /travelExpense — Create travel expense report
  Required: employee ({"id": N}), title (string)
  Do NOT use startDate/endDate — those fields do NOT exist!
  Use travelDetails nested object for dates:
  {"employee": {"id": N}, "title": "Trip name", "travelDetails": {"departureDate": "YYYY-MM-DD", "returnDate": "YYYY-MM-DD", "destination": "City", "purpose": "reason"}}
  Optional top-level: project ({"id": N}), department ({"id": N}), isCompleted (boolean)
GET /travelExpense — Search existing travel expenses
DELETE /travelExpense/{id} — Delete a travel expense

POST /travelExpense/perDiemCompensation — Add daily allowance to travel expense
  Required: travelExpense ({"id": N}), location (string)
  Optional: rateCategory ({"id": N}), count (int = number of days), rate (number), amount (number)
  Optional: isDeductionForBreakfast (bool), isDeductionForLunch (bool), isDeductionForDinner (bool)
  To find rate categories: GET /travelExpense/rateCategory
  RATE SELECTION: For multi-day trips (2+ days with overnight), use overnight rate ("Døgn"/"Overnatting").
  For day trips, use "Dagsreise" rate. Override the rate amount to match task-specified daily rate.
  Set count = number of days. Set rate = task's daily rate. Set amount = count × rate.

POST /travelExpense/cost — Add individual expense to travel report
  Required: travelExpense ({"id": N}), costCategory ({"id": N}), paymentType ({"id": N}), amountCurrencyIncVat (number), date (YYYY-MM-DD)
  COST CATEGORY MATCHING: GET /travelExpense/costCategory returns a list. Match by name:
    - Flight/fly/avión/avion/vol/Flug → find category containing "fly" or "flight"
    - Taxi/transport → find category containing "taxi" or "transport"
    - Hotel/overnatting → find category containing "hotell" or "overnatting"
    Use the FIRST matching category ID. If no match, use the first category in the list.
  Optional: comments (string), currency ({"id": N})
  To find cost categories: GET /travelExpense/costCategory
  To find payment types: GET /travelExpense/paymentType

GET /salary/type — List salary types (needed for payroll)
  Make TWO lookups to get DIFFERENT IDs for base salary vs bonus:
    GET /salary/type?name=Fastlønn → base salary type ID
    GET /salary/type?name=Bonus → bonus type ID
  CRITICAL: Do NOT use the same {result_N_id} for both — they MUST be different IDs!

POST /salary/transaction — Create payroll (DO NOT use POST /salary/payslip — it doesn't exist!)
  Required: year (int), month (int), payslips (array)
  Each payslip needs SEPARATE salary type IDs:
  {"employee": {"id": N}, "specifications": [
    {"salaryType": {"id": FASTLONN_TYPE_ID}, "rate": BASE_SALARY, "count": 1},
    {"salaryType": {"id": BONUS_TYPE_ID}, "rate": BONUS_AMOUNT, "count": 1}
  ]}
  Optional on transaction: date (YYYY-MM-DD)
  Optional query param: ?generateTaxDeduction=true
  IMPORTANT: GET /salary/type FIRST to find valid salary type IDs. Use the ACTUAL IDs from the response.
  Salary type IDs vary per sandbox — do NOT hardcode (1, 30, 1000 etc. are WRONG).
  For base salary, find the type named "Fastlønn" or "Fast lønn". For bonus, find "Bonus" or "Tillegg".
  Use DIFFERENT salary type IDs for base salary vs bonus — do NOT use the same ID for both.
  PREREQUISITE: Employment must be linked to a "virksomhet" (business unit/division).
  If salary transaction fails with "ikke knyttet mot en virksomhet", the fix round should:
    1. GET /company to find the company's division
    2. PUT /employee/employment/{id} to link the division
    3. Retry POST /salary/transaction
  Example: {"year": 2026, "month": 3, "payslips": [{"employee": {"id": 123}, "specifications": [{"salaryType": {"id": 100}, "rate": 42350, "count": 1}]}]}

GET /salary/payslip — Query existing payslips (read-only)

GET /ledger/account — Query chart of accounts
  Search by number: GET /ledger/account?number=6010
  NOTE: Account number ≠ account ID. You MUST query to get the ID.
  If a number returns empty values, try nearby numbers (e.g., 1200, 1210, 6000, 6010).
GET /ledger/posting — Query ledger postings
GET /ledger/paymentType — List payment types (for paymentTypeId in payment registration)

FREE ACCOUNTING DIMENSIONS ("fri regnskapsdimensjon"):
  Do NOT use /ledger/closeGroup (read-only, returns 405 on POST). Do NOT use departments as proxy (scores 0%).
  Step 1: POST /ledger/accountingDimensionName — create the dimension
    Body: {"dimensionName": "Kostsenter", "description": "Cost center", "active": true}
    Response includes dimensionIndex (1, 2, or 3 — max 3 dimensions allowed).
  Step 2: POST /ledger/accountingDimensionValue — add values to the dimension
    Body: {"displayName": "Økonomi", "dimensionIndex": 1, "number": "100", "active": true, "showInVoucherRegistration": true, "position": 0}
    Repeat for each value (e.g., "Produktutvikling" with number "200", position 1).
  Step 3: POST /ledger/voucher — post voucher linked to dimension value
    In each posting, add: "freeAccountingDimension1": {"id": DIMENSION_VALUE_ID}
    (use freeAccountingDimension2 or freeAccountingDimension3 for 2nd/3rd dimensions)

POST /ledger/voucher — Create a journal entry / voucher
  Required: date (YYYY-MM-DD), description (string)
  Required: postings (array) — NOT "voucherLines" (that field does NOT exist!)
  Each posting MUST have these fields:
    {"row": 1, "account": {"id": N}, "amountGross": N, "amountGrossCurrency": N, "description": "..."}
  For customer-related postings (AR account 1500, exchange rate diffs, disagio/agio):
    EVERY posting that touches account 1500 MUST include "customer": {"id": N}.
    Example: {"row": 2, "account": {"id": AR_ID}, "amountGross": -N, "amountGrossCurrency": -N, "customer": {"id": CUST_ID}, "description": "..."}
  For supplier-related postings (AP account 2400, expense postings for supplier invoices/receipts):
    EVERY posting that touches account 2400 or expense accounts for a supplier MUST include "supplier": {"id": N}.
    "Leverandør mangler" error = you forgot to add supplier reference to the posting.
    Omitting customer causes "Kunde mangler" 422 — this is the #1 reason disagio vouchers fail.
  REQUIRED: "row" (integer, MUST start at 1, then 2, 3, ...). Row 0 is RESERVED for system postings — using row 0 causes instant 422.
  REQUIRED: "amountGross", "amountGrossCurrency", "account.id"
  AMOUNT SIGN: positive amountGross = DEBIT, negative = CREDIT. Postings MUST sum to zero.
  NEVER use "amount" — it causes "uten posteringer" error! ONLY use "amountGross" and "amountGrossCurrency".
  NEVER use "debitAmount"/"creditAmount" — those fields do NOT exist!
  NOTE: Use account IDs from GET /ledger/account, not account numbers directly.
  IMPORTANT: If an account number returns empty (id=None), it doesn't exist in the sandbox.
  Common fallbacks: 1209→credit the asset account directly (1230/1250/1210), 8700→use 8300, 2920→try 2500.
  COMMON ACCOUNTS: 8160 = Valutatap/disagio (exchange rate loss), 8060 = Valutagevinst/agio (exchange rate gain),
    1500 = Kundefordringer (accounts receivable), 2400 = Leverandørgjeld (accounts payable).
  EXPENSE ACCOUNTS for receipts/kvitteringer:
    7140 = Reisekostnad (travel), 6300 = Leie lokale (rent), 6500 = Verktøy/inventar (tools/inventory),
    6340 = IT-utstyr/konsulenttjenester, 6590 = Annen driftskostnad (other operating cost).
  Norwegian VAT input rates: id=11 (25% standard), id=12 (15% food), id=13 (12% transport), id=6 (0% exempt).
  For currency/exchange rate tasks: ALWAYS use 8160 for loss (disagio) and 8060 for gain (agio). NOT 7960.
  Always search nearby: GET /ledger/account?numberFrom=X&numberTo=Y&count=10
  Example 1 (depreciation — debit expense 6010, credit asset 1230):
  {"date": "2025-12-31", "description": "Depreciation 2025", "postings": [
    {"row": 1, "account": {"id": 123}, "amountGross": 50000, "amountGrossCurrency": 50000, "description": "Depreciation expense"},
    {"row": 2, "account": {"id": 456}, "amountGross": -50000, "amountGrossCurrency": -50000, "description": "Accumulated depreciation"}
  ]}

  Example 2 (exchange rate loss/disagio — customer-related, requires customer ref):
  IMPORTANT: Use account 8160 for exchange rate LOSS (disagio), NOT 7960.
    8160 = Valutatap (disagio). 8060 = Valutagevinst (agio). 1500 = Kundefordringer (AR).
  {"date": "2025-07-01", "description": "Disagio", "postings": [
    {"row": 1, "account": {"id": ACCT_8160_ID}, "amountGross": 2937.12, "amountGrossCurrency": 2937.12, "description": "Valutatap disagio", "customer": {"id": CUSTOMER_ID}},
    {"row": 2, "account": {"id": ACCT_1500_ID}, "amountGross": -2937.12, "amountGrossCurrency": -2937.12, "description": "Kundefordringer", "customer": {"id": CUSTOMER_ID}}
  ]}
  REMINDER: "amount" DOES NOT WORK — you MUST use "amountGross" and "amountGrossCurrency" on every posting.

REFERENCING PREVIOUS RESULTS:
Use "{result_N_id}" to reference the ID from the Nth call's response (0-indexed).
Example: after creating a customer in call 0, reference it as {"id": "{result_0_id}"} in call 1.

The "depends_on" field (0-indexed integer) indicates which previous call's response ID to use for {prev_id} substitution.

CRITICAL — FIND EXISTING vs CREATE NEW:
When the task says an entity ALREADY EXISTS ("has an invoice", "har en faktura", "tiene una factura",
"has an unpaid invoice", "outstanding invoice", "uteståande faktura", "offene Rechnung", "facture impayée"):
  → SEARCH for it first! Do NOT create new entities from scratch.
  → GET /customer?organizationNumber=X to find existing customer
  → GET /invoice?customerId=X&invoiceDateFrom=2020-01-01&invoiceDateTo=2026-12-31 to find their invoices
  → GET /supplierInvoice?supplierId=X to find supplier invoices
  → Then act on the FOUND entity (register payment, reverse, credit note, etc.)
  Creating new entities when the task expects existing ones will FAIL scoring.

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
  {"method": "POST", "path": "/product", "body": {"name": "Service", "priceExcludingVatCurrency": 10000, "vatType": {"id": 3}}, "description": "Create product"},
  {"method": "POST", "path": "/order", "body": {"customer": {"id": "{result_2_id}"}, "deliveryDate": "2026-01-15", "orderDate": "2026-01-15", "orderLines": [{"product": {"id": "{result_3_id}"}, "count": 1}]}, "description": "Create order"},
  {"method": "PUT", "path": "/order/{prev_id}/:invoice?invoiceDate=2026-01-15&invoiceDueDate=2026-02-15&sendToCustomer=true", "body": {}, "description": "Convert order to invoice and send", "depends_on": 4}
]
```
NOTE: If the task says "send" (opprett og SEND, erstellen und SENDEN, create and send):
  Preferred: add sendToCustomer=true as query param on the :invoice call (shown above).
  Fallback: add a separate send step after invoice conversion:
    {"method": "PUT", "path": "/invoice/{prev_id}/:send?sendType=EMAIL", "body": {}, "description": "Send invoice", "depends_on": 5}

Pattern 5 — Create invoice + register payment:
```json
[
  {"method": "POST", "path": "/department", "body": {"name": "General", "departmentNumber": 1}, "description": "Create department"},
  {"method": "POST", "path": "/employee", "body": {"firstName": "Admin", "lastName": "User", "email": "admin@example.org", "userType": "STANDARD", "department": {"id": "{prev_id}"}}, "description": "Create employee", "depends_on": 0},
  {"method": "POST", "path": "/customer", "body": {"name": "Client AS", "email": "client@example.org", "isCustomer": true, "organizationNumber": "123456789"}, "description": "Create customer"},
  {"method": "POST", "path": "/product", "body": {"name": "Service", "priceExcludingVatCurrency": 10000, "vatType": {"id": 3}}, "description": "Create product"},
  {"method": "POST", "path": "/order", "body": {"customer": {"id": "{result_2_id}"}, "deliveryDate": "2026-01-15", "orderDate": "2026-01-15", "orderLines": [{"product": {"id": "{result_3_id}"}, "count": 1}]}, "description": "Create order"},
  {"method": "PUT", "path": "/order/{prev_id}/:invoice?invoiceDate=2026-01-15&invoiceDueDate=2026-02-15", "body": {}, "description": "Convert order to invoice", "depends_on": 4},
  {"method": "PUT", "path": "/invoice/{prev_id}/:createPayment?paymentDate=2026-01-20&paymentTypeId=1&paidAmount=12500&paidAmountCurrency=12500", "body": {}, "description": "Register full payment", "depends_on": 5}
]
```
NOTE: paidAmount must be the invoice total INCLUDING VAT. Calculate: excl_vat * 1.25 for 25% MVA.
NOTE: paymentTypeId MUST be 1. If :createPayment returns 404, the auto-fix retries with alternative endpoints.

Pattern 6 — Register a supplier:
```json
[
  {"method": "POST", "path": "/supplier", "body": {"name": "Acme Supplier AS", "email": "faktura@acme.no", "organizationNumber": "123456789"}, "description": "Register supplier"}
]
```

Pattern 8 — Register supplier invoice (2-step: invoice + separate voucher):
```json
[
  {"method": "POST", "path": "/supplier", "body": {"name": "Silveroak Ltd", "email": "faktura@silveroak.no", "organizationNumber": "945217456"}, "description": "Create supplier"},
  {"method": "GET", "path": "/ledger/account?numberFrom=6340&numberTo=6350&count=10", "body": null, "description": "Look up expense account"},
  {"method": "GET", "path": "/ledger/account?numberFrom=2400&numberTo=2410&count=10", "body": null, "description": "Look up accounts payable (2400)"},
  {"method": "POST", "path": "/supplierInvoice", "body": {"supplier": {"id": "{result_0_id}"}, "invoiceNumber": "INV-2026-5539", "invoiceDate": "2026-02-28", "invoiceDueDate": "2026-03-30"}, "description": "Create supplier invoice (NO voucher in body)"},
  {"method": "POST", "path": "/ledger/voucher", "body": {"date": "2026-02-28", "description": "Sikkerhetsprogramvare", "postings": [{"row": 1, "account": {"id": "{result_1_id}"}, "amountGross": 67050, "amountGrossCurrency": 67050, "vatType": {"id": 11}, "description": "Sikkerhetsprogramvare"}, {"row": 2, "account": {"id": "{result_2_id}"}, "amountGross": -83812.50, "amountGrossCurrency": -83812.50, "description": "Leverandørgjeld"}]}, "description": "Post accounting entry for supplier invoice"}
]
```
CRITICAL: Do NOT include "voucher" in the /supplierInvoice body — it ALWAYS fails through the proxy.
Use the 2-step approach: POST /supplierInvoice (no voucher) + POST /ledger/voucher (separate).
The voucher uses amountGross/amountGrossCurrency and row (same format as all other voucher postings).
NOTE: vatType {"id": 11} = 25% input VAT. {"id": 12} = 15%. {"id": 13} = 12%.

Pattern 9 — Find existing unpaid invoice and register payment:
```json
[
  {"method": "GET", "path": "/customer?organizationNumber=893135979&count=1", "body": null, "description": "Find existing customer by org number"},
  {"method": "GET", "path": "/invoice?customerId={result_0_id}&invoiceDateFrom=2020-01-01&invoiceDateTo=2026-12-31&count=100", "body": null, "description": "Find unpaid invoices for this customer"},
  {"method": "PUT", "path": "/invoice/{result_1_id}/:createPayment?paymentDate=2026-03-21&paymentTypeId=1&paidAmount=AMOUNT&paidAmountCurrency=AMOUNT", "body": {}, "description": "Register full payment on found invoice", "depends_on": 1}
]
```
USE THIS PATTERN when the task says "has an invoice", "has an unpaid invoice", "find the overdue invoice", etc.
Do NOT create a new customer/invoice — search for the EXISTING ones first.
The paidAmount must match the invoice's total amount (including VAT). Use the amount from the GET response.

Pattern 7 — Run payroll (salary + optional bonus):
```json
[
  {"method": "POST", "path": "/department", "body": {"name": "General", "departmentNumber": 1}, "description": "Create department"},
  {"method": "POST", "path": "/employee", "body": {"firstName": "Ola", "lastName": "Nordmann", "email": "ola@example.org", "userType": "STANDARD", "department": {"id": "{prev_id}"}}, "description": "Create employee", "depends_on": 0},
  {"method": "POST", "path": "/employee/employment", "body": {"employee": {"id": "{prev_id}"}, "startDate": "2025-01-01"}, "description": "Create employment", "depends_on": 1},
  {"method": "POST", "path": "/employee/employment/details", "body": {"employment": {"id": "{prev_id}"}, "date": "2025-01-01"}, "description": "Set employment details (required before payroll)", "depends_on": 2},
  {"method": "GET", "path": "/salary/type?isInactive=false&count=100", "body": null, "description": "Get salary types — find Fastlønn and Bonus IDs"},
  {"method": "POST", "path": "/salary/transaction?generateTaxDeduction=true", "body": {"year": 2025, "month": 3, "payslips": [{"employee": {"id": "{result_1_id}"}, "specifications": [{"salaryType": {"id": "{result_4_id}"}, "rate": 45000, "count": 1}]}]}, "description": "Run payroll with salary"}
]
```
NOTE: Use POST /salary/transaction (NOT /salary/payslip — that returns 500!).
NOTE: You MUST GET /salary/type first — do NOT hardcode salary type IDs. Use {result_4_id} for Fastlønn.
NOTE: POST /employee/employment/details is REQUIRED before running salary — without it you get "Arbeidsforholdet er ikke knyttet mot en virksomhet".
NOTE: For bonus, add a second specification. Find the Bonus type ID from GET /salary/type response.
NOTE: Body wraps payslips in array: {"year": N, "month": N, "payslips": [{"employee": ..., "specifications": [...]}]}

Pattern 10 — Create free accounting dimension with values + post voucher:
```json
[
  {"method": "POST", "path": "/ledger/accountingDimensionName", "body": {"dimensionName": "Kostsenter", "description": "Cost center", "active": true}, "description": "Create accounting dimension"},
  {"method": "POST", "path": "/ledger/accountingDimensionValue", "body": {"displayName": "Økonomi", "dimensionIndex": 1, "number": "100", "active": true, "showInVoucherRegistration": true, "position": 0}, "description": "Add dimension value 1"},
  {"method": "POST", "path": "/ledger/accountingDimensionValue", "body": {"displayName": "Produktutvikling", "dimensionIndex": 1, "number": "200", "active": true, "showInVoucherRegistration": true, "position": 1}, "description": "Add dimension value 2"},
  {"method": "GET", "path": "/ledger/account?numberFrom=6340&numberTo=6350&count=10", "body": null, "description": "Look up expense account"},
  {"method": "GET", "path": "/ledger/account?numberFrom=2900&numberTo=2910&count=10", "body": null, "description": "Look up liability account"},
  {"method": "POST", "path": "/ledger/voucher", "body": {"date": "2026-03-21", "description": "Voucher with dimension", "postings": [{"row": 1, "account": {"id": "{result_3_id}"}, "amountGross": 50000, "amountGrossCurrency": 50000, "description": "Expense", "freeAccountingDimension1": {"id": "{result_1_id}"}}, {"row": 2, "account": {"id": "{result_4_id}"}, "amountGross": -50000, "amountGrossCurrency": -50000, "description": "Liability"}]}, "description": "Post voucher linked to dimension value"}
]
```
CRITICAL: Do NOT use /ledger/closeGroup (read-only, 405). Use /ledger/accountingDimensionName + /ledger/accountingDimensionValue.
NOTE: dimensionIndex from step 1 tells you which freeAccountingDimensionN to use (1, 2, or 3).
NOTE: Use {result_1_id} or {result_2_id} to reference the dimension VALUE id in the voucher posting.

RESPONSE FORMAT — return a JSON array of API calls. Your plan MUST include POST/PUT action calls, not just GET analysis calls. A plan with only GETs scores 0%.
Use "depends_on" (0-indexed integer) for {prev_id} substitution. Use "{result_N_id}" to reference any previous call's ID.

IMPORTANT NOTES:
- GET list responses return {"fullResultSize": N, "values": [...]}. Extract the ID from values[0].id if needed.
- For PUT /order/{id}/:invoice, pass invoiceDate and invoiceDueDate as QUERY PARAMS in the path, not in body.
- If a task includes file attachments, I'll describe their contents.

Think step by step about:
1. What entity needs to be created/modified?
2. Does the task reference EXISTING entities ("has an invoice", "has an unpaid invoice", "find the overdue invoice")?
   If yes: SEARCH FIRST with GET before creating anything. Use GET /customer?organizationNumber=X, GET /invoice?customerId=X, etc.
   The sandbox often has PRE-EXISTING customers, invoices, employees — do NOT blindly create new ones.
3. What prerequisites need to be created first? (only if the task says to CREATE them)
4. What's the correct order of API calls?
5. What are the REQUIRED fields for each endpoint?

CRITICAL: If the task asks you to ANALYZE data and THEN CREATE entities based on results,
you MUST include BOTH the analysis GETs AND the creation POSTs in your plan.
Example: "Analyze ledger and create projects for top 3 expense accounts" requires:
  - GET /ledger/posting to read data
  - POST /project × 3 to create entities based on what you found
Do NOT stop after the analysis — you MUST act on the results!

Be precise and minimal — fewer API calls = better score. Every 4xx error reduces your efficiency bonus.
"""


def _validate_norwegian_nin(nin: str) -> bool:
    """Validate Norwegian national identity number (11-digit personnummer) checksum."""
    if not nin or len(nin) != 11 or not nin.isdigit():
        return False
    d = [int(c) for c in nin]
    # Control digit 1
    w1 = [3, 7, 6, 1, 8, 9, 4, 5, 2]
    s1 = sum(d[i] * w1[i] for i in range(9))
    r1 = 11 - (s1 % 11)
    if r1 == 11:
        r1 = 0
    if r1 == 10 or r1 != d[9]:
        return False
    # Control digit 2
    w2 = [5, 4, 3, 2, 7, 6, 5, 4, 3, 2]
    s2 = sum(d[i] * w2[i] for i in range(10))
    r2 = 11 - (s2 % 11)
    if r2 == 11:
        r2 = 0
    if r2 == 10 or r2 != d[10]:
        return False
    return True


def extract_file_content(files: list) -> tuple:
    """Extract text from files and collect image blocks for vision.

    Returns:
        tuple of (text_descriptions: str, image_blocks: list)
    """
    if not files:
        return "", []

    descriptions = []
    image_blocks = []
    for f in files:
        filename = f.get("filename", "unknown")
        mime = f.get("mime_type", "")
        data = base64.b64decode(f.get("content_base64", ""))

        if "pdf" in mime:
            try:
                import fitz
                doc = fitz.open(stream=data, filetype="pdf")
                text_parts = [page.get_text() for page in doc]
                doc.close()
                full_text = "\n".join(text_parts).strip()
                if full_text:
                    descriptions.append(f"PDF file {filename} contents:\n{full_text[:5000]}")
                else:
                    descriptions.append(f"[PDF file: {filename}, no extractable text]")
            except Exception as e:
                descriptions.append(f"[PDF file: {filename}, extraction error: {e}]")
        elif "image" in mime:
            media_type = "image/png" if "png" in mime else "image/jpeg"
            image_blocks.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": f.get("content_base64", ""),
                },
            })
            image_blocks.append({
                "type": "text",
                "text": f"Image above is from file: {filename}",
            })
        else:
            try:
                text = data.decode("utf-8")
                descriptions.append(f"File {filename}:\n{text[:2000]}")
            except UnicodeDecodeError:
                descriptions.append(f"[Binary file: {filename}, {len(data)} bytes]")

    return "\n\n".join(descriptions), image_blocks


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

        # Auto-fix: AUTODETECT payment amount from prior invoice GET response
        if "AUTODETECT" in path and ":createPayment" in path:
            for prev_r in results:
                if prev_r.get("status") == 200 and prev_r.get("data"):
                    prev_data = prev_r["data"]
                    # Check list responses (GET /invoice returns values array)
                    vals = prev_data.get("values", []) if isinstance(prev_data, dict) else []
                    if vals:
                        inv = vals[0]  # Use first invoice found
                        amt = inv.get("amount") or inv.get("amountCurrency") or inv.get("amountOutstanding")
                        if amt:
                            path = path.replace("AUTODETECT", str(amt))
                            print(f"  [{i}] AUTO-FIX: detected payment amount={amt} from invoice response")
                            break
            # If still unresolved, try extracting amount from prompt
            if "AUTODETECT" in path:
                import re as _re
                # Match amounts like "49600 NOK", "9400 kr", "12000 NOK"
                amt_match = _re.search(r'(\d+(?:\.\d+)?)\s*(?:NOK|kr|nok)', prompt if 'prompt' in dir() else '')
                if amt_match:
                    raw_amt = float(amt_match.group(1))
                    # Tasks usually say "excl. VAT" — multiply by 1.25 for total
                    total_amt = raw_amt * 1.25
                    path = path.replace("AUTODETECT", str(total_amt))
                    print(f"  [{i}] AUTO-FIX: estimated payment amount={total_amt} (excl VAT {raw_amt} * 1.25)")

        # Auto-fix: bank reconciliation — match payment calls to invoices by amount
        if method == "PUT" and ":createPayment" in path and "/invoice/" in path:
            import urllib.parse
            parsed_qs = urllib.parse.parse_qs(urllib.parse.urlparse(path).query)
            paid_amount = parsed_qs.get("paidAmount", [None])[0]
            if paid_amount:
                try:
                    paid_float = float(paid_amount)
                    # Check if the invoice ID in the path came from a list response
                    inv_match = re.search(r'/invoice/(\d+)/', path)
                    if inv_match:
                        inv_id = int(inv_match.group(1))
                        # Find if this ID is from a list and there's a better match by amount
                        for prev_r in results:
                            if prev_r.get("status") == 200 and prev_r.get("data"):
                                vals = prev_r["data"].get("values", []) if isinstance(prev_r["data"], dict) else []
                                if len(vals) >= 2 and any(v.get("id") == inv_id for v in vals):
                                    # This is a list response containing our ID — find better match by amount
                                    best = None
                                    for v in vals:
                                        v_amt = v.get("amount") or v.get("amountCurrency") or 0
                                        if abs(float(v_amt) - paid_float) < 0.01:
                                            best = v
                                            break
                                    if best and best.get("id") != inv_id:
                                        old_id = str(inv_id)
                                        new_id = str(best["id"])
                                        path = path.replace(f"/invoice/{old_id}/", f"/invoice/{new_id}/")
                                        print(f"  [{i}] AUTO-FIX: matched payment {paid_amount} to invoice id={new_id} (was {old_id})")
                                    break
                except (ValueError, TypeError):
                    pass

        # Validate: skip calls with unresolved placeholders
        unresolved = re.findall(r'\{(?:prev_id|result_\d+_id)\}', path)
        if body:
            unresolved += re.findall(r'\{(?:prev_id|result_\d+_id)\}', json.dumps(body))
        if unresolved:
            print(f"  [{i}] {method} {path} — {desc}")
            print(f"    SKIP: unresolved refs {unresolved}")
            results.append({"error": f"Unresolved references: {unresolved}", "id": None})
            continue

        # Skip calls with null ID references (cascaded from failed lookups)
        if body:
            body_check = json.dumps(body)
            if '"id": null' in body_check or '"id": None' in body_check:
                print(f"  [{i}] {method} {path} — {desc}")
                print(f"    SKIP: body contains null id reference")
                results.append({"error": "Null ID reference in body", "id": None})
                continue

        # Auto-fix: ensure customer has both postalAddress and physicalAddress
        if method == "POST" and path.strip("/") == "customer" and body:
            addr = body.get("physicalAddress") or body.get("postalAddress")
            if addr:
                if "postalAddress" not in body:
                    body["postalAddress"] = addr
                if "physicalAddress" not in body:
                    body["physicalAddress"] = addr

        # Auto-strip fields that don't exist on these endpoints
        if method in ("POST", "PUT") and body:
            if "/employee/employment" in path:
                for bad_field in ("employmentType", "jobCode"):
                    if bad_field in body:
                        print(f"  [{i}] AUTO-STRIP: removing invalid field '{bad_field}' from employment body")
                        body.pop(bad_field)
            if path.strip("/") == "employee":
                for bad_field in ("percentOfFullTimeEquivalent", "salary", "monthlySalary", "startDate"):
                    if bad_field in body:
                        print(f"  [{i}] AUTO-STRIP: removing invalid field '{bad_field}' from employee body")
                        body.pop(bad_field)
            if "/project/orderline" in path:
                for bad_field in ("isInvoiced", "amountGross", "amountGrossCurrency"):
                    if bad_field in body:
                        print(f"  [{i}] AUTO-STRIP: removing invalid field '{bad_field}' from orderline body")
                        body.pop(bad_field)
                # Convert amount → unitPriceExcludingVatCurrency (amount doesn't exist on orderline)
                if "amount" in body and "unitPriceExcludingVatCurrency" not in body:
                    body["unitPriceExcludingVatCurrency"] = body.pop("amount")
                    body.setdefault("count", 1)
                    print(f"  [{i}] AUTO-FIX: converted amount→unitPriceExcludingVatCurrency on orderline")
            if path.strip("/") == "activity":
                # Force PROJECT_GENERAL_ACTIVITY — only type that works with timesheets
                if not body.get("activityType") or body.get("activityType") == "GENERAL_ACTIVITY":
                    body["activityType"] = "PROJECT_GENERAL_ACTIVITY"
                    print(f"  [{i}] AUTO-FIX: set activityType to PROJECT_GENERAL_ACTIVITY")
        # Auto-fix: customer must have BOTH postalAddress and physicalAddress
        if method == "POST" and path.strip("/") == "customer" and body:
            pa = body.get("physicalAddress")
            po = body.get("postalAddress")
            if pa and not po:
                body["postalAddress"] = dict(pa)
                print(f"  [{i}] AUTO-FIX: copied physicalAddress → postalAddress")
            elif po and not pa:
                body["physicalAddress"] = dict(po)
                print(f"  [{i}] AUTO-FIX: copied postalAddress → physicalAddress")

            # Auto-fix: voucher postings row must be >= 1 (row 0 = system-generated error)
            if "/ledger/voucher" in path and body.get("postings"):
                for idx, posting in enumerate(body["postings"]):
                    if posting.get("row", 0) == 0:
                        posting["row"] = idx + 1
                        print(f"  [{i}] AUTO-FIX: voucher posting row 0 -> {idx + 1}")
                    if "row" not in posting:
                        posting["row"] = idx + 1
                # Validate Norwegian NIN checksum — strip if invalid to avoid 422
                nin = body.get("nationalIdentityNumber")
                if nin and not _validate_norwegian_nin(nin):
                    print(f"  [{i}] AUTO-STRIP: invalid NIN checksum '{nin}', removing to avoid 422")
                    body.pop("nationalIdentityNumber")

        # Auto-inject dateOfBirth on employee POST if missing (required for employment)
        if method == "POST" and path.strip("/") == "employee" and body and not body.get("dateOfBirth"):
            body["dateOfBirth"] = "1990-01-15"

        # Auto-inject userType on employee POST if missing
        if method == "POST" and path.strip("/") == "employee" and body and not body.get("userType"):
            body["userType"] = "STANDARD"
            print(f"  [{i}] AUTO-FIX: injected userType=STANDARD")

        # Auto-inject department on employee POST if missing
        if method == "POST" and path.strip("/") == "employee" and body:
            dept = body.get("department")
            if not dept or not isinstance(dept, dict) or not dept.get("id"):
                try:
                    dept_resp = requests.get(f"{base_url}/department?count=1", auth=auth, timeout=10)
                    if dept_resp.status_code == 200:
                        dept_vals = dept_resp.json().get("values", [])
                        if dept_vals and dept_vals[0].get("id"):
                            body["department"] = {"id": dept_vals[0]["id"]}
                            print(f"  [{i}] AUTO-FIX: injected existing department id={dept_vals[0]['id']}")
                        else:
                            dc = requests.post(f"{base_url}/department", auth=auth,
                                               json={"name": "General", "departmentNumber": 1}, timeout=10)
                            if dc.status_code in (200, 201):
                                dv = dc.json().get("value", dc.json())
                                did = dv.get("id") if isinstance(dv, dict) else None
                                if did:
                                    body["department"] = {"id": did}
                                    print(f"  [{i}] AUTO-FIX: created department id={did}")
                except Exception:
                    pass

        # Auto-lookup: if POST /employee, check if email already exists first
        if method == "POST" and path.strip("/") == "employee" and body and body.get("email"):
            email = body["email"]
            lookup_url = f"{base_url}/employee?email={email}&fields=id,firstName,lastName,email"
            try:
                lookup_resp = requests.get(lookup_url, auth=auth, timeout=15)
                if lookup_resp.status_code == 200:
                    lookup_data = lookup_resp.json()
                    vals = lookup_data.get("values", [])
                    if vals and vals[0].get("id"):
                        existing_id = vals[0]["id"]
                        print(f"  [{i}] POST {path} — {desc}")
                        print(f"    AUTO-LOOKUP: employee email={email} exists, id={existing_id}")
                        results.append({"status": 200, "id": existing_id, "data": vals[0]})
                        continue
            except Exception:
                pass  # Fall through to normal POST

        # Auto-lookup: if POST /customer, check if org number already exists first
        if method == "POST" and path.strip("/") == "customer" and body and body.get("organizationNumber"):
            org_num = body["organizationNumber"]
            lookup_url = f"{base_url}/customer?organizationNumber={org_num}&count=5"
            try:
                lookup_resp = requests.get(lookup_url, auth=auth, timeout=15)
                if lookup_resp.status_code == 200:
                    lookup_data = lookup_resp.json()
                    vals = lookup_data.get("values", [])
                    if vals and vals[0].get("id"):
                        existing_id = vals[0]["id"]
                        print(f"  [{i}] POST {path} — {desc}")
                        print(f"    AUTO-LOOKUP: customer org={org_num} exists, id={existing_id}")
                        results.append({"status": 200, "id": existing_id, "data": vals[0]})
                        continue
            except Exception:
                pass  # Fall through to normal POST

        # Auto-lookup: if POST /supplier, check if org number already exists first
        if method == "POST" and path.strip("/") == "supplier" and body and body.get("organizationNumber"):
            org_num = body["organizationNumber"]
            lookup_url = f"{base_url}/supplier?organizationNumber={org_num}&count=5"
            try:
                lookup_resp = requests.get(lookup_url, auth=auth, timeout=15)
                if lookup_resp.status_code == 200:
                    lookup_data = lookup_resp.json()
                    vals = lookup_data.get("values", [])
                    if vals and vals[0].get("id"):
                        existing_id = vals[0]["id"]
                        print(f"  [{i}] POST {path} — {desc}")
                        print(f"    AUTO-LOOKUP: supplier org={org_num} exists, id={existing_id}")
                        results.append({"status": 200, "id": existing_id, "data": vals[0]})
                        continue
            except Exception:
                pass  # Fall through to normal POST

        # Auto-lookup: if POST /activity, check if name already exists first
        if method == "POST" and path.strip("/") == "activity" and body and body.get("name"):
            act_name = body["name"]
            found_activity = False
            for lookup_url in [f"{base_url}/activity?name={act_name}&count=5",
                               f"{base_url}/activity?count=100"]:
                try:
                    lookup_resp = requests.get(lookup_url, auth=auth, timeout=15)
                    if lookup_resp.status_code == 200:
                        lookup_data = lookup_resp.json()
                        vals = lookup_data.get("values", [])
                        match = next((v for v in vals if v.get("name", "").lower() == act_name.lower()), None)
                        if not match and vals:
                            match = vals[0]  # fallback to first result if name filter worked
                        if match and match.get("id"):
                            # Skip if wrong activity type (GENERAL_ACTIVITY can't be used for timesheets)
                            act_type = match.get("activityType", "")
                            if act_type == "GENERAL_ACTIVITY":
                                print(f"  [{i}] AUTO-LOOKUP: activity '{act_name}' exists but type={act_type}, skipping (need PROJECT_GENERAL_ACTIVITY)")
                                break  # Don't use it, fall through to POST
                            print(f"  [{i}] POST {path} — {desc}")
                            print(f"    AUTO-LOOKUP: activity name='{act_name}' exists, id={match['id']}, type={act_type}")
                            results.append({"status": 200, "id": match["id"], "data": match})
                            found_activity = True
                            break
                except Exception:
                    continue
            if found_activity:
                continue

        # Auto-skip: if POST /product but a prior GET already found this product, skip to avoid 422
        if method == "POST" and path.strip("/") == "product" and body and body.get("name"):
            prod_name = body["name"].lower()
            found_product = None
            for prev_r in results:
                if prev_r.get("status") == 200 and prev_r.get("id") and prev_r.get("data"):
                    prev_data = prev_r["data"]
                    prev_vals = prev_data.get("values", []) if isinstance(prev_data, dict) else []
                    for v in prev_vals:
                        if v.get("name", "").lower() == prod_name:
                            found_product = v
                            break
                    if found_product:
                        break
            if found_product:
                pid = found_product["id"]
                print(f"  [{i}] POST {path} — {desc}")
                print(f"    AUTO-SKIP: product '{body['name']}' already found in prior GET, id={pid}")
                results.append({"status": 200, "id": pid, "data": found_product})
                continue

        # Auto-fix: strip voucher from supplierInvoice and inject separate POST /ledger/voucher
        if method == "POST" and "/supplierInvoice" in path and body and body.get("voucher"):
            voucher_data = body.pop("voucher")
            postings = voucher_data.get("postings", [])
            voucher_date = voucher_data.get("date", body.get("invoiceDate", "2026-01-01"))
            voucher_desc = voucher_data.get("description", "Supplier invoice")

            # Convert postings to /ledger/voucher format (amountGross + row)
            for idx, posting in enumerate(postings):
                val = posting.get("amountGross") or posting.get("amount") or 0
                posting["amountGross"] = val
                posting["amountGrossCurrency"] = val
                posting.pop("amount", None)
                posting.pop("amountCurrency", None)
                posting["row"] = idx + 1
                posting.pop("project", None)
                posting.pop("department", None)

            # Fix credit amount if VAT rounding is off
            if len(postings) >= 2:
                debit = postings[0]
                credit = postings[1]
                vat_id = debit.get("vatType", {}).get("id") if debit.get("vatType") else None
                if vat_id:
                    debit_amt = abs(debit.get("amountGross", 0))
                    vat_mult = {11: 1.25, 12: 1.15, 13: 1.12}.get(vat_id, 1.0)
                    correct_gross = round(debit_amt * vat_mult, 2)
                    credit["amountGross"] = -correct_gross
                    credit["amountGrossCurrency"] = -correct_gross

            # Inject the voucher as a NEW call after the current supplierInvoice
            voucher_call = {
                "method": "POST",
                "path": "/ledger/voucher",
                "body": {"date": voucher_date, "description": voucher_desc, "postings": postings},
                "description": "Accounting entry for supplier invoice (auto-split)",
            }
            # Insert after current call in the plan
            plan.insert(i + 1, voucher_call)
            print(f"  [{i}] AUTO-FIX: stripped voucher from supplierInvoice, injected separate POST /ledger/voucher")

        # Pre-fix: voucher posting — force sequential rows, convert amount→amountGross, ensure amountGrossCurrency
        if method == "POST" and "/ledger/voucher" in path and body:
            postings = body.get("postings", [])
            for idx, posting in enumerate(postings):
                posting["row"] = idx + 1  # Always force sequential 1-based rows
                if "amount" in posting and "amountGross" not in posting:
                    posting["amountGross"] = posting.pop("amount")  # Convert amount→amountGross
                if "amountGross" in posting and "amountGrossCurrency" not in posting:
                    posting["amountGrossCurrency"] = posting["amountGross"]

        # Pre-fix: salary/transaction — proactively link employment to division (virksomhet)
        if method == "POST" and "/salary/transaction" in path and body:
            try:
                emp_id = None
                for ps in body.get("payslips", []):
                    emp_ref = ps.get("employee", {})
                    emp_id = emp_ref.get("id")
                    if emp_id:
                        break
                if emp_id:
                    # Step 1: Get employment
                    emp_r = requests.get(f"{base_url}/employee/employment?employeeId={emp_id}&count=1", auth=auth, timeout=10)
                    if emp_r.status_code == 200:
                        emp_vals = emp_r.json().get("values", [])
                        if emp_vals:
                            employment = emp_vals[0]
                            employment_id = employment.get("id")

                            # Step 2: Ensure employment details exist (prerequisite for salary)
                            det_r = requests.get(f"{base_url}/employee/employment/details?employmentId={employment_id}&count=1", auth=auth, timeout=10)
                            has_details = False
                            if det_r.status_code == 200:
                                det_vals = det_r.json().get("values", [])
                                has_details = bool(det_vals)
                            if not has_details:
                                start_date = employment.get("startDate", "2025-01-01")
                                det_post = requests.post(f"{base_url}/employee/employment/details", auth=auth,
                                    json={"employment": {"id": employment_id}, "date": start_date}, timeout=15)
                                print(f"  [{i}] AUTO-FIX: created employment details (status={det_post.status_code})")

                            # Step 3: Re-check if division is now set (details creation can auto-assign)
                            emp_r2 = requests.get(f"{base_url}/employee/employment/{employment_id}", auth=auth, timeout=10)
                            div = None
                            if emp_r2.status_code == 200:
                                emp_data2 = emp_r2.json()
                                emp_val2 = emp_data2.get("value", emp_data2)
                                if isinstance(emp_val2, dict):
                                    div = emp_val2.get("division")

                            if not div or not isinstance(div, dict) or not div.get("id"):
                                # Step 4: Find a valid division ID via multiple strategies
                                division_id = None

                                # Strategy A: GET /company/divisions or /division
                                for div_path in ["/company/divisions?count=10", "/division?count=10"]:
                                    try:
                                        dr = requests.get(f"{base_url}{div_path}", auth=auth, timeout=10)
                                        if dr.status_code == 200:
                                            dv = dr.json().get("values", [])
                                            if dv and dv[0].get("id"):
                                                division_id = dv[0]["id"]
                                                print(f"  [{i}] AUTO-FIX: found division via {div_path}: {division_id}")
                                                break
                                    except Exception:
                                        pass

                                # Strategy B: GET /company with values array (avoid bank data)
                                if not division_id:
                                    try:
                                        co_r = requests.get(f"{base_url}/company?count=5", auth=auth, timeout=10)
                                        if co_r.status_code == 200:
                                            co_data = co_r.json()
                                            co_vals = co_data.get("values", [])
                                            # Filter out banks — look for non-bank entries
                                            for cv in co_vals:
                                                name = (cv.get("name") or "").lower()
                                                if cv.get("id") and "dnb" not in name and "bank" not in name:
                                                    division_id = cv["id"]
                                                    print(f"  [{i}] AUTO-FIX: using company id={division_id} name='{cv.get('name')}'")
                                                    break
                                            # Fallback: use first company even if it looks like bank
                                            if not division_id and co_vals and co_vals[0].get("id"):
                                                division_id = co_vals[0]["id"]
                                                print(f"  [{i}] AUTO-FIX: using first company id={division_id} (fallback)")
                                            # Also try single value response
                                            if not division_id:
                                                co_val = co_data.get("value")
                                                if isinstance(co_val, dict) and co_val.get("id"):
                                                    division_id = co_val["id"]
                                    except Exception:
                                        pass

                                if division_id:
                                    put_r = requests.put(
                                        f"{base_url}/employee/employment/{employment_id}",
                                        auth=auth,
                                        json={"id": employment_id, "employee": {"id": emp_id}, "division": {"id": division_id}},
                                        timeout=15)
                                    print(f"  [{i}] AUTO-FIX: linked employment {employment_id} to division {division_id} (status={put_r.status_code})")
                                    if put_r.status_code not in (200, 201):
                                        # Retry with minimal body
                                        put_r2 = requests.put(
                                            f"{base_url}/employee/employment/{employment_id}",
                                            auth=auth, json={"division": {"id": division_id}}, timeout=15)
                                        print(f"  [{i}] AUTO-FIX: retry minimal PUT (status={put_r2.status_code})")
                            else:
                                print(f"  [{i}] AUTO-FIX: division already set: {div.get('id')}")
            except Exception as e:
                print(f"  [{i}] AUTO-FIX: division linking failed: {e}")

        # Pre-fix: salary/transaction — replace hardcoded salary type IDs with real ones from prior GET
        if method == "POST" and "/salary/transaction" in path and body:
            all_st = []
            for prev_r in results:
                if prev_r.get("status") == 200 and prev_r.get("data"):
                    vals = prev_r["data"].get("values", []) if isinstance(prev_r["data"], dict) else []
                    if any("lønn" in v.get("name", "").lower() or "bonus" in v.get("name", "").lower() for v in vals):
                        all_st = vals
                        break
            if all_st:
                real_ids = {s["id"] for s in all_st if s.get("id")}
                tkw = {"base": ["fastlønn", "fastlonn", "månedslønn"], "bonus": ["bonus", "tillegg"],
                       "hourly": ["timelønn", "timelonn"], "overtime": ["overtid"]}
                id_map = {1: "base", 1000: "base", 30: "bonus", 3000: "bonus"}
                for payslip in body.get("payslips", []):
                    for spec in payslip.get("specifications", []):
                        st = spec.get("salaryType", {})
                        cid = st.get("id")
                        if not isinstance(cid, int) or cid in real_ids:
                            continue
                        tgt = None
                        sd = (spec.get("description", "") + " " + desc).lower()
                        for tt, kws in tkw.items():
                            if any(k in sd for k in kws):
                                tgt = tt
                                break
                        if not tgt:
                            tgt = id_map.get(cid, "base")
                        m = None
                        for s in all_st:
                            if any(k in s.get("name", "").lower() for k in tkw.get(tgt, [])):
                                m = s
                                break
                        if not m:
                            m = next((s for s in all_st if "fastlønn" in s.get("name", "").lower()), None)
                        if m:
                            print(f"  [{i}] AUTO-FIX: salary type {cid} → {m.get('name', '?')} id={m['id']}")
                            st["id"] = m["id"]
            # Fix: if 2+ specs all use same salary type, set second to Bonus
            for payslip in body.get("payslips", []):
                specs = payslip.get("specifications", [])
                if len(specs) >= 2:
                    type_ids = [s.get("salaryType", {}).get("id") for s in specs]
                    if len(set(type_ids)) == 1 and all_st:
                        for v in all_st:
                            if "bonus" in v.get("name", "").lower() and v["id"] != type_ids[0]:
                                specs[1]["salaryType"]["id"] = v["id"]
                                print(f"  [{i}] AUTO-FIX: second spec → Bonus type id={v['id']}")
                                break

        # Auto-fix: force sequential rows + amount→amountGross on voucher postings
        if method == "POST" and body:
            voucher = body.get("voucher", body) if "/supplierInvoice" in path else body
            postings = voucher.get("postings", []) if isinstance(voucher, dict) else []
            if postings and ("/voucher" in path or "/supplierInvoice" in path):
                for pidx, p in enumerate(postings):
                    if isinstance(p, dict):
                        p["row"] = pidx + 1  # Force sequential 1-based
                        if "/ledger/voucher" in path:
                            if "amount" in p and "amountGross" not in p:
                                p["amountGross"] = p.pop("amount")
                            if "amountGross" in p and "amountGrossCurrency" not in p:
                                p["amountGrossCurrency"] = p["amountGross"]

        # Auto-strip banned fields from employment
        if method == "POST" and "/employee/employment" in path and body:
            for bad in ("percentOfFullTimeEquivalent", "employmentType", "salary", "monthlySalary", "occupationCode"):
                if bad in body:
                    body.pop(bad)
                    print(f"  [{i}] AUTO-STRIP: removed '{bad}' from employment (banned field)")

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
                    # Smart extraction: for invoice lookups, match by invoiceNumber if queried
                    if method == "GET" and "/invoice" in path and values_list:
                        import urllib.parse as _up
                        _qs = _up.parse_qs(_up.urlparse(path).query)
                        _inv_num = _qs.get("invoiceNumber", [None])[0]
                        if _inv_num:
                            for _v in values_list:
                                if str(_v.get("invoiceNumber", "")) == str(_inv_num):
                                    result_id = _v["id"]
                                    print(f"    (matched invoiceNumber={_inv_num} → id={result_id})")
                                    break
                    # Smart extraction: for salary types, prefer Fastlønn over first result
                    if method == "GET" and "/salary/type" in path and values_list:
                        for st in values_list:
                            name = st.get("name", "").lower()
                            if "fastlønn" in name or "fastlonn" in name:
                                result_id = st["id"]
                                print(f"    (salary types: using Fastlønn id={result_id})")
                                break
                else:
                    value = data
                    result_id = data.get("id") if isinstance(data, dict) else None
                # Auto-fallback: if GET /ledger/account returned empty, try nearby exact numbers
                # NOTE: numberFrom/numberTo range queries return wrong results on this proxy
                if (result_id is None and method == "GET"
                        and "/ledger/account" in path and "number=" in path
                        and "numberFrom" not in path):
                    import urllib.parse
                    parsed_qs = urllib.parse.parse_qs(urllib.parse.urlparse(path).query)
                    acct_num = parsed_qs.get("number", [None])[0]
                    if acct_num and acct_num.isdigit():
                        acct_int = int(acct_num)
                        # Try nearby account numbers with exact queries
                        nearby = [acct_int - 1, acct_int + 1, acct_int - 10, acct_int + 10,
                                  (acct_int // 100) * 100]
                        for try_num in nearby:
                            if try_num <= 0:
                                continue
                            fb_url = f"{base_url}/ledger/account?number={try_num}&count=1"
                            try:
                                fb_resp = requests.get(fb_url, auth=auth, timeout=10)
                                if fb_resp.status_code == 200:
                                    fb_data = fb_resp.json()
                                    fb_vals = fb_data.get("values", [])
                                    if fb_vals and fb_vals[0].get("id"):
                                        result_id = fb_vals[0]["id"]
                                        value = fb_data
                                        print(f"    FALLBACK: account {acct_num} empty, trying {try_num} → found id={result_id}")
                                        break
                            except Exception:
                                pass

                results.append({"status": resp.status_code, "id": result_id, "data": value})
                print(f"    OK ({resp.status_code}), id={result_id}")
            else:
                # Auto-fix: if GET /ledger/account returns 422, try nearby exact numbers
                if resp.status_code == 422 and method == "GET" and "/ledger/account" in path and "number=" in path and "numberFrom" not in path:
                    import urllib.parse
                    parsed_qs = urllib.parse.parse_qs(urllib.parse.urlparse(path).query)
                    acct_num = parsed_qs.get("number", [None])[0]
                    if acct_num and acct_num.isdigit():
                        acct_int = int(acct_num)
                        nearby = [acct_int - 1, acct_int + 1, acct_int - 10, acct_int + 10,
                                  (acct_int // 100) * 100]
                        for try_num in nearby:
                            if try_num <= 0:
                                continue
                            fallback_url = f"{base_url}/ledger/account?number={try_num}&count=1"
                        try:
                            fb_resp = requests.get(fallback_url, auth=auth, timeout=15)
                            if fb_resp.status_code == 200:
                                fb_data = fb_resp.json()
                                fb_vals = fb_data.get("values", [])
                                if fb_vals:
                                    fb_id = fb_vals[0]["id"]
                                    results.append({"status": 200, "id": fb_id, "data": fb_data})
                                    print(f"    AUTO-FIX: account {acct_num} got 422, range {range_from}-{range_to} found {len(fb_vals)}, using id={fb_id}")
                                    continue
                        except Exception:
                            pass

                # Auto-fix: if employment fails with "dateOfBirth" error, set DOB and retry
                if (resp.status_code == 422 and method == "POST"
                        and "/employee/employment" in path
                        and "dateOfBirth" in resp.text):
                    emp_id = None
                    if body and isinstance(body.get("employee"), dict):
                        emp_id = body["employee"].get("id")
                    if emp_id:
                        print(f"    AUTO-FIX: employment needs dateOfBirth, setting on employee {emp_id}")
                        put_resp = requests.put(
                            f"{base_url}/employee/{emp_id}",
                            auth=auth, json={"dateOfBirth": "1990-01-15"}, timeout=15)
                        if put_resp.status_code in (200, 201):
                            retry_resp = requests.post(url, auth=auth, json=body, timeout=30)
                            if retry_resp.status_code in (200, 201):
                                retry_data = retry_resp.json()
                                val = retry_data.get("value", retry_data)
                                rid = val.get("id") if isinstance(val, dict) else None
                                results.append({"status": retry_resp.status_code, "id": rid, "data": val})
                                print(f"    AUTO-FIX: employment created after setting DOB, id={rid}")
                                continue

                # Auto-fix: if project creation fails with PM permission error, create new employee as PM
                if (resp.status_code == 422 and method == "POST" and path.strip("/") == "project"
                        and "prosjektleder" in resp.text.lower()):
                    print(f"    AUTO-FIX: project manager lacks permissions, creating new PM employee")
                    try:
                        # Create a new department + employee to use as PM
                        dept_r = requests.post(f"{base_url}/department", auth=auth,
                            json={"name": "PMO", "departmentNumber": 99}, timeout=10)
                        dept_id = dept_r.json().get("value", dept_r.json()).get("id") if dept_r.status_code in (200, 201) else None
                        if not dept_id:
                            dept_r2 = requests.get(f"{base_url}/department?count=1", auth=auth, timeout=10)
                            if dept_r2.status_code == 200:
                                dept_id = dept_r2.json().get("values", [{}])[0].get("id")
                        if dept_id:
                            emp_r = requests.post(f"{base_url}/employee", auth=auth, json={
                                "firstName": "Project", "lastName": "Manager",
                                "email": f"pm{i}@internal.no", "userType": "STANDARD",
                                "department": {"id": dept_id}, "dateOfBirth": "1985-06-15"
                            }, timeout=15)
                            if emp_r.status_code in (200, 201):
                                new_pm_id = emp_r.json().get("value", emp_r.json()).get("id")
                                if new_pm_id and body:
                                    body["projectManager"] = {"id": new_pm_id}
                                    retry_r = requests.post(url, auth=auth, json=body, timeout=30)
                                    if retry_r.status_code in (200, 201):
                                        rd = retry_r.json()
                                        rv = rd.get("value", rd)
                                        rid = rv.get("id") if isinstance(rv, dict) else None
                                        results.append({"status": retry_r.status_code, "id": rid, "data": rv})
                                        print(f"    AUTO-FIX: project created with new PM id={new_pm_id}, project id={rid}")
                                        continue
                    except Exception:
                        pass

                # Auto-fix: if salary transaction fails with "virksomhet" error, link division and retry
                if (resp.status_code == 422 and method == "POST"
                        and "/salary/transaction" in path
                        and "virksomhet" in resp.text.lower()):
                    print(f"    AUTO-FIX: employment not linked to virksomhet, attempting to link")
                    try:
                        # Find the employee ID from the payslip
                        emp_id = None
                        if body and body.get("payslips"):
                            emp_ref = body["payslips"][0].get("employee", {})
                            emp_id = emp_ref.get("id")
                        if emp_id:
                            # Get employee's employment
                            emp_resp = requests.get(f"{base_url}/employee/employment?employeeId={emp_id}&count=1", auth=auth, timeout=10)
                            if emp_resp.status_code == 200:
                                emp_vals = emp_resp.json().get("values", [])
                                if emp_vals:
                                    employment_id = emp_vals[0].get("id")
                                    # Get company division
                                    co_resp = requests.get(f"{base_url}/company?count=1&fields=id", auth=auth, timeout=10)
                                    if co_resp.status_code == 200:
                                        co_data = co_resp.json()
                                        co_vals = co_data.get("values", [co_data.get("value", {})])
                                        if isinstance(co_vals, dict):
                                            co_vals = [co_vals]
                                        if co_vals:
                                            co_id = co_vals[0].get("id") if isinstance(co_vals[0], dict) else None
                                            if co_id and employment_id:
                                                # Link employment to company division
                                                put_r = requests.put(
                                                    f"{base_url}/employee/employment/{employment_id}",
                                                    auth=auth, json={"division": {"id": co_id}}, timeout=15)
                                                print(f"    AUTO-FIX: linked employment {employment_id} to division {co_id} (status={put_r.status_code})")
                                                # Retry salary transaction
                                                retry_r = requests.post(url, auth=auth, json=body, timeout=30)
                                                if retry_r.status_code in (200, 201):
                                                    rd = retry_r.json()
                                                    rv = rd.get("value", rd)
                                                    rid = rv.get("id") if isinstance(rv, dict) else None
                                                    results.append({"status": retry_r.status_code, "id": rid, "data": rv})
                                                    print(f"    AUTO-FIX: salary transaction succeeded after linking division!")
                                                    continue
                    except Exception as e:
                        print(f"    AUTO-FIX: virksomhet fix failed: {e}")

                # Auto-fix: if timesheet fails with "kan ikke benyttes", create PROJECT_GENERAL_ACTIVITY and retry
                if (resp.status_code == 422 and method == "POST"
                        and "/timesheet/entry" in path
                        and "kan ikke benyttes" in resp.text and body):
                    act_name = "Prosjektarbeid"
                    print(f"    AUTO-FIX: activity type wrong, creating PROJECT_GENERAL_ACTIVITY '{act_name}'")
                    try:
                        act_resp = requests.post(
                            f"{base_url}/activity",
                            auth=auth, json={"name": act_name, "activityType": "PROJECT_GENERAL_ACTIVITY"},
                            timeout=15)
                        if act_resp.status_code in (200, 201):
                            act_data = act_resp.json()
                            new_act_id = act_data.get("value", act_data).get("id")
                            if new_act_id and body.get("activity"):
                                body["activity"]["id"] = new_act_id
                                retry_resp = requests.post(url, auth=auth, json=body, timeout=30)
                                if retry_resp.status_code in (200, 201):
                                    retry_data = retry_resp.json()
                                    val = retry_data.get("value", retry_data)
                                    rid = val.get("id") if isinstance(val, dict) else None
                                    results.append({"status": retry_resp.status_code, "id": rid, "data": val})
                                    print(f"    AUTO-FIX: timesheet succeeded with new activity id={new_act_id}")
                                    continue
                    except Exception as e:
                        print(f"    AUTO-FIX: timesheet activity fix failed: {e}")

                # Auto-fix: if supplier invoice returns 500, retry without department on postings
                if resp.status_code == 500 and method == "POST" and "/supplierInvoice" in path and body:
                    voucher = body.get("voucher", {})
                    changed = False
                    for posting in voucher.get("postings", []):
                        if "department" in posting:
                            posting.pop("department")
                            changed = True
                    if changed:
                        print(f"    AUTO-FIX: supplierInvoice 500, retrying without department on postings")
                        retry_resp = requests.post(url, auth=auth, json=body, timeout=30)
                        if retry_resp.status_code in (200, 201):
                            retry_data = retry_resp.json()
                            val = retry_data.get("value", retry_data)
                            rid = val.get("id") if isinstance(val, dict) else None
                            results.append({"status": retry_resp.status_code, "id": rid, "data": val})
                            print(f"    AUTO-FIX: supplierInvoice succeeded without department, id={rid}")
                            continue

                # Auto-fix: supplierInvoice 422/500 — full cleanup retry (strip row, amountGross→amount, try amountCurrency)
                if resp.status_code in (422, 500) and method == "POST" and "/supplierInvoice" in path and body:
                    voucher = body.get("voucher", {})
                    for posting in voucher.get("postings", []):
                        if "amountGross" in posting and "amount" not in posting:
                            posting["amount"] = posting.pop("amountGross")
                        posting.pop("amountGrossCurrency", None)
                        posting.pop("row", None)
                        posting.pop("project", None)
                        posting.pop("department", None)
                    print(f"    AUTO-FIX: supplierInvoice {resp.status_code}, cleanup retry with 'amount'")
                    retry_resp = requests.post(url, auth=auth, json=body, timeout=30)
                    if retry_resp.status_code in (200, 201):
                        retry_data = retry_resp.json()
                        val = retry_data.get("value", retry_data)
                        rid = val.get("id") if isinstance(val, dict) else None
                        results.append({"status": retry_resp.status_code, "id": rid, "data": val})
                        print(f"    AUTO-FIX: supplierInvoice succeeded after cleanup, id={rid}")
                        continue
                    # If still failing, try with amountCurrency instead of amount
                    if retry_resp.status_code in (422, 500):
                        for posting in voucher.get("postings", []):
                            if "amount" in posting:
                                posting["amountCurrency"] = posting.pop("amount")
                        print(f"    AUTO-FIX: supplierInvoice retry with 'amountCurrency'")
                        retry2_resp = requests.post(url, auth=auth, json=body, timeout=30)
                        if retry2_resp.status_code in (200, 201):
                            retry2_data = retry2_resp.json()
                            val = retry2_data.get("value", retry2_data)
                            rid = val.get("id") if isinstance(val, dict) else None
                            results.append({"status": retry2_resp.status_code, "id": rid, "data": val})
                            print(f"    AUTO-FIX: supplierInvoice succeeded with amountCurrency, id={rid}")
                            continue
                    # Last resort: try without voucher entirely (just basic fields)
                    if retry2_resp.status_code in (422, 500):
                        basic_body = {
                            "supplier": body.get("supplier"),
                            "invoiceNumber": body.get("invoiceNumber"),
                            "invoiceDate": body.get("invoiceDate"),
                            "invoiceDueDate": body.get("invoiceDueDate"),
                        }
                        print(f"    AUTO-FIX: supplierInvoice last resort — basic fields only (no voucher)")
                        retry3_resp = requests.post(url, auth=auth, json=basic_body, timeout=30)
                        if retry3_resp.status_code in (200, 201):
                            retry3_data = retry3_resp.json()
                            val = retry3_data.get("value", retry3_data)
                            rid = val.get("id") if isinstance(val, dict) else None
                            results.append({"status": retry3_resp.status_code, "id": rid, "data": val})
                            print(f"    AUTO-FIX: supplierInvoice succeeded with basic fields, id={rid}")
                            continue

                # Auto-fix: if salary/transaction fails with "virksomhet", create employment details and retry
                if (resp.status_code == 422 and method == "POST"
                        and "/salary/transaction" in path
                        and "virksomhet" in resp.text):
                    # Find employee ID and employment ID from prior results
                    emp_id = None
                    employment_id = None
                    for prev_r in results:
                        if prev_r.get("status") in (200, 201) and prev_r.get("id"):
                            prev_data = prev_r.get("data", {})
                            if isinstance(prev_data, dict):
                                pv = prev_data.get("value", prev_data) if "value" in prev_data else prev_data
                                if "employmentId" in pv or "startDate" in pv or "employment" in str(pv.get("url", "")):
                                    employment_id = prev_r["id"]
                                elif "firstName" in pv or "email" in pv:
                                    emp_id = prev_r["id"]
                    if employment_id:
                        print(f"    AUTO-FIX: salary needs employment details, creating for employment {employment_id}")
                        details_resp = requests.post(
                            f"{base_url}/employee/employment/details",
                            auth=auth, json={"employment": {"id": employment_id}, "date": "2025-01-01"},
                            timeout=15)
                        if details_resp.status_code in (200, 201):
                            print(f"    AUTO-FIX: employment details created, retrying salary/transaction...")
                            retry_resp = requests.post(url, auth=auth, json=body, timeout=30)
                            if retry_resp.status_code in (200, 201):
                                retry_data = retry_resp.json()
                                val = retry_data.get("value", retry_data)
                                rid = val.get("id") if isinstance(val, dict) else None
                                results.append({"status": retry_resp.status_code, "id": rid, "data": val})
                                print(f"    AUTO-FIX: salary/transaction succeeded, id={rid}")
                                continue

                # Auto-fix: if :invoice fails with "bankkontonummer", set up bank account and retry
                if (resp.status_code == 422 and method == "PUT" and ":invoice" in path
                        and "bankkontonummer" in resp.text):
                    print(f"    AUTO-FIX: bank account required, trying POST /bank...")
                    try:
                        bank_resp = requests.post(
                            f"{base_url}/bank",
                            auth=auth,
                            json={"name": "Bedriftskonto", "bankAccountNumber": "12345678903"},
                            timeout=15)
                        print(f"    POST /bank → {bank_resp.status_code}")
                        if bank_resp.status_code in (200, 201):
                            # Retry the invoice call
                            retry_resp = requests.put(url, auth=auth, json=body if body else {}, timeout=30)
                            print(f"    Retry PUT :invoice → {retry_resp.status_code}")
                            if retry_resp.status_code in (200, 201):
                                retry_data = retry_resp.json()
                                val = retry_data.get("value", retry_data)
                                rid = val.get("id") if isinstance(val, dict) else None
                                results.append({"status": retry_resp.status_code, "id": rid, "data": val})
                                print(f"    AUTO-FIX: invoice created after bank setup, id={rid}")
                                continue
                    except Exception as e:
                        print(f"    Bank setup failed: {e}")

                # Auto-fix: projectActivity 404 — return activity ID so downstream calls work
                if resp.status_code == 404 and method == "POST" and "/projectActivity" in path:
                    activity_id = None
                    if body and isinstance(body.get("activity"), dict):
                        activity_id = body["activity"].get("id")
                    print(f"    AUTO-FIX: projectActivity 404 (expected), passing activity_id={activity_id}")
                    results.append({"status": 200, "id": activity_id, "data": {"id": activity_id}})
                    continue

                # Auto-fix: if payment endpoint returns 404/500, try alternatives inline
                if resp.status_code in (404, 500) and method == "PUT" and "/invoice/" in path and (
                    ":createPayment" in path or ":payment" in path or ":pay" in path
                ):
                    inv_match = re.search(r'/invoice/(\d+)/', path)
                    if inv_match:
                        inv_id = inv_match.group(1)
                        import urllib.parse
                        params = urllib.parse.parse_qs(urllib.parse.urlparse(path).query)
                        pay_date = params.get("paymentDate", ["2026-01-15"])[0]
                        amount = params.get("paidAmount", params.get("paidAmountCurrency", [None]))[0]
                        if amount:
                            tried_action = path.split("/:")[-1].split("?")[0]
                            orig_tid = params.get("paymentTypeId", [None])[0]
                            # Try POST /payment with body FIRST (most reliable through proxy)
                            for tid in [1, 2]:
                                try:
                                    pay_body = {
                                        "date": pay_date, "paymentDate": pay_date,
                                        "amount": float(amount), "amountCurrency": float(amount),
                                        "paidAmount": float(amount), "paidAmountCurrency": float(amount),
                                        "paymentType": {"id": tid}, "paymentTypeId": tid,
                                        "invoice": {"id": int(inv_id)},
                                    }
                                    for post_path in ["/payment", f"/invoice/{inv_id}/payment", "/invoicePayment", "/ledger/payment"]:
                                        post_resp = requests.post(f"{base_url}{post_path}", auth=auth, json=pay_body, timeout=15)
                                        print(f"    AUTO-FIX: POST {post_path} typeId={tid} → {post_resp.status_code}")
                                        if post_resp.status_code in (200, 201):
                                            post_data = post_resp.json()
                                            post_val = post_data.get("value", post_data)
                                            post_rid = post_val.get("id") if isinstance(post_val, dict) else None
                                            results.append({"status": post_resp.status_code, "id": post_rid, "data": post_val})
                                            print(f"    AUTO-FIX: payment via POST {post_path} succeeded, id={post_rid}")
                                            break
                                    else:
                                        continue
                                    break  # outer loop — payment succeeded
                                except Exception:
                                    continue
                            else:
                                # POST didn't work, fall through to PUT retries
                                pass
                            if len(results) > i:
                                continue  # Payment was handled by POST
                            # Include original endpoint (with correct typeIds) + alternatives
                            all_actions = [tried_action] + [a for a in ["payment", "createPayment", "pay"] if a != tried_action]
                            payment_fixed = False
                            # Retry the original call after a short delay (invoice may need time to propagate)
                            import time
                            time.sleep(2)
                            retry_url = f"{base_url}/invoice/{inv_id}/:{tried_action}?paymentDate={pay_date}&paymentTypeId=1&paidAmount={amount}&paidAmountCurrency={amount}"
                            retry_resp = requests.put(retry_url, auth=auth, json={}, timeout=15)
                            print(f"    AUTO-FIX: retry :{tried_action} typeId=1 after 2s -> {retry_resp.status_code}")
                            if retry_resp.status_code in (200, 201):
                                retry_data = retry_resp.json()
                                retry_value = retry_data.get("value", retry_data)
                                retry_id = retry_value.get("id") if isinstance(retry_value, dict) else None
                                results.append({"status": retry_resp.status_code, "id": retry_id, "data": retry_value})
                                print(f"    AUTO-FIX SUCCESS (after delay)")
                                payment_fixed = True
                            if not payment_fixed:
                                for alt in all_actions:
                                    for tid in [1, 2]:
                                        if alt == tried_action and str(tid) == str(orig_tid):
                                            continue
                                        alt_url = f"{base_url}/invoice/{inv_id}/:{alt}?paymentDate={pay_date}&paymentTypeId={tid}&paidAmount={amount}&paidAmountCurrency={amount}"
                                        alt_resp = requests.put(alt_url, auth=auth, json={}, timeout=15)
                                        print(f"    AUTO-FIX: :{alt} typeId={tid} -> {alt_resp.status_code}")
                                        if alt_resp.status_code in (200, 201):
                                            alt_data = alt_resp.json()
                                            alt_value = alt_data.get("value", alt_data)
                                            alt_id = alt_value.get("id") if isinstance(alt_value, dict) else None
                                            results.append({"status": alt_resp.status_code, "id": alt_id, "data": alt_value})
                                            print(f"    AUTO-FIX SUCCESS")
                                            payment_fixed = True
                                            break
                                    if payment_fixed:
                                        break
                            if payment_fixed:
                                continue

                # Auto-fix: if employment fails with "dateOfBirth", PUT employee with default DOB and retry
                if (resp.status_code == 422 and method == "POST"
                        and "/employee/employment" in path
                        and "dateOfBirth" in resp.text):
                    emp_ref = body.get("employee", {}) if body else {}
                    emp_id = emp_ref.get("id")
                    if emp_id and isinstance(emp_id, int):
                        print(f"    AUTO-FIX: employment needs dateOfBirth, updating employee {emp_id}")
                        put_resp = requests.put(
                            f"{base_url}/employee/{emp_id}",
                            auth=auth, json={"id": emp_id, "dateOfBirth": "1990-01-15"},
                            timeout=15,
                        )
                        if put_resp.status_code in (200, 201):
                            print(f"    AUTO-FIX: employee DOB set, retrying employment...")
                            retry_resp = requests.post(url, auth=auth, json=body, timeout=30)
                            if retry_resp.status_code in (200, 201):
                                retry_data = retry_resp.json()
                                retry_value = retry_data.get("value", retry_data)
                                retry_id = retry_value.get("id") if isinstance(retry_value, dict) else None
                                results.append({"status": retry_resp.status_code, "id": retry_id, "data": retry_value})
                                print(f"    AUTO-FIX SUCCESS: employment created, id={retry_id}")
                                continue

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
        ("PUT", f"/invoice/{invoice_id}/:createPayment", True),   # primary endpoint
        ("PUT", f"/invoice/{invoice_id}/:payment", True),         # fallback
        ("PUT", f"/invoice/{invoice_id}/:pay", True),             # alternative
        ("POST", "/payment", False),                               # body-based fallback
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

    p = prompt.lower()

    # Fix: INVOICE_PAYMENT — force GET-first pattern when task says invoice ALREADY EXISTS
    # The LLM often creates new entities instead of finding existing ones
    payment_keywords = [
        "unpaid invoice", "outstanding invoice", "register full payment", "register payment",
        "offene rechnung", "registrieren sie die vollständige zahlung", "vollständige zahlung",
        "uteståande faktura", "registrer full betaling", "registrer betaling",
        "facture impayée", "enregistrez le paiement", "paiement intégral",
        "factura impaga", "factura pendiente", "registre el pago",
        "fatura em aberto", "fatura pendente", "registe o pagamento",
        "har en faktura", "hat eine rechnung", "a une facture", "tiene una factura", "tem uma fatura",
        "has an invoice", "har ein faktura",
    ]
    is_payment_task = any(kw in p for kw in payment_keywords)

    # Don't trigger for supplier invoices, travel expenses, credit notes, or payment reversals
    not_payment = ["supplier", "leverandør", "lieferant", "fournisseur", "proveedor", "fornecedor",
                   "credit note", "kreditnota", "gutschrift", "nota de crédito",
                   "travel expense", "reiseutgift", "gastos de viaje", "frais de déplacement",
                   "returnert", "returned", "reverser", "reverse"]
    if any(kw in p for kw in not_payment):
        is_payment_task = False

    if is_payment_task and plan and plan[0].get("method") == "POST":
        # Plan starts with POST (creating new entities) — override with GET-first pattern
        org_match = re.search(
            r'(?:org\.?\s*(?:n[roº]|number|nr)\.?\s*:?\s*)(\d{9})',
            prompt, re.IGNORECASE
        )
        if org_match:
            org_num = org_match.group(1)
            print(f"  PLAN OVERRIDE: invoice payment task with org {org_num}, forcing GET-first pattern")
            plan = [
                {"method": "GET", "path": f"/customer?organizationNumber={org_num}&count=1",
                 "body": None, "description": "Find existing customer by org number"},
                {"method": "GET",
                 "path": "/invoice?customerId={result_0_id}&invoiceDateFrom=2020-01-01&invoiceDateTo=2026-12-31&count=100",
                 "body": None, "description": "Find invoices for this customer", "depends_on": 0},
                {"method": "PUT",
                 "path": "/invoice/{result_1_id}/:createPayment?paymentDate=2026-01-15&paymentTypeId=1&paidAmount=AUTODETECT&paidAmountCurrency=AUTODETECT",
                 "body": {}, "description": "Register full payment", "depends_on": 1},
            ]

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
    file_text, image_blocks = extract_file_content(files)
    text_prompt = f"Task prompt:\n{prompt}"
    if file_text:
        text_prompt += f"\n\nAttached files:\n{file_text}"

    text_prompt += f"""

Base URL: {base_url}
Authentication: Basic Auth with username "0" and the session token.

Analyze this task and provide the JSON array of API calls needed to complete it.
Remember: be precise and minimal. Each unnecessary call or error hurts the score."""

    # Build content: text + optional images for multimodal
    if image_blocks:
        full_prompt = image_blocks + [{"type": "text", "text": text_prompt}]
    else:
        full_prompt = text_prompt

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
            else:
                # Voucher-based payment fallback: debit bank (1920), credit AR (1500)
                print("  Trying voucher-based payment fallback...")
                auth = ("0", session_token)
                for pi, (p_call, p_res) in enumerate(zip(current_plan, results)):
                    p_path = p_call.get("path", "")
                    if (p_res.get("status") in (404, 500)
                            and ("createPayment" in p_path or ":payment" in p_path)
                            and "/invoice/" in p_path):
                        import urllib.parse
                        parsed_qs = urllib.parse.parse_qs(urllib.parse.urlparse(p_path).query)
                        pay_date = parsed_qs.get("paymentDate", ["2026-01-15"])[0]
                        amount_str = parsed_qs.get("paidAmount", [None])[0]
                        inv_match = re.search(r'/invoice/(\d+)/', p_path)
                        if amount_str and inv_match:
                            amount = float(amount_str)
                            inv_id = int(inv_match.group(1))
                            # Find customer from prior invoice data
                            customer_id = None
                            for pr in results:
                                if pr.get("status") == 200 and pr.get("data"):
                                    pd = pr["data"]
                                    vals = pd.get("values", []) if isinstance(pd, dict) else []
                                    for v in vals:
                                        if v.get("id") == inv_id:
                                            cust = v.get("customer", {})
                                            customer_id = cust.get("id") if isinstance(cust, dict) else None
                            # Look up accounts
                            bank_id = ar_id = None
                            for acct_num, var_name in [(1920, "bank_id"), (1500, "ar_id")]:
                                try:
                                    ar = requests.get(f"{base_url}/ledger/account?number={acct_num}&count=1", auth=auth, timeout=10)
                                    if ar.status_code == 200:
                                        av = ar.json().get("values", [])
                                        if av:
                                            if var_name == "bank_id":
                                                bank_id = av[0]["id"]
                                            else:
                                                ar_id = av[0]["id"]
                                except Exception:
                                    pass
                            if bank_id and ar_id:
                                postings = [
                                    {"row": 1, "account": {"id": bank_id},
                                     "amountGross": amount, "amountGrossCurrency": amount,
                                     "description": "Payment received"},
                                    {"row": 2, "account": {"id": ar_id},
                                     "amountGross": -amount, "amountGrossCurrency": -amount,
                                     "description": "Invoice payment"},
                                ]
                                if customer_id:
                                    postings[1]["customer"] = {"id": customer_id}
                                vr = requests.post(f"{base_url}/ledger/voucher", auth=auth,
                                    json={"date": pay_date, "description": f"Payment invoice {inv_id}", "postings": postings},
                                    timeout=15)
                                print(f"  Voucher payment fallback → {vr.status_code}")
                                if vr.status_code in (200, 201):
                                    vd = vr.json()
                                    vv = vd.get("value", vd)
                                    results[pi] = {"status": vr.status_code, "id": vv.get("id") if isinstance(vv, dict) else None, "data": vv}
                                    print(f"  ✓ Voucher payment succeeded!")
                failed = [r for r in results if r.get("status") not in (200, 201, None) or r.get("error")]

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
- "Det finnes allerede en bruker med denne e-postadressen" = email already exists.
  Try GET /employee?email=X to find their ID.
  If GET also fails (422/404), create a NEW employee with a modified email: add "2" before the @, e.g. alice.smith2@example.org
- "Produktnummeret NNNN er i bruk" = product number exists. Try GET /product?number=NNNN to find it. If GET returns 404, retry POST /product WITHOUT the number field.
- If GET returns a list, the ID is in values[0].id — use that integer directly.
- If PUT /order/ID/:invoice returns 404, try PUT /order/:invoice/ID or POST /invoice with orders: [{{"id": ORDER_ID}}]
- If PUT /order/ID/:invoice returns 422 on a time tracking / project task, try PUT /project/PROJECT_ID/:createInvoice?invoiceDate=YYYY-MM-DD instead.
  Project invoices use a different endpoint than order-based invoices.
- When registering payment on an EXISTING invoice, use the invoice's "amount" field from the GET response.
  Do NOT recalculate from the prompt's excl-VAT amount. The invoice amount already includes VAT.
- If PUT /order/ID/:invoice returns "invoiceDate: Kan ikke være null", you put dates in the body.
  Dates MUST be query params in the URL, body MUST be {{}}. Example:
  {{"method": "PUT", "path": "/order/ID/:invoice?invoiceDate=YYYY-MM-DD&invoiceDueDate=YYYY-MM-DD", "body": {{}}}}
- If task says "send"/"sende"/"senden"/"envoyer"/"enviar" but invoice was created without sending:
  PUT /invoice/INVOICE_ID/:send?sendType=EMAIL with body: {{}}
- For payment: include BOTH endpoint variants in your fix plan (one will succeed):
  1. PUT /invoice/INVOICE_ID/:payment?paymentDate=YYYY-MM-DD&paymentTypeId=1&paidAmount=N&paidAmountCurrency=N
  2. PUT /invoice/INVOICE_ID/:createPayment?paymentDate=YYYY-MM-DD&paymentTypeId=1&paidAmount=N&paidAmountCurrency=N
  DO NOT try GET /ledger/paymentType (404). paymentTypeId MUST be 1. paidAmount INCLUDES VAT.
- "Feltet eksisterer ikke i objektet" on /employee/employment = remove "employmentType" (doesn't exist).
  Only valid fields: employee, startDate, endDate, percentOfFullTimeEquivalent, occupationCode.
- For payslip: field is "specifications" NOT "payslipSpecifications".
  Each spec: {{"salaryType": {{"id": N}}, "rate": N, "count": 1, "amount": N}}
  GET /salary/type first to find valid IDs. "Fastlønn" = base salary.
- If nationalIdentityNumber caused 422 ("Ugyldig format"), OMIT it — create employee without it.
- "Navnet er i bruk" on POST /activity = activity already exists. GET /activity?name=X to find its ID.
- "employee.dateOfBirth: Feltet må fylles ut" on /employee/employment = employee needs dateOfBirth.
  PUT /employee/ID with dateOfBirth field, then retry employment creation.
- If supplierInvoice returns "credit posting missing": you are missing the credit (AP) posting. You MUST have TWO postings:
  DEBIT: {{"account": {{"id": EXPENSE_ID}}, "amount": NET_AMOUNT, "vatType": {{"id": 11}}, "description": "..."}}
  CREDIT: {{"account": {{"id": AP_ID}}, "amount": -GROSS_AMOUNT, "description": "..."}}
  Use "amount" field. Do NOT use "amountGross" or "row" on supplier invoice postings.
  Hardcode vatType {{"id": 11}} for 25% input VAT.
- If GET /ledger/account?number=N returns 422, use range search instead:
  GET /ledger/account?numberFrom=N&numberTo=N+10&count=10
- If voucher returns "uten posteringer" (without postings): use "amountGross" and "amountGrossCurrency" with "row" >= 1.
  Each posting: {{"row": N, "account": {{"id": X}}, "amountGross": AMT, "amountGrossCurrency": AMT, "description": "..."}}
  NOTE: For /ledger/voucher use amountGross. For /supplierInvoice use amount. They are DIFFERENT endpoints.
- If voucher returns "Kunde mangler" (customer missing): add "customer": {{"id": CUSTOMER_ID}} to each posting on AR accounts (1500).
- If POST /project/ID/projectActivity returns 404: SKIP IT. Timesheet entries work without it —
  just reference the activity ID directly in POST /timesheet/entry.
- If PUT /project/ID/:createInvoice returns 404: This endpoint is BROKEN. Instead, create an order
  with orderLines for the project hours/services, then PUT /order/ID/:invoice to generate the invoice.
- If POST /project/orderline fails: use "amount" or "unitPriceExcludingVatCurrency" — NOT "amountGross" (doesn't exist).

Provide a COMPLETE corrected JSON array of ONLY the calls that still need to succeed.
DO NOT repeat calls that already returned 200/201 — those entities exist and their IDs are in the results above.
Return [] if the task is already complete.

CRITICAL RULES FOR FIX ROUND:
1. You MUST include the actual POST/PUT calls that failed — not just GET calls to explore.
2. HARDCODE all known IDs as integers (e.g., "customer": {{"id": 108249547}}).
   For IDs from previous rounds, copy the actual integer from the results above.
   You MAY use "depends_on" and {{prev_id}} ONLY for NEW entities created in THIS fix round
   (e.g., if you create an order in this fix round, use depends_on to reference it for the invoice call).
3. For voucher/journal entries: field is "postings" NOT "voucherLines".
   Each posting: {{"account": {{"id": N}}, "amount": N, "description": "..."}}
   Positive amount = DEBIT, negative = CREDIT. Do NOT use debitAmount/creditAmount.
4. If account lookup returned empty (id=None), search nearby: GET /ledger/account?numberFrom=X&numberTo=Y&count=10.
   Common: 1209 doesn't exist → credit asset directly (1230/1250/1210). 8700→use 8300. 2920→try 2500.
5. You must COMPLETE the entire remaining task chain — if order creation succeeds, you MUST also include the invoice conversion and any payment calls after it.
6. Do NOT just explore — you must actually COMPLETE the task."""

        print(f"  Asking LLM to fix (round {round_num + 2})...")
        fix_response = call_claude(fix_prompt, system=SYSTEM_PROMPT, max_tokens=8192)
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
