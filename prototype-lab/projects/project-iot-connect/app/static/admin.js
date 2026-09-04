const adminState = {
  accounts: [],
  selectedId: null,
  plans: [],
  profiles: [],
  availableSims: [],
  accountSims: [],
  activationBatch: null,
};

const MAX_BATCH_SIMS = 50;
const byId = (id) => document.getElementById(id);

function domesticMdn(value) {
  const digits = String(value || "").replace(/\D/g, "");
  const local = digits.length === 11 && digits.startsWith("1") ? digits.slice(1) : digits;
  if (local.length !== 10) return value || "—";
  return `${local.slice(0, 3)}-${local.slice(3, 6)}-${local.slice(6)}`;
}

function selectedAccount() {
  return adminState.accounts.find((row) => row.account_id === adminState.selectedId);
}

function newBatchReference(accountNumber) {
  const now = new Date();
  const digits = (value, width = 2) => String(value).padStart(width, "0");
  const date = `${now.getFullYear()}${digits(now.getMonth() + 1)}${digits(now.getDate())}`;
  const time = `${digits(now.getHours())}${digits(now.getMinutes())}${digits(now.getSeconds())}`;
  return `${accountNumber}-BATCH-${date}-${time}-${digits(now.getMilliseconds(), 3)}`;
}

function optionRows(rows, valueKey, label) {
  if (!rows.length) return '<option value="">None available</option>';
  return rows
    .map((row) => `<option value="${escapeHtml(row[valueKey])}">${escapeHtml(label(row))}</option>`)
    .join("");
}

function resetActivationSession() {
  adminState.activationBatch = null;
  byId("activationStatus").textContent = "Not started";
  byId("activationStatus").className = "pill neutral";
  byId("submitActivationButton").disabled = true;
  byId("activationResult").textContent = "No activation batch created in this session.";
  byId("usePrivateApn").checked = false;
  byId("privateApnSelect").disabled = true;
}

function checkedValues(name) {
  return [...document.querySelectorAll(`input[name="${name}"]:checked`)].map(
    (input) => input.value,
  );
}

function updateSelectionState(name, selectAllId, counterId, buttonId) {
  const checkboxes = [...document.querySelectorAll(`input[name="${name}"]`)];
  const selected = checkboxes.filter((input) => input.checked).length;
  const selectAll = byId(selectAllId);
  selectAll.checked = checkboxes.length > 0 && selected === checkboxes.length;
  selectAll.indeterminate = selected > 0 && selected < checkboxes.length;
  byId(counterId).textContent = `${selected} selected · max ${MAX_BATCH_SIMS}`;
  const draftAlreadyExists =
    buttonId === "createActivationButton" && adminState.activationBatch?.status === "DRAFT";
  byId(buttonId).disabled = selected === 0 || draftAlreadyExists;
}

function renderSimChecklist({ rows, containerId, name, selectAllId, counterId, buttonId }) {
  const visible = rows.slice(0, MAX_BATCH_SIMS);
  byId(containerId).innerHTML = visible.length
    ? visible
        .map(
          (row) => `<label class="sim-check-row">
            <input type="checkbox" name="${name}" value="${escapeHtml(row.sim_resource_id)}">
            <b title="${escapeHtml(row.iccid)}">ICCID ${escapeHtml(row.iccid)}</b>
            <small title="${escapeHtml(row.imsi)}">IMSI ${escapeHtml(row.imsi)}</small>
          </label>`,
        )
        .join("")
    : '<div class="sim-list-empty">No eligible SIMs available.</div>';
  byId(selectAllId).checked = false;
  byId(selectAllId).indeterminate = false;
  byId(selectAllId).disabled = visible.length === 0;
  document.querySelectorAll(`input[name="${name}"]`).forEach((input) => {
    input.onchange = () => updateSelectionState(name, selectAllId, counterId, buttonId);
  });
  updateSelectionState(name, selectAllId, counterId, buttonId);
}

function selectAllVisible(name, checked, selectAllId, counterId, buttonId) {
  document.querySelectorAll(`input[name="${name}"]`).forEach((input) => {
    input.checked = checked;
  });
  updateSelectionState(name, selectAllId, counterId, buttonId);
}

