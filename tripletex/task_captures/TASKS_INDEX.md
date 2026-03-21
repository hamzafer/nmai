# Tripletex Task Index

**Last updated:** 2026-03-21 22:28
**Total unique task types seen:** 30 of 30
**Total real submissions analyzed:** 123

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
| PAYMENT_REVERSAL | PARTIAL | 6/8 | No | 1 |

## Tier 3 (x3 multiplier)

| Task Type | Status | Best Result | Has Files | Attempts |
|-----------|--------|-------------|-----------|----------|
| MONTH_END_CLOSING | FAILED | 2/10 | No | 1 |
| YEAR_END_CLOSING | PARTIAL | 13/17 | No | 1 |
| PDF_EMPLOYEE_CONTRACT | PARTIAL | 4/8 | Yes (PDF) | 2 |
| BANK_RECONCILIATION_CSV | FAILED | 0/10 | Yes (CSV) | 1 |

| LEDGER_ANALYSIS_PROJECT | FAILED | 0/10 | No | 1 |
| PROJECT_FULL_CYCLE | FAILED | 2/11 | No | 1 |

## Not Yet Identified (5 remaining task types)

We've seen 26 of 30 task types. The remaining 4 will appear as we submit more.
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
| 2026-03-21 19:58 | 20260321_195800.md | TRAVEL_EXPENSE | 4.5/8 (56%) | Spanish. 0 errors, all 9 calls OK. Score low: wrong per diem rate type, cost categories |
| 2026-03-21 20:03 | 20260321_200300.md | PAYMENT_REVERSAL | 6/8 (75%) | NEW — bank returned payment, reverse via voucher. 0 errors, 5 calls |
| 2026-03-21 21:08 | 20260321_210800.md | INVOICE_PAYMENT | 2/7 (29%) | French. Created new entities instead of finding existing unpaid invoice. 404 on payment |
| 2026-03-21 21:11 | 20260321_211100.md | TIME_TRACKING | 4/8 (50%) | Portuguese. Timesheet OK, hourly rate set in fix round, project invoice creation failed |
| 2026-03-21 21:15 | 20260321_211500.md | LEDGER_ANALYSIS_PROJECT | 0/10 (0%) | NEW TYPE — German. Only GET calls, no projects/activities created. Agent stopped after reading ledger |
| 2026-03-21 21:16 | 20260321_211600.md | PROJECT_FULL_CYCLE | 2/11 (18%) | NEW TYPE — Norwegian. Full project lifecycle: budget, 2 employees timesheet, supplier cost, invoice. Many 422/404 errors |
| 2026-03-21 21:33 | 20260321_213300.md | SUPPLIER_INVOICE | 2/10 (20%) | German. PDF attached (small). Supplier created OK but invoice registration 500 error |
| 2026-03-21 21:35 | 20260321_213500.md | PROJECT_FIXED_PRICE | 2/8 (25%) | Norwegian. Fixed price + 25% partial invoice. 422 on invoice creation |
| 2026-03-21 21:39 | 20260321_213900.md | PAYROLL | 2/10 (20%) | Nynorsk. Base salary + one-time bonus. 422 on salary spec, 405 on fix attempt |
| 2026-03-21 20:41 | 20260321_204100.md | PAYROLL | 0/8 (0%) | Nynorsk. Bonus variant. Employment 422, salary 422, fix 405 |
| 2026-03-21 20:43 | 20260321_204400.md | BANK_RECONCILIATION_CSV | 0/10 (0%) | German. 14/19 OK but reconciliation matching wrong. Still 0% |
| 2026-03-21 20:46 | 20260321_204600.md | CUSTOMER_CREATE | 5/8 (63%) | Norwegian. 1 call, 0 errors, 7.4s. Missing address fields cost points |
| 2026-03-21 20:48 | 20260321_204800.md | CUSTOM_DIMENSION_VOUCHER | 0/13 (0%) | Spanish. Created depts instead of dimensions. Still unsolved |
| 2026-03-21 20:53 | 20260321_205300.md | RECEIPT_EXPENSE_PDF | 0/10 (0%) | English. Togbillett receipt PDF. 4/5 OK but supplierInvoice creation failed |
| 2026-03-21 20:56 | 20260321_205600.md | PAYMENT_REVERSAL | 6/8 (75%) | Norwegian. Correctly found existing entities. Negative payment failed. Same 75% |
| 2026-03-21 21:00 | 20260321_210000.md | PRODUCT_CREATE | 7/7 (100%) | PERFECT! Portuguese. 2 calls, 0 errors, 13.77s |
| 2026-03-21 21:02 | 20260321_210200.md | CUSTOM_DIMENSION_VOUCHER | 0/13 (0%) | Nynorsk. Used /ledger/closeGroup — wrong endpoint. 4th attempt, still 0% |
| 2026-03-21 21:04 | 20260321_210400.md | CURRENCY_EXCHANGE_PAYMENT | 2/10 (20%) | Spanish. Found existing entities. Wrong disagio account (7960 vs 8160). Voucher failed |
| 2026-03-21 21:08 | 20260321_210800b.md | PDF_EMPLOYEE_CONTRACT | 8/14 (57%) | Portuguese. Best score yet. Employment details cascading failures |
| 2026-03-21 21:09 | — | BANK_RECONCILIATION_CSV | 0/10 (0%) | Portuguese. 9/14 OK but still 0% score |
| 2026-03-21 21:12 | — | CREDIT_NOTE | 8/8 (100%) | PERFECT! English. 2/3 OK, 20s |
| 2026-03-21 21:13 | — | PROJECT_FIXED_PRICE | 2/8 (25%) | Norwegian. 5/7 OK, 84.66s |
| 2026-03-21 21:18 | — | PAYMENT_REVERSAL | 2/8 (25%) | Portuguese. 2/3 OK, 73.6s |
| 2026-03-21 21:21 | — | YEAR_END_CLOSING | 6/10 (60%) | Norwegian. 7/13 OK, 63s. Depreciation + tax provision |
| 2026-03-21 21:24 | — | MONTH_END_CLOSING | 7/10 (70%) | Portuguese. Best score! 7/10 OK. Accrual + depreciation + salary provision |
| 2026-03-21 21:25 | — | BANK_RECONCILIATION_CSV | 0/10 (0%) | French. 8/8 GETs OK but no payments posted |
| 2026-03-21 21:27 | — | PROJECT_CREATE | 0/7 (0%) | Portuguese. 4/4 OK but 0% — wrong project structure? |
| 2026-03-21 21:28 | — | PDF_EMPLOYEE_CONTRACT | 7/14 (50%) | French. 4/8 OK, 46.85s |
| 2026-03-21 21:29 | — | CURRENCY_EXCHANGE_PAYMENT | 7/10 (70%) | Spanish. 4/6 OK. Best score for disagio type |
| 2026-03-21 21:33 | — | PDF_EMPLOYEE_CONTRACT | 8/14 (57%) | English. 5/7 OK |
| 2026-03-21 21:34 | — | MONTH_END_CLOSING | 10/10 (100%) | PERFECT! Spanish. Accrual + depreciation + salary provision. Best score! |
| 2026-03-21 21:35 | — | PAYROLL | 0/8 (0%) | Spanish. 4/5 OK but scored 0%. Salary + bonus |
| 2026-03-21 21:38 | — | CREDIT_NOTE | 8/8 (100%) | PERFECT! German. 3/3 OK, 13s. Fourth 100% tonight! |
| 2026-03-21 21:40 | — | SUPPLIER_INVOICE | 2/10 (20%) | French PDF. 3/4 OK |
| 2026-03-21 21:42 | — | PAYROLL | 0/8 (0%) | French. 4/5 OK but scored 0% |
| 2026-03-21 21:45 | — | PRODUCT_CREATE | 7/7 (100%) | PERFECT! Spanish. Repeat 100% |
| 2026-03-21 21:46 | — | CURRENCY_EXCHANGE_PAYMENT | 6/8 (75%) | German. 5/6 OK |
| 2026-03-21 21:48 | — | TIME_TRACKING | 4/8 (50%) | French. 9/10 OK, 108s |
| 2026-03-21 21:51 | — | TRAVEL_EXPENSE | 4.5/8 (56%) | Portuguese. 9/9 OK! All calls succeeded |
| 2026-03-21 21:54 | — | TIME_TRACKING | 4/8 (50%) | Portuguese. 9/10 OK, 122s |
| 2026-03-21 21:57 | — | PAYMENT_REVERSAL | 6/8 (75%) | French. 2/3 OK |
| 2026-03-21 22:00 | — | LEDGER_ANALYSIS_PROJECT | 0/10 (0%) | Norwegian. 10/10 OK but scored 0% — GET-only task, no action taken |
| 2026-03-21 22:01 | — | LEDGER_ANALYSIS_PROJECT | 7.5/10 (75%) | Nynorsk. 7/10 OK! Huge improvement from 0% to 75%! |
| 2026-03-21 22:04 | — | INVOICE_PAYMENT | 2/7 (29%) | French. 2/3 OK |
| 2026-03-21 22:06 | — | LEDGER_ANALYSIS_PROJECT | ?/? (pending) | Portuguese. 11/11 OK, 28s |
| 2026-03-21 22:08 | — | BANK_RECONCILIATION_CSV | 0/10 (0%) | French. 11/16 OK but still 0%. Matching logic broken |
| 2026-03-21 22:12 | — | SUPPLIER_CREATE | 6/6 (100%) | PERFECT! Norwegian. 1 call, 9.49s |
| 2026-03-21 22:15 | — | LEDGER_ANALYSIS_PROJECT | 2/10 (20%) | English. 7/10 OK |
| 2026-03-21 22:17 | — | SUPPLIER_CREATE | 6/6 (100%) | PERFECT! German. 1 call, 10.54s |
| 2026-03-21 22:18 | — | LEDGER_ANALYSIS_PROJECT | 0/10 (0%) | Norwegian. 11/11 OK but 0%. Inconsistent scoring (was 75% earlier) |
| 2026-03-21 22:20 | — | EMPLOYEE_WITH_DETAILS | 8/8 (100%) | PERFECT! French. 3/3 OK, 22s |
| 2026-03-21 22:21 | — | SUPPLIER_INVOICE | 2/10 (20%) | Norwegian PDF. 3/4 OK |
| 2026-03-21 22:22 | — | PROJECT_CREATE | 7/7 (100%) | PERFECT! Spanish. 4/4 OK, 22s |
| 2026-03-21 22:24 | — | PAYROLL | ?/8 | French. 5/6 OK. May have scored! |
| 2026-03-21 22:24 | — | PAYROLL | 0/8 (0%) | French. 5/6 OK but 0% |
| 2026-03-21 22:28 | — | PROJECT_FULL_CYCLE | 10/22 (45%) | Portuguese. 13/18 OK! Best score (was 18%) |
| 2026-03-21 22:30 | — | PRODUCT_CREATE | 6/7 (86%) | English. 2/2 OK |
| 2026-03-21 22:32 | — | PDF_EMPLOYEE_CONTRACT | 4/11 (36%) | Norwegian PDF. 4/7 OK |

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
