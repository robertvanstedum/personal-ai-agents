const proofState = { accounts: [], selectedId: null, lastRunId: null };
const byId = (id) => document.getElementById(id);

function currentAccount() {
  return proofState.accounts.find((row) => row.account_id === proofState.selectedId);
}

async function initializeProof() {
  try {
    const health = await apiRequest("/health");
    byId("backendBadge").textContent = `${health.data_backend} backend`;
    await loadProofAccounts();
  } catch (error) {
    showToast(`${error.code}: ${error.message}`, "error");
  }
}

async function loadProofAccounts(preferredId = proofState.selectedId) {
  proofState.accounts = await apiRequest("/accounts");
  if (!proofState.accounts.some((row) => row.account_id === preferredId)) {
    preferredId = proofState.accounts[0]?.account_id || null;
  }
  proofState.selectedId = preferredId;
  byId("accountCards").innerHTML = proofState.accounts
    .map(
      (account) => `<button class="account-card ${account.account_id === proofState.selectedId ? "selected" : ""}" data-account-id="${escapeHtml(account.account_id)}">
        <span><b>${escapeHtml(account.account_name)}</b><small>${escapeHtml(account.account_number)} · ${escapeHtml(account.contract_number)}</small></span>
        <em>${escapeHtml(account.billing_mode)}</em>
      </button>`,
    )
    .join("");
  document.querySelectorAll(".account-card").forEach((button) => {
    button.onclick = async () => {
      proofState.selectedId = button.dataset.accountId;
      proofState.lastRunId = null;
      byId("billingEvidence").classList.add("hidden");
      await loadProofAccounts(proofState.selectedId);
    };
  });
  await renderProofSummary();
}

async function renderProofSummary() {
  const account = currentAccount();
  if (!account) return;
  byId("proofAccount").textContent = account.account_number;
  byId("proofContract").textContent = account.contract_number;
  byId("proofMode").textContent = account.billing_mode;
  byId("proofLegacyAccount").textContent = account.legacy_account_ref;
  byId("iotCount").textContent = account.iot_subscription_count;
  byId("legacyCount").textContent = account.legacy_standard_line_count;
  byId("routingStatement").innerHTML = account.billing_mode === "SUMMARIZED"
    ? `<b>Controlled suppression active.</b> ${account.iot_subscription_count} IoT subscriptions remain authoritative; detailed legacy creation is suppressed and consolidated financial output posts to ${escapeHtml(account.legacy_account_ref)}.`
    : `<b>Detailed route active.</b> Each new IoT subscription is mirrored to a standard legacy line for downstream billing.`;
}

async function refreshProof() {
  await loadProofAccounts(proofState.selectedId);
  showToast("Account evidence refreshed", "success");
}

async function runBilling() {
  try {
    const run = await apiJson(
      `/admin/accounts/${proofState.selectedId}/bill-runs`,
      "POST",
      { bill_cycle: byId("billCycle").value },
      true,
    );
    proofState.lastRunId = run.bill_run_id;
    const [charges, rows, reconciliation] = await Promise.all([
      apiRequest(`/bill-runs/${run.bill_run_id}/charges`),
      apiRequest(`/bill-runs/${run.bill_run_id}/file`),
      apiRequest(`/bill-runs/${run.bill_run_id}/reconciliation`),
    ]);
    renderBilling(run, charges, rows, reconciliation);
    showToast("Billing proof completed", "success");
  } catch (error) {
    byId("runResult").innerHTML = `<div class="message error">${escapeHtml(error.code)}: ${escapeHtml(error.message)}<br><small>Request ${escapeHtml(error.requestId)}</small></div>`;
  }
}