async function initializeAdmin() {
  try {
    const [health, plans, profiles] = await Promise.all([
      apiRequest("/health"),
      apiRequest("/catalog/rate-plans"),
      apiRequest("/catalog/network-profiles"),
    ]);
    adminState.plans = plans.filter(
      (row) => row.product_offering_id === "OFFER-IOT-CONNECTIVITY" && row.status === "ACTIVE",
    );
    adminState.profiles = profiles.filter((row) => row.status === "ACTIVE");
    byId("backendBadge").textContent = `${health.data_backend} backend`;
    byId("catalogTable").innerHTML = renderTable(adminState.plans, [
      { key: "rate_plan_id", label: "Rate plan ID" },
      { key: "rate_plan_code", label: "Code" },
      { key: "name", label: "Commercial name" },
      { key: "monthly_price", label: "Monthly price", format: money, className: "number" },
      { key: "gl_code", label: "GL code" },
      { key: "status", label: "Status" },
    ]);
    renderActivationCatalogs();
    const requestedAccount = new URLSearchParams(location.search).get("account");
    await loadAccounts(requestedAccount);
  } catch (error) {
    showToast(`${error.code}: ${error.message}`, "error");
  }
}

function renderActivationCatalogs() {
  byId("activationRatePlan").innerHTML = optionRows(
    adminState.plans,
    "rate_plan_id",
    (row) => `${row.rate_plan_code} · ${row.name} · ${money(row.monthly_price)}/month`,
  );
  byId("networkProfile").innerHTML = optionRows(
    adminState.profiles,
    "technical_profile_id",
    (row) => `${row.name} · ${row.profile_code}`,
  );
}

async function loadAccounts(preferredId = adminState.selectedId) {
  adminState.accounts = await apiRequest("/accounts");
  if (!adminState.accounts.some((row) => row.account_id === preferredId)) {
    preferredId = adminState.accounts[0]?.account_id || null;
  }
  adminState.selectedId = preferredId;
  byId("accountCount").textContent = adminState.accounts.length;
  byId("accountList").innerHTML = adminState.accounts
    .map(
      (account) => `<button class="account-item ${account.account_id === adminState.selectedId ? "selected" : ""}" data-account-id="${escapeHtml(account.account_id)}">
        <span>
          <b>${escapeHtml(account.account_name)}</b>
          <small>${escapeHtml(account.account_number)} · ${escapeHtml(account.contract_number)}</small>
        </span>
        <em>${escapeHtml(account.billing_mode)}</em>
      </button>`,
    )
    .join("");
  document.querySelectorAll(".account-item").forEach((button) => {
    button.onclick = async () => {
      adminState.selectedId = button.dataset.accountId;
      const url = new URL(location.href);
      url.searchParams.set("account", adminState.selectedId);
      history.replaceState({}, "", url);
      byId("sourceOrderRef").value = "";
      resetActivationSession();
      await loadAccounts(adminState.selectedId);
    };
  });
  renderAccount();
  await Promise.all([loadInventory(), refreshData()]);
}

