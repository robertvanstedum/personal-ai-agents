(async () => {
  const sheet = document.getElementById("statementSheet");
  const params = new URLSearchParams(location.search);
  const accountId = params.get("account");
  const cycle = params.get("cycle");
  const money = (value) => new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(Number(value || 0));
  const mdn = (value) => {
    const digits = String(value || "").replace(/\D/g, "");
    const local = digits.length === 11 && digits.startsWith("1") ? digits.slice(1) : digits;
    return local.length === 10 ? `${local.slice(0,3)}-${local.slice(3,6)}-${local.slice(6)}` : value || "—";
  };
  try {
    if (!accountId || !cycle) throw new Error("Account and billing cycle are required");
    const statement = await apiRequest(`/artifacts/accounts/${encodeURIComponent(accountId)}/legacy-statement/${encodeURIComponent(cycle)}`);
    document.title = `${statement.statement_number} · ${statement.account_name}`;
    sheet.innerHTML = `
      <section class="statement-head"><div class="brand"><h1>Legacy Billing</h1><p>Enterprise IoT service statement · simulated artifact</p></div><div class="invoice-title"><h2>${escapeHtml(statement.statement_number)}</h2><p>${escapeHtml(statement.status)}</p></div></section>
      <section class="facts"><div class="fact"><span>Billing period</span><strong>${escapeHtml(statement.billing_period_start)}–${escapeHtml(statement.billing_period_end)}</strong></div><div class="fact"><span>Statement date</span><strong>${escapeHtml(statement.statement_date)}</strong></div><div class="fact"><span>Due date</span><strong>${escapeHtml(statement.due_date)}</strong></div><div class="fact"><span>Posting mode</span><strong>${escapeHtml(statement.billing_mode)}</strong></div></section>
      <section class="addresses"><div><h3>Customer account</h3><p><b>${escapeHtml(statement.account_name)}</b></p><p>IoT Connect account ${escapeHtml(statement.account_number)}</p><p>Contract ${escapeHtml(statement.contract_number)}</p></div><div><h3>External billing account</h3><p><b>${escapeHtml(statement.legacy_account_ref)}</b></p><p>Invoice, tax, AR, collections and SAP interface</p></div></section>
      <section class="summary"><div class="boundary">IoT Connect supplied reconciled charges. Legacy Billing formatted this illustrative customer statement and remains responsible for taxation, payment terms, accounts receivable and collections.</div><div class="amount"><span>Amount due</span><strong>${money(statement.amount_due)}</strong></div></section>
      <table><thead><tr><th>Scope</th><th>MDN</th><th>Charge code</th><th>Description</th><th class="num">Qty</th><th class="num">Unit price</th><th class="num">Amount</th></tr></thead><tbody>${statement.line_items.map((row) => `<tr><td>${escapeHtml(row.posting_scope)}</td><td>${escapeHtml(mdn(row.mdn))}</td><td>${escapeHtml(row.charge_code)}</td><td>${escapeHtml(row.description)}</td><td class="num">${escapeHtml(row.quantity)}</td><td class="num">${money(row.unit_price)}</td><td class="num">${money(row.amount)}</td></tr>`).join("")}<tr class="total"><td colspan="6">Total current charges</td><td class="num">${money(statement.current_charges)}</td></tr></tbody></table>
      <div class="foot">${escapeHtml(statement.artifact_disclaimer)} Source charges: ${escapeHtml(statement.source_charge_count)} · Statement rows: ${escapeHtml(statement.statement_charge_item_count)} · Service lines posted: ${escapeHtml(statement.legacy_service_line_count)}.</div>`;
  } catch (error) {
    sheet.innerHTML = `<div class="error"><h2>Statement unavailable</h2><p>${escapeHtml(error.message)}</p><p>Return to the account Billing page and choose a completed cycle.</p></div>`;
  }
})();
