# itau_tax — Brazilian Bank Statement → Excel Tax Report

Converts an Itaú Conta Corrente export (semicolon-delimited .txt) into a
4-tab Excel workbook organized for both Brazilian IR and U.S. Schedule E filing.

## Usage

```bash
python itau_tax.py <input.txt> [output.xlsx] [config.json]
```

**Examples:**
```bash
# Minimal — uses config.json in same directory, output named automatically
python itau_tax.py Extrato_2025.txt

# Explicit output name
python itau_tax.py Extrato_2025.txt Itau_2025_Taxes.xlsx

# Different config (e.g. for a second property)
python itau_tax.py Extrato_2025.txt report.xlsx config_ipanema.json
```

## Input Format

Itaú export file, semicolon-delimited, one transaction per line:
```
DD/MM/YYYY;DESCRIPTION;AMOUNT
03/01/2025;PIX TRANSF  BANCO I03/01;4459,77
06/01/2025;DA  LIGHT 010145473501;-109,52
```
- Positive amounts = receipts (receitas)
- Negative amounts = expenses (despesas)
- Lines from years other than `tax_year` in config.json are skipped automatically

## Output Tabs

| Tab | Contents |
|-----|----------|
| **Resumo Mensal** | All categories × 12 months. IPTU/Condo highlighted as U.S. deductibles. |
| **Receitas por Mês** | Monthly income detail with subtotals. |
| **US Tax Summary** | Annual totals by category for Schedule E / Form 1116. |
| **Transações** | Raw data with auto-filter. |

## Configuration (config.json)

| Key | Purpose |
|-----|---------|
| `tax_year` | Filter transactions to this year |
| `account_label` | Label shown in report headers |
| `usd_brl_annual_avg` | Annual average exchange rate (set each year for USD conversion) |
| `category_rules` | Keyword → category mapping. First match wins. Case-insensitive. |
| `category_order_expense` | Display order of expense categories |
| `category_order_income` | Display order of income categories |
| `us_tax_notes` | Notes column text in US Tax Summary tab |

### Adding new payees

Edit `category_rules` in `config.json`. Add keywords to the appropriate category:
```json
"Pagamentos Pessoas (Adm/Manutenção)": ["MARIA A", "TECER C", "EUGENIO", "NEW_NAME"]
```

### New year checklist

1. Update `tax_year` in `config.json`
2. Set `usd_brl_annual_avg` (IRS average rate, usually published Jan/Feb)
3. Review `category_rules` for any new payees that appeared in the new year
4. Run: `python itau_tax.py Extrato_2026.txt`

## Requirements

```bash
pip install openpyxl
```

## Property context (2025)

- Account: Itaú Conta Corrente, Rio de Janeiro
- Key deductibles: IPTU (real estate tax) + Condomínio (HOA fees)
- Capital expenses (Grupo Casas / Elizabe electrical install): depreciate, don't expense
- Isabella payments: family/personal — confirm treatment with tax advisor