function renderAccount() {
  const account = selectedAccount();
  if (!account) return;
  byId("modePill").textContent = account.billing_mode;
  byId("modePill").className = `pill ${account.billing_mode === "SUMMARIZED" ? "success" : "neutral"}`;

  // Once summarized, the control stays visible but spent -- the viewer should see the
  // state and that it is no longer available, rather than the control disappearing.
  const summarized = account.billing_mode === "SUMMARIZED";
  byId("summarizedToggle").checked = summarized;
  byId("summarizedToggle").disabled = summarized;
  byId("modeWarning").classList.add("hidden");
  byId("modeLocked").classList.toggle("hidden", !summarized);
  byId("accountFacts").innerHTML = [
    ["Customer", `${account.customer_name} · ${account.customer_number}`],
    ["IoT Connect account", account.account_number],
    ["IoT Connect account UUID", account.account_id],
    ["Contract", account.contract_number],
    ["Contract UUID", account.contract_id],
    ["External Amdocs account", account.external_billing_account_number],
    ["Private APN", account.private_apn_name || "Not configured — public/default APN"],
  ]
    .map(([label, value]) => `<div><dt>${escapeHtml(label)}</dt><dd title="${escapeHtml(value)}">${escapeHtml(value)}</dd></div>`)
    .join("");
  const privateApnControl = byId("privateApnControl");
  if (account.private_apn_name) {
    privateApnControl.classList.remove("hidden");
    byId("privateApnSelect").innerHTML = `<option value="${escapeHtml(account.private_apn_name)}">${escapeHtml(account.private_apn_name)}</option>`;
    byId("privateApnHint").textContent = "Optional. Leave off to use the public/default APN.";
  } else {
    privateApnControl.classList.add("hidden");
    byId("usePrivateApn").checked = false;
    byId("privateApnSelect").innerHTML = "";
  }
  byId("privateApnSelect").disabled = !byId("usePrivateApn").checked;
  if (!byId("sourceOrderRef").value) {
    byId("sourceOrderRef").value = newBatchReference(account.account_number);
  }
}

async function createAccount() {
  try {
    const customerName = byId("customerName").value.trim();
    const accountName = byId("accountName").value.trim();
    const externalBillingAccount = byId("externalBillingAccount").value.trim();
    if (!customerName || !accountName || !externalBillingAccount) {
      throw Object.assign(
        new Error("Customer name, account name and external Amdocs billing account are required."),
        { code: "INPUT_REQUIRED" },
      );
    }
    const account = await apiJson(
      "/admin/accounts",
      "POST",
      {
        customer_name: customerName,
        account_name: accountName,
        external_billing_account_number: externalBillingAccount,
        external_customer_ref: byId("externalRef").value.trim() || null,
        reason: byId("createReason").value,
      },
      true,
    );
    byId("customerName").value = "";
    byId("accountName").value = "";
    byId("externalBillingAccount").value = "";
    byId("externalRef").value = "";
    byId("sourceOrderRef").value = newBatchReference(account.account_number);
    resetActivationSession();
    await loadAccounts(account.account_id);
    showToast(
      `Created ${account.customer_number}, ${account.account_number} and ${account.contract_number}`,
      "success",
    );
  } catch (error) {
    showToast(`${error.code}: ${error.message}`, "error");
  }
}

// Summarized billing is a one-way transition, so the toggle only arms the change --
// it never applies it directly. The warning must be acknowledged and a reason given
// before anything is committed. See app/services/demo.py set_billing_mode, which
// enforces the same rule for any caller.
function armModeChange() {
  byId("modeWarning").classList.toggle("hidden", !byId("summarizedToggle").checked);
}

function cancelModeChange() {
  byId("summarizedToggle").checked = false;
  byId("modeWarning").classList.add("hidden");
}

async function applyMode() {
  const account = selectedAccount();
  try {
    const updated = await apiJson(
      `/admin/accounts/${account.account_id}/billing-mode`,
      "POST",
      { billing_mode: "SUMMARIZED", reason: byId("modeReason").value },
      true,
    );
    await loadAccounts(updated.account_id);
    setConfigMessage(
      "Summarized billing enabled. Network-successful subscriptions will not be created in Amdocs, and charges post consolidated to the account. This cannot be reversed.",
      "success",
    );
  } catch (error) {
    setConfigMessage(`${error.code}: ${error.message}`, "error");
  }
}

function setConfigMessage(text, kind) {
  byId("configResult").textContent = text;
  byId("configResult").className = `message ${kind}`;
}

async function loadInventory() {
  if (!adminState.selectedId) return;
  const [available, accountSims] = await Promise.all([
    apiRequest("/inventory/sims/available"),
    apiRequest(`/accounts/${adminState.selectedId}/inventory/sims`),
  ]);
  adminState.availableSims = available;
  adminState.accountSims = accountSims;
  byId("availableSimCount").textContent = `${available.length} available`;
  const orderable = accountSims.filter((row) => row.resource_status === "ASSIGNED");
  renderSimChecklist({
    rows: available,
    containerId: "availableSimList",
    name: "availableSimSelection",
    selectAllId: "selectAllAvailableSims",
    counterId: "availableSelectionCount",
    buttonId: "assignSimButton",
  });
  renderSimChecklist({
    rows: orderable,
    containerId: "assignedSimList",
    name: "activationSimSelection",
    selectAllId: "selectAllAssignedSims",
    counterId: "activationSelectionCount",
    buttonId: "createActivationButton",
  });
}