function renderBilling(run, charges, rows, reconciliation) {
  byId("billingEvidence").classList.remove("hidden");
  byId("runResult").innerHTML = `<b>${escapeHtml(run.bill_run_number)}</b><br>${escapeHtml(run.status)} · ${escapeHtml(run.bill_run_id)}`;
  byId("billingMetrics").innerHTML = [
    ["Source charges", run.source_charge_count],
    ["Legacy rows", run.output_row_count],
    ["Source total", money(run.source_total)],
    ["Output total", money(run.output_total)],
    ["Variance", money(run.variance)],
  ].map(([label, value]) => `<div><span>${label}</span><b>${escapeHtml(value)}</b></div>`).join("");
  byId("chargeCount").textContent = `${charges.length} records`;
  byId("fileCount").textContent = `${rows.length} rows`;
  byId("chargeTable").innerHTML = renderTable(charges, [
    { key: "subscription_number", label: "Subscription" },
    { key: "rate_plan_id", label: "Plan ID" },
    { key: "charge_level", label: "Level" },
    { key: "description", label: "Description" },
    { key: "amount", label: "Amount", format: money, className: "number" },
  ]);
  byId("fileTable").innerHTML = renderTable(rows, [
    { key: "source_charge_level", label: "Source level" },
    { key: "posting_scope", label: "Posting scope" },
    { key: "mdn", label: "MDN" },
    { key: "charge_code", label: "Charge code" },
    { key: "rate_plan_id", label: "Plan ID" },
    { key: "description", label: "Description" },
    { key: "quantity", label: "Qty", className: "number" },
    { key: "source_record_count", label: "Sources", className: "number" },
    { key: "amount", label: "Amount", format: money, className: "number" },
  ]);
  const labels = {
    amounts_balance: "Amounts balance",
    all_sources_represented_once: "All sources represented",
    no_duplicate_source_representations: "No duplicate representation",
    all_posting_targets_valid: "Posting targets valid",
  };
  const checks = Object.entries(reconciliation.acceptance_checks)
    .map(([key, passed]) => `<div class="recon-check ${passed ? "pass" : "fail"}"><b>${passed ? "PASS" : "FAIL"}</b><span>${escapeHtml(labels[key] || key)}</span></div>`)
    .join("");
  byId("reconciliationPanel").innerHTML = `<div class="panel-head"><div><p class="eyebrow">Accounting and Revenue Assurance</p><h2>Reconciliation ${escapeHtml(reconciliation.status)}</h2></div><span class="pill ${reconciliation.status === "PASSED" ? "success" : "danger"}">Variance ${money(reconciliation.variance)}</span></div><div class="recon-grid">${checks}</div>`;
}

async function loadInvoice() {
  try {
    const invoice = await apiRequest(
      `/artifacts/accounts/${proofState.selectedId}/legacy-statement/${byId("billCycle").value}`,
    );
    byId("invoice").innerHTML = `<div class="invoice-head"><div><p>${escapeHtml(invoice.generated_by)}</p><h3>Statement ${escapeHtml(invoice.statement_number)}</h3><span>${escapeHtml(invoice.account_name)} · ${escapeHtml(invoice.account_number)} · ${escapeHtml(invoice.legacy_account_ref)}</span></div><div><small>Amount due ${escapeHtml(invoice.due_date)}</small><b>${money(invoice.amount_due)}</b><span>${escapeHtml(invoice.status)}</span></div></div><p class="artifact-note">${escapeHtml(invoice.artifact_disclaimer)}</p>${renderTable(invoice.line_items, [
      { key: "posting_scope", label: "Posting scope" },
      { key: "mdn", label: "MDN" },
      { key: "charge_code", label: "Charge code" },
      { key: "description", label: "Description" },
      { key: "rate_plan_id", label: "Plan ID" },
      { key: "quantity", label: "Quantity", className: "number" },
      { key: "unit_price", label: "Unit price", format: money, className: "number" },
      { key: "amount", label: "Amount", format: money, className: "number" },
    ])}`;
  } catch (error) {
    showToast(`${error.code}: ${error.message}`, "error");
  }
}

byId("refreshButton").onclick = refreshProof;
byId("runBillingButton").onclick = runBilling;
byId("loadInvoiceButton").onclick = loadInvoice;
initializeProof();
