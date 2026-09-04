const comparison = { accounts: [], cycle: "2026-08" };
const byId = (id) => document.getElementById(id);

function displayDate(value) {
  if (!value) return "—";
  return new Date(`${value}T12:00:00`).toLocaleDateString("en-US", {
    month: "short", day: "numeric", year: "numeric",
  });
}

function groupByServiceLine(items) {
  return items.reduce((groups, row) => {
    const key = row.posting_scope === "SUBSCRIPTION"
      ? `MDN ${row.mdn}`
      : `Account ${row.legacy_account_ref}`;
    (groups[key] ||= []).push(row);
    return groups;
  }, {});
}

function renderStatement(statement, variant) {
  const groups = groupByServiceLine(statement.line_items);
  const serviceLines = Object.entries(groups)
    .map(([lineRef, rows], index) => `<section class="service-line">
      <div class="service-line-head"><span><b>${rows[0].posting_scope === "ACCOUNT" ? "Account charges" : `Mobile service ${index + 1}`}</b><small>${escapeHtml(lineRef)}</small></span><strong>${money(rows.reduce((sum, row) => sum + Number(row.amount), 0))}</strong></div>
      ${renderTable(rows, [
        { key: "description", label: "Monthly service and other charges" },
        { key: "rate_plan_id", label: "Plan" },
        { key: "quantity", label: "Qty", className: "number" },
        { key: "amount", label: "Amount", className: "number", format: money },
      ])}
    </section>`)
    .join("");

  return `<article class="telecom-statement ${variant}">
    <header class="statement-masthead"><div><span class="statement-logo">LM</span><span><b>Legacy Mobile</b><small>Enterprise IoT Services</small></span></div><div><small>Customer statement</small><b>${escapeHtml(statement.statement_number)}</b></div></header>
    <section class="statement-identity"><div><small>Bill to</small><b>${escapeHtml(statement.account_name)}</b><span>${escapeHtml(statement.account_number)} · ${escapeHtml(statement.legacy_account_ref)}</span></div><div><small>Billing period</small><b>${displayDate(statement.billing_period_start)} – ${displayDate(statement.billing_period_end)}</b><span>Statement date ${displayDate(statement.statement_date)}</span></div></section>
    <section class="amount-due"><div><small>Amount due</small><b>${money(statement.amount_due)}</b></div><div><small>Due date</small><b>${displayDate(statement.due_date)}</b><span>${escapeHtml(statement.status)}</span></div></section>
    <section class="statement-summary"><h3>Account summary</h3><dl><div><dt>Previous balance</dt><dd>${money(statement.previous_balance)}</dd></div><div><dt>Payments received</dt><dd>− ${money(statement.payments_received)}</dd></div><div><dt>Adjustments</dt><dd>${money(statement.adjustments)}</dd></div><div class="current"><dt>Current charges</dt><dd>${money(statement.current_charges)}</dd></div></dl></section>
    <section class="service-overview"><div><small>Legacy service lines billed</small><b>${statement.legacy_service_line_count}</b></div><div><small>IoT source charges represented</small><b>${statement.source_charge_count}</b></div><div><small>Statement charge items</small><b>${statement.statement_charge_item_count}</b></div></section>
    <section class="charges-section"><div class="charges-title"><span><small>${variant === "summarized" ? "IoT Connect operating model" : "Existing operating model"}</small><h3>${variant === "summarized" ? "Consolidated account posting" : "Subscription and account postings"}</h3></span><span class="mode-stamp">${escapeHtml(statement.billing_mode)}</span></div>${serviceLines}</section>
    <footer class="statement-footer"><b>Questions about your bill?</b><span>Legacy Billing Customer Care · AR and payment processing remain in Legacy Billing</span><small>${escapeHtml(statement.artifact_disclaimer)}</small></footer>
  </article>`;
}

async function generateComparison() {
  comparison.cycle = byId("comparisonCycle").value;
  try {
    comparison.accounts = await apiRequest("/accounts");
    const aster = comparison.accounts.find((row) => row.account_number === "ACCT-000100");
    const boreal = comparison.accounts.find((row) => row.account_number === "ACCT-000200");
    if (!aster || !boreal) throw new Error("Prepared Aster and Boreal accounts are required");
    const [detailed, summarized] = await Promise.all([
      apiRequest(`/artifacts/accounts/${aster.account_id}/legacy-statement/${comparison.cycle}`),
      apiRequest(`/artifacts/accounts/${boreal.account_id}/legacy-statement/${comparison.cycle}`),
    ]);
    byId("detailedStatement").innerHTML = renderStatement(detailed, "detailed");
    byId("summarizedStatement").innerHTML = renderStatement(summarized, "summarized");
    byId("comparisonSummary").classList.remove("hidden");
    byId("comparisonSummary").innerHTML = `<div><span>Detailed model</span><b>${detailed.legacy_service_line_count} subscription MDNs billed</b><small>${detailed.source_charge_count} source charges · ${money(detailed.amount_due)}</small></div><i>→</i><div><span>IoT Connect model</span><b>0 subscription MDNs billed</b><small>${summarized.source_charge_count} source charges posted to the account · ${money(summarized.amount_due)}</small></div><strong>100% reconciled</strong>`;
    showToast("Legacy statement comparison generated", "success");
  } catch (error) {
    const message = `${error.code || "NOT_READY"}: ${error.message}`;
    byId("detailedStatement").innerHTML = `<div class="empty-state">${escapeHtml(message)}<br>Complete activation and billing for Aster and Boreal first.</div>`;
    byId("summarizedStatement").innerHTML = `<div class="empty-state">The comparison is generated only after both bill runs exist.</div>`;
    byId("comparisonSummary").classList.add("hidden");
    showToast(message, "error");
  }
}

byId("generateButton").onclick = generateComparison;
byId("printButton").onclick = () => window.print();
generateComparison();