async function assignSim() {
  const simResourceIds = checkedValues("availableSimSelection");
  if (!simResourceIds.length) return showToast("Select at least one available operator SIM.", "error");
  try {
    const assigned = await apiJson(
      `/admin/accounts/${adminState.selectedId}/sim-assignments`,
      "POST",
      { sim_resource_ids: simResourceIds },
      true,
    );
    const newlyAssigned = assigned.filter((row) => simResourceIds.includes(row.sim_resource_id));
    byId("assignmentResult").innerHTML = `<b>${newlyAssigned.length} SIM${newlyAssigned.length === 1 ? "" : "s"} assigned to ${escapeHtml(selectedAccount().account_number)}</b><br>${newlyAssigned
      .slice(0, 3)
      .map((row) => `ICCID ${escapeHtml(row.iccid)}`)
      .join("<br>")}${newlyAssigned.length > 3 ? `<br><small>+ ${newlyAssigned.length - 3} more SIMs</small>` : ""}`;
    await loadInventory();
    document.querySelectorAll('input[name="activationSimSelection"]').forEach((input) => {
      input.checked = simResourceIds.includes(input.value);
    });
    updateSelectionState(
      "activationSimSelection",
      "selectAllAssignedSims",
      "activationSelectionCount",
      "createActivationButton",
    );
    showToast(
      `${newlyAssigned.length} SIM${newlyAssigned.length === 1 ? "" : "s"} moved into the customer account`,
      "success",
    );
  } catch (error) {
    showToast(`${error.code}: ${error.message}`, "error");
  }
}

async function createActivation() {
  const batchReference = byId("sourceOrderRef").value.trim();
  const simResourceIds = checkedValues("activationSimSelection");
  if (!batchReference || !simResourceIds.length) {
    return showToast("A customer batch reference and at least one assigned SIM are required.", "error");
  }
  byId("createActivationButton").disabled = true;
  try {
    const referenceBase = batchReference.slice(0, 76);
    adminState.activationBatch = await apiJson(
      `/admin/accounts/${adminState.selectedId}/activation-batches`,
      "POST",
      {
        items: simResourceIds.map((simResourceId, index) => ({
            source_order_ref: `${referenceBase}-${String(index + 1).padStart(3, "0")}`,
            sim_resource_id: simResourceId,
            product_offering_id: "OFFER-IOT-CONNECTIVITY",
            price_plan_id: byId("activationRatePlan").value,
            technical_profile_id: byId("networkProfile").value,
            private_apn: byId("usePrivateApn").checked
              ? byId("privateApnSelect").value
              : null,
          })),
      },
      true,
    );
    renderActivationResult(adminState.activationBatch);
    byId("submitActivationButton").disabled = false;
    showToast(
      `Draft batch created; ${simResourceIds.length} SIM${simResourceIds.length === 1 ? "" : "s"} and MDNs reserved`,
      "success",
    );
  } catch (error) {
    byId("createActivationButton").disabled = false;
    showToast(`${error.code}: ${error.message}`, "error");
  }
}

async function submitActivation() {
  if (!adminState.activationBatch) return;
  byId("submitActivationButton").disabled = true;
  byId("activationStatus").textContent = "Submitting";
  try {
    adminState.activationBatch = await apiRequest(
      `/admin/activation-batches/${adminState.activationBatch.batch_id}:submit`,
      { method: "POST", admin: true },
    );
    renderActivationResult(adminState.activationBatch);
    byId("sourceOrderRef").value = newBatchReference(selectedAccount().account_number);
    await loadAccounts(adminState.selectedId);
    showToast(
      adminState.activationBatch.failure_count
        ? "Activation completed with a network failure"
        : "Network activation completed and account policy was applied",
      adminState.activationBatch.failure_count ? "error" : "success",
    );
  } catch (error) {
    byId("submitActivationButton").disabled = false;
    byId("activationStatus").textContent = "Submission error";
    byId("activationStatus").className = "pill danger";
    showToast(`${error.code}: ${error.message}`, "error");
  }
}

