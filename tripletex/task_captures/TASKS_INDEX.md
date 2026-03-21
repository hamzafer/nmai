# Tripletex Task Index

**Last updated:** 2026-03-21 19:51
**Total unique task types seen:** 23 of 30
**Total real submissions analyzed:** 56

## Status Legend
- PERFECT = all API calls succeeded (0 errors)
- PARTIAL = some calls succeeded, some failed
- FAILED = no calls succeeded
- NOT SEEN = task type never encountered

---

## Tier 1 (x1 multiplier)

| Task Type | Status | Best Result | Has Files | Attempts |
|-----------|--------|-------------|-----------|----------|
| EMPLOYEE_SIMPLE | FAILED | 0/2 | No | 2 |
| EMPLOYEE_ADMIN | NOT SEEN | — | — | 0 |
| EMPLOYEE_WITH_DETAILS | PERFECT | 3/3 | No | 2 |
| CUSTOMER_CREATE | PERFECT | 3/3 | No | 3 |
| DEPARTMENTS_CREATE | PERFECT | 3/3 | No | 3+ |
| SUPPLIER_CREATE | PERFECT | 1/1 | No | 7 |
| PRODUCT_CREATE | PERFECT | 2/2 | No | 1 |

## Tier 2 (x2 multiplier)

| Task Type | Status | Best Result | Has Files | Attempts |
|-----------|--------|-------------|-----------|----------|
| INVOICE_CREATE_SEND | PARTIAL | 8/9 | No | 5 |
| INVOICE_PAYMENT | PARTIAL | 7/9 | No | 3 |
| INVOICE_MULTI_LINE | PARTIAL | 13/17 | No | 2 |
| ORDER_MULTI_PRODUCT_INVOICE_PAY | PARTIAL | 5/6 | No | 1 |
| PROJECT_CREATE | PERFECT | 4/4 | No | 6 |
| CREDIT_NOTE | PERFECT | 7/7 | No | 2 |
| PAYROLL | PARTIAL | 3/10 | No | 1 |
| TRAVEL_EXPENSE | PARTIAL | 9/9 calls OK, 4.5/8 score | No | 2 |
| SUPPLIER_INVOICE | PARTIAL | 3/5 | Yes (PDF) | 2 |
| TIME_TRACKING | PARTIAL | 5/13 | No | 1 |
| PROJECT_FIXED_PRICE | PARTIAL | 7/12 | No | 1 |
| REMINDER_FEE | PARTIAL | 6/11 | No | 1 |

## Tier 2/3 (new discoveries)

| Task Type | Status | Best Result | Has Files | Attempts |
|-----------|--------|-------------|-----------|----------|
| CUSTOM_DIMENSION_VOUCHER | FAILED | 0/6 | No | 2 |
| RECEIPT_EXPENSE_PDF | FAILED | 0/10 | Yes (PDF) | 1 |
| CURRENCY_EXCHANGE_PAYMENT | PARTIAL | 7/10 | No | 1 |

## Tier 3 (x3 multiplier)

| Task Type | Status | Best Result | Has Files | Attempts |
|-----------|--------|-------------|-----------|----------|
| MONTH_END_CLOSING | FAILED | 2/10 | No | 1 |
| YEAR_END_CLOSING | PARTIAL | 13/17 | No | 1 |
| PDF_EMPLOYEE_CONTRACT | PARTIAL | 4/8 | Yes (PDF) | 2 |
| BANK_RECONCILIATION_CSV | FAILED | 0/10 | Yes (CSV) | 1 |

## Not Yet Identified (13 remaining task types)

We've seen 23 of 30 task types. The remaining 7 will appear as we submit more.
Possible unseen types based on Tripletex API capabilities:
- Employee update/delete
- Customer with address
- Invoice deletion/reversal
- Supplier invoice from PDF (with VAT)
- Bank reconciliation
- Voucher/journal entry
- Budget entries
- ~~Currency transactions~~ → SEEN as CURRENCY_EXCHANGE_PAYMENT
- Inventory/stock management
- Contact person management
- Activity-based invoicing
- Recurring invoices
- Balance sheet reports

