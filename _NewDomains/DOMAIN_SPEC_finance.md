# Finance Domain — DOMAIN_SPEC.md
**Status:** Design phase — nothing built yet  
**Visibility:** Private indefinitely  
**Created:** 2026-03-07

---

## Vision

Personal finance tools that parse real financial documents — bank statements, Airbnb earnings — and produce structured reports for tax preparation and household cost management. Private tooling, not a portfolio piece.

---

## Tax Entities

All income and expenses are held in **personal name (Robert)**. No corporate entity involved at this stage.

| Entity | Filing | Status |
|--------|--------|--------|
| US personal (1040) | Self-file | Active |
| Brazilian CPF | Self-file or accountant | Active |
| RVS Associates | Future | Not in scope yet |

---

## Immediate Scope (2025 Tax Year)

**Priority 1: Brazilian income statement**
- Monthly earnings and costs in BRL
- Meet Brazilian filing deadline first
- Possible accountant for Brazilian side if US income complicates it

**Priority 2: US 1040**
- Includes Brazilian income (Airbnb + other)
- Foreign income conversion at monthly BRL/USD rates
- Self-file

**Strategy:** File Brazilian taxes first (online, meet deadline). Come back to refile with US income included if needed.

---

## Source Documents

| Source | Format | Bank/Platform |
|--------|--------|---------------|
| Brazilian bank statements | OFX or CSV | Itaú |
| Airbnb earnings | CSV | Airbnb dashboard |
| US bank statements | TBD | TBD |

**Note on Itaú:** Exports clean OFX and CSV. OFX preferred for parsing — check export options in Itaú app before building parser.

---

## Folder Structure

```
domains/finance/
├── statements/                ← gitignored entirely
│   ├── itau/                  ← raw Itaú OFX/CSV exports
│   └── airbnb/                ← Airbnb earnings CSVs
├── tools/
│   ├── itau_parser.py         ← Itaú-specific adapter
│   ├── airbnb_parser.py       ← Airbnb CSV parser
│   ├── categorizer.py         ← assigns income/expense categories
│   ├── fx_converter.py        ← BRL→USD at monthly rates
│   └── report.py              ← generates monthly statement
├── reports/                   ← gitignored
│   └── 2025/                  ← generated outputs by month
├── config/
│   └── categories.yaml        ← income/expense category definitions
└── DOMAIN_SPEC.md             ← this file
```

---

## Output Format (Monthly)

```
=== Income Statement — March 2025 ===
Currency: BRL (USD equivalent at 2025-03 rate: 4.97)

INCOME
  Airbnb rentals:        R$ 4,200.00  ($845.07)
  Other income:          R$   500.00  ($100.60)
  TOTAL INCOME:          R$ 4,700.00  ($945.67)

EXPENSES
  Property costs:        R$ 1,200.00  ($241.45)
  Utilities:             R$   350.00  ($70.42)
  Personal:              R$   800.00  ($160.96)
  TOTAL EXPENSES:        R$ 2,350.00  ($472.84)

NET:                     R$ 2,350.00  ($472.84)
```

**Annual summary:** 12 months rolled up, ready for tax prep or accountant handoff.

---

## US Tax Considerations

- Foreign income must be reported on 1040
- Form 2555 (Foreign Earned Income Exclusion) or Form 1116 (Foreign Tax Credit) — determine which applies
- **FBAR (FinCEN 114):** Required if Brazilian bank balance exceeds $10,000 at any point during the year. Currently under threshold — build a warning flag into the tool.
- Monthly exchange rates from IRS published rates or Federal Reserve data

---

## Brazilian Tax Considerations

- CPF filing covers all Brazilian-source income
- Airbnb income in Brazil is taxable — categorize separately
- US income must be declared in Brazilian return (double taxation treaty applies)
- Filing deadline: April 30 each year (carnê-leão for rental income may apply monthly)

---

## Category Schema

```yaml
income:
  - airbnb_rental
  - other_rental  
  - salary
  - other_income

expenses:
  property:
    - maintenance
    - cleaning
    - utilities
    - condominium_fee
    - property_tax (IPTU)
  personal:
    - groceries
    - transport
    - health
    - education
    - other_personal
  financial:
    - bank_fees
    - transfers
    - other_financial
```

Categories are configurable in `categories.yaml` — not hardcoded.

---

## Future Scope (Phase 2+)

**Household cost management:**
- Monthly family budget tracking
- Spending patterns and alerts
- Cost control dashboards

**RVS Associates:**
- Separate entity, separate tools
- Not in scope until entity is active

---

## Privacy Rules

| Content | Treatment |
|---------|-----------|
| Bank statements (raw) | Gitignored always |
| Generated reports | Gitignored always |
| Any account numbers | Never in code or config |
| Tools and parsers | Private repo |
| Category config | Private repo |

**This domain never goes public.**

---

## Build Sequence

### Phase 1 — Foundation (start here)
1. Robert exports Itaú statements (OFX preferred, CSV fallback)
2. Robert downloads Airbnb earnings CSV for 2025
3. Build `itau_parser.py` — parse transactions, assign dates and amounts
4. Build `airbnb_parser.py` — parse earnings by month
5. Build `categorizer.py` — assign categories (manual rules first, AI-assist later)
6. Build `report.py` — generate monthly income statement
7. Validate against known totals

### Phase 2 — US Tax Integration
1. Build `fx_converter.py` — BRL/USD at monthly IRS rates
2. Add USD equivalent to all outputs
3. Generate annual summary for 1040 prep
4. FBAR threshold monitor

### Phase 3 — Automation
1. Scheduled monthly pull (when Itaú API available)
2. Automated categorization with AI review
3. Anomaly detection (unusual transactions)

---

## Key Blockers

1. **Itaú export format:** Robert to check OFX vs CSV availability in Itaú app
2. **Airbnb CSV:** Robert to download 2025 earnings report from Airbnb dashboard
3. **Tax strategy:** Confirm Form 2555 vs Form 1116 approach for US filing

---

## Notes

- Start with 2025 data only. Do not retroactively parse 2024 until 2025 is working.
- Manual category review is acceptable for Year 1. Automate in Year 2.
- Keep it simple: the goal is a clean income statement for tax prep, not a full accounting system.