async function retryActivationItem(batchId, batchItemId) {
  byId("activationStatus").textContent = "Retrying one SIM";
  byId("activationStatus").className = "pill neutral";
  document.querySelectorAll(".retry-activation-item").forEach((button) => {
    button.disabled = true;
  });
  try {
    adminState.activationBatch = await apiRequest(
      `/admin/activation-batches/${batchId}/items/${batchItemId}:retry`,
      { method: "POST", admin: true },
    );
    renderActivationResult(adminState.activationBatch);
    await loadAccounts(adminState.selectedId);
    showToast("Failed SIM retried alone; successful batch items were not resubmitted", "success");
  } catch (error) {
    renderActivationResult(adminState.activationBatch);
    showToast(`${error.code}: ${error.message}`, "error");
  }
}

function renderActivationResult(batch) {
  const complete = batch.status === "COMPLETED" || batch.status === "COMPLETED_WITH_ERRORS";
  byId("activationStatus").textContent = batch.status;
  byId("activationStatus").className = `pill ${batch.failure_count ? "danger" : complete ? "success" : "neutral"}`;
  const itemEvidence = renderTable(
    batch.items,
    [
      { key: "source_order_ref", label: "Customer order" },
      { key: "sim", label: "ICCID", format: (value) => value.iccid },
      { key: "mdn", label: "Assigned MDN", format: (value) => domesticMdn(value.mdn) },
      { key: "private_apn", label: "APN", format: (value) => value || "Public/default" },
      { key: "network_status", label: "FlowOne" },
      { key: "legacy_status", label: "Amdocs" },
    ],
    "No batch items returned.",
  );
  let elementEvidence = "";
  if (batch.items.length === 1 && complete) {
    elementEvidence = renderFlowOneEvidence(
      batch.items[0].flowone_element_results || [],
    );
  } else if (batch.items.length > 1 && complete) {
    elementEvidence = '<p class="microcopy">Per-element FlowOne evidence is retained for every batch item and remains available through the API and SQL evidence queries.</p>';
  }
  const retryActions = complete
    ? batch.items
        .filter((item) => item.overall_status === "FAILED")
        .map(
          (item) => `<div class="retry-row">
            <span><b>${escapeHtml(item.source_order_ref)}</b> · ICCID ${escapeHtml(item.sim.iccid)} · ${escapeHtml(item.message)}</span>
            <button class="button secondary retry-activation-item" data-batch-id="${escapeHtml(batch.batch_id)}" data-batch-item-id="${escapeHtml(item.batch_item_id)}">Retry this SIM only</button>
          </div>`,
        )
        .join("")
    : "";
  byId("activationResult").innerHTML = `
    <div class="activation-summary">
      <span><small>Batch</small><b>${escapeHtml(batch.batch_number)}</b></span>
      <span><small>Items</small><b>${escapeHtml(batch.item_count)}</b></span>
      <span><small>Succeeded</small><b>${escapeHtml(batch.success_count)}</b></span>
      <span><small>Failed</small><b>${escapeHtml(batch.failure_count)}</b></span>
      <span><small>Status</small><b>${escapeHtml(batch.status)}</b></span>
      <span><small>Batch UUID</small><b>${escapeHtml(batch.batch_id)}</b></span>
    </div>
    <p class="sequence-note"><b>Execution rule:</b> FlowOne network activation runs first. Amdocs is eligible only after network success and only when the account policy requires it.</p>
    ${!complete ? '<div class="sequence-note">Draft only: no network call has been made and nothing has been sent to Amdocs.</div>' : ""}
    ${itemEvidence}
    ${retryActions ? `<div class="retry-list"><h3>Failed items ready for controlled retry</h3>${retryActions}</div>` : ""}
    ${elementEvidence}
    <p class="microcopy">One rate plan and one network profile were applied across this controlled batch.</p>`;
  document.querySelectorAll(".retry-activation-item").forEach((button) => {
    button.onclick = () => retryActivationItem(button.dataset.batchId, button.dataset.batchItemId);
  });
}