---

## Captures Log

| Timestamp | File | Task Type | Score | Notes |
|-----------|------|-----------|-------|-------|
| 2026-03-21 18:01 | 20260321_180100.md | SUPPLIER_INVOICE (PDF) | 2/10 (20%) | PDF not extracted, credit posting missing |
| 2026-03-21 18:07 | 20260321_180700.md | CUSTOM_DIMENSION_VOUCHER | 0/13 (0%) | /ledger/closeGroup 405, wrong endpoint for free dimensions |
| 2026-03-21 18:10 | 20260321_181100.md | RECEIPT_EXPENSE_PDF | 0/10 (0%) | PDF not read, supplier invoice credit posting missing, account lookup wrong |
| 2026-03-21 18:15 | 20260321_181500.md | DEPARTMENTS_CREATE | 7/7 (100%) | PERFECT — 3 POST /department, 0 errors, 10s |
| 2026-03-21 18:47 | 20260321_184700.md | ORDER_MULTI_PRODUCT_INVOICE_PAY | 0/8 (0%) | Everything OK except payment: used `:payment` instead of `:createPayment` |
| 2026-03-21 18:54 | 20260321_185400.md | TIME_TRACKING_PROJECT_INVOICE | 4/8 (50%) | Activity already exists (GET first), employment needs DOB, project invoice 404 |
| 2026-03-21 18:57 | 20260321_185700.md | BANK_RECONCILIATION_CSV | 0/10 (0%) | CSV read but invoice lookup by number returned empty, all payments skipped |
| 2026-03-21 19:01 | 20260321_190100.md | TRAVEL_EXPENSE | 4.5/8 (56%) | ALL 9 calls OK! Score low due to field values (rate/cost categories, dates) |
| 2026-03-21 19:05 | 20260321_190500.md | CUSTOM_DIMENSION_VOUCHER | 0/13 (0%) | Repeat — same issues, no API for free dimensions, voucher postings fail |
| 2026-03-21 19:10 | 20260321_191000.md | MONTH_END_CLOSING | 2/10 (20%) | NEW — all 6 account lookups OK, all 3 voucher postings fail (format issue) |
| 2026-03-21 19:51 | 20260321_195100.md | CURRENCY_EXCHANGE_PAYMENT | 7/10 (70%) | NEW — EUR invoice + disagio. Voucher posting failed: customer missing in postings |
| 2026-03-21 19:55 | 20260321_195500.md | INVOICE_MULTI_LINE | 0/8 (0%) | Spanish. Products existed but agent re-created (422 dup name). Wrong product IDs on order lines |

---

## Key Findings

### PDF Tasks Are Broken
- Agent receives PDF files but never extracts content
- LLM guesses values (names, amounts, ID numbers) → validation failures
- Fix: Add pdfplumber/PyPDF2 for text extraction

### Supplier Invoice Needs Balanced Postings
- POST /supplierInvoice requires debit (expense) + credit (payable) postings
- Similar to voucher format: `postings` array with positive=debit, negative=credit

### Payment Registration Inconsistent
- Multiple endpoint variants: `:createPayment`, `:pay`, `POST /payment`
- paymentTypeId must be ≥ 1 (not 0)
- paidAmount must include VAT

### Efficiency Bonus Being Lost
- Many 4xx errors from trial-and-error reduce efficiency score
- Perfect tasks with 0 errors could score 2x higher

### Custom Accounting Dimensions Unknown
- "Fri regnskapsdimensjon" = free accounting dimension / close group
- POST /ledger/closeGroup returns 405 — endpoint may not support creation via API
- Need to explore Tripletex API docs for correct endpoint
- Voucher posting with dimension reference also unknown

### Currency/Disagio Voucher Needs Customer ID
- When posting exchange rate difference (disagio) voucher, postings must include `customer: {id: ...}`
- Account 8160 = "Valutatap (disagio)" for exchange rate loss
- Account 8060 = likely "Valutagevinst (agio)" for exchange rate gain
- Voucher validation requires customer reference on AR-related postings