function renderFlowOneEvidence(rows) {
  if (!rows.length) return "<p>No FlowOne element evidence returned.</p>";
  const labels = {
    HSS: "Subscriber Repository (HSS/HLR)",
    POLICY: "Policy (SPR/UDR)",
    SMSC: "SMSC — legacy",
    AAA: "AAA / RADIUS — private APN",
  };
  return `<div class="table-wrap"><table><thead><tr>
    <th>Network element</th><th>Result</th><th>Code</th><th>Rollback</th>
  </tr></thead><tbody>${rows.map((row) => {
    const skipped = row.provisioning_status === "SKIPPED_NOT_APPLICABLE";
    const result = skipped ? "Skipped — not applicable" : row.provisioning_status;
    return `<tr class="${skipped ? "flowone-skipped" : ""}">
      <td>${escapeHtml(labels[row.element] || row.element)}</td>
      <td>${escapeHtml(result)}</td>
      <td>${escapeHtml(row.element_code)}</td>
      <td>${escapeHtml(row.rollback_status)}</td>
    </tr>`;
  }).join("")}</tbody></table></div>`;
}

async function refreshData() {
  if (!adminState.selectedId) return;
  try {
    const [iot, legacy] = await Promise.all([
      apiRequest(`/accounts/${adminState.selectedId}/subscriptions?system=iot`),
      apiRequest(`/accounts/${adminState.selectedId}/subscriptions?system=legacy`),
    ]);
    byId("iotTable").innerHTML = renderTable(iot, [
      { key: "subscription_number", label: "Subscription" },
      { key: "source_subscription_ref", label: "Customer order ref" },
      { key: "price_plan_id", label: "Rate plan" },
      { key: "status", label: "Status" },
      { key: "subscription_id", label: "Internal UUID" },
    ]);
    byId("legacyTable").innerHTML = renderTable(legacy, [
      { key: "legacy_line_ref", label: "Legacy line" },
      { key: "mdn", label: "MDN" },
      { key: "line_type", label: "Type" },
      { key: "source_subscription_id", label: "Source subscription UUID" },
      { key: "status", label: "Status" },
    ]);
  } catch (error) {
    showToast(`${error.code}: ${error.message}`, "error");
  }
}

async function resetDemo() {
  if (!window.confirm("Reset all IoT Connect v1.0 demo data to the two prepared accounts?")) return;
  try {
    await apiRequest("/admin/demo:reset", { method: "POST", admin: true });
    adminState.selectedId = null;
    byId("sourceOrderRef").value = "";
    byId("assignmentResult").textContent = "No SIMs assigned in this session.";
    resetActivationSession();
    await loadAccounts();
    showToast("Prepared demo restored", "success");
  } catch (error) {
    showToast(`${error.code}: ${error.message}`, "error");
  }
}

byId("createAccountButton").onclick = createAccount;
byId("summarizedToggle").onchange = armModeChange;
byId("cancelModeButton").onclick = cancelModeChange;
byId("applyModeButton").onclick = applyMode;
byId("assignSimButton").onclick = assignSim;
byId("selectAllAvailableSims").onchange = (event) =>
  selectAllVisible(
    "availableSimSelection",
    event.target.checked,
    "selectAllAvailableSims",
    "availableSelectionCount",
    "assignSimButton",
  );
byId("selectAllAssignedSims").onchange = (event) =>
  selectAllVisible(
    "activationSimSelection",
    event.target.checked,
    "selectAllAssignedSims",
    "activationSelectionCount",
    "createActivationButton",
  );
byId("createActivationButton").onclick = createActivation;
byId("usePrivateApn").onchange = () => {
  byId("privateApnSelect").disabled = !byId("usePrivateApn").checked;
};
byId("submitActivationButton").onclick = submitActivation;
byId("refreshDataButton").onclick = refreshData;
byId("resetButton").onclick = resetDemo;

initializeAdmin();
