(() => {
  "use strict";

  const page = document.body.dataset.iotconnectPage;
  const state = {
    account: null,
    summary: null,
    resources: [],
    filteredResources: [],
    selectedResourceIds: new Set(),
    sortColumn: 0,
    sortDirection: "asc",
  };

  const html = (value) => escapeHtml(value ?? "—");
  const count = (value) => Number(value || 0).toLocaleString("en-US");
  const currency = (value) =>
    new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(
      Number(value || 0),
    );

  function domesticMdn(value) {
    const digits = String(value || "").replace(/\D/g, "");
    const local = digits.length === 11 && digits.startsWith("1") ? digits.slice(1) : digits;
    if (local.length !== 10) return value || "—";
    return `${local.slice(0, 3)}-${local.slice(3, 6)}-${local.slice(6)}`;
  }

  function shortDate(value) {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.valueOf())) return value;
    return new Intl.DateTimeFormat("en-US", {
      month: "short",
      day: "numeric",
      year: "numeric",
    }).format(date);
  }

  function monthLabel(value) {
    if (!/^\d{4}-\d{2}$/.test(value || "")) return value || "—";
    return new Intl.DateTimeFormat("en-US", { month: "short", year: "numeric" }).format(
      new Date(`${value}-02T12:00:00`),
    );
  }

  function toast(message, kind = "info") {
    let element = document.getElementById("iotconnectToast");
    if (!element) {
      element = document.createElement("div");
      element.id = "iotconnectToast";
      element.className = "iotconnect-toast";
      document.body.appendChild(element);
    }
    element.textContent = message;
    element.dataset.kind = kind;
    element.classList.add("show");
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => element.classList.remove("show"), 5000);
  }

  function showFailure(error) {
    toast(`${error.code || "ERROR"}: ${error.message}`, "error");
  }

  async function resolveAccount() {
    const accounts = await apiRequest("/accounts");
    const requested = new URLSearchParams(location.search).get("account");
    if (requested) {
      state.account = accounts.find(
        (row) => row.account_id === requested || row.account_number === requested,
      );
      if (!state.account) {
        const error = new Error(`Account ${requested} is not available`);
        error.code = "ACCOUNT_NOT_FOUND";
        throw error;
      }
    } else {
      state.account =
        accounts.find((row) => row.account_number === "ACCT-000200") || accounts[0];
    }
    if (!state.account) throw new Error("No account is available in the POC data store");
    return state.account;
  }

  function accountHref(path) {
    return appPath(`${path}?account=${encodeURIComponent(state.account.account_id)}`);
  }

  function applyAccountContext() {
    const account = state.account;
    document.title = `IoT Connect · ${account.account_name}`;
    document.querySelectorAll(".identity .name").forEach((node) => {
      if (page.startsWith("portal-")) node.textContent = account.account_name;
    });
    document.querySelectorAll(".account-context strong").forEach(
      (node) => (node.textContent = account.account_name),
    );
    document.querySelectorAll(".account-context > span:last-child").forEach(
      (node) => (node.textContent = account.account_number),
    );
    document.querySelectorAll(".customer-tab, .account-tab").forEach((link) => {
      if (link.getAttribute("href")?.startsWith(appPath("/portal")) || link.getAttribute("href")?.startsWith(appPath("/operator"))) {
        link.href = accountHref(link.getAttribute("href"));
      }
    });
  }

  function convertBillingChromeForOperator() {
    if (location.pathname !== appPath("/operator/billing")) return;
    document.querySelector(".brand small").textContent = "Enterprise IoT Operations";
    const identity = document.querySelector(".identity");
    identity.innerHTML = '<span class="name">Business Operations</span><span class="chip role">Operator</span><span class="chip">POC environment</span>';
    const customerNav = document.querySelector(".customer-nav");
    customerNav.className = "operator-nav";
    customerNav.setAttribute("aria-label", "Operator work areas");
    customerNav.innerHTML = `<a class="operator-tab" href="${appPath("/operator")}">Portfolio</a><a class="operator-tab" href="${appPath("/operator")}">Accounts</a><a class="operator-tab" href="${appPath("/operator/inventory")}">SIM Inventory</a><a class="operator-tab current" href="${appPath("/operator/bill-cycles")}">Bill Cycles</a><a class="operator-tab" href="${appPath("/operator/catalog")}">Plan Catalog</a><a class="operator-tab" href="${appPath("/operator/api-activity")}">API Activity</a>`;
    const main = document.querySelector("main");
    const accountBar = document.createElement("nav");
    accountBar.className = "account-bar";
    accountBar.setAttribute("aria-label", "Selected account sections");
    accountBar.innerHTML = `<div class="account-context"><a href="${appPath("/operator")}">Portfolio</a><span>›</span><strong>Boreal Equipment Group</strong><span>ACCT-000200</span></div><div class="account-tabs"><a class="account-tab" href="${appPath("/operator/account")}">Overview</a><a class="account-tab" href="${appPath("/operator/subscriptions")}">Subscriptions &amp; SIMs</a><a class="account-tab current" href="${appPath("/operator/billing")}">Billing</a><a class="account-tab" href="${appPath("/operator/account/configuration")}">Account Configuration</a></div>`;
    main.before(accountBar);
  }

  async function loadAccountSummary() {
    await resolveAccount();
    state.summary = await apiRequest(`/accounts/${state.account.account_id}/summary`);
    applyAccountContext();
    return state.summary;
  }

  function updateMetric(label, value, detail) {
    document.querySelectorAll(".metric").forEach((card) => {
      if (card.querySelector(".label")?.textContent.trim() !== label) return;
      card.querySelector("strong").textContent = value;
      if (detail && card.querySelector("small")) card.querySelector("small").textContent = detail;
    });
  }

  function renderPlanMix(summary) {
    const panel = [...document.querySelectorAll(".panel")].find((node) =>
      node.querySelector("h2")?.textContent.includes("rate plan"),
    );
    if (!panel) return;
    const body = panel.querySelector("tbody");
    const total = summary.active_subscriptions;
    const catalog = window.__iotconnectCatalog || [];
    const rows = Object.entries(summary.rate_plan_counts || {});
    panel.querySelector(".panel-head span").textContent = `${count(total)} active subscriptions`;
    body.innerHTML = rows.length
      ? rows
          .map(([planId, quantity]) => {
            const plan = catalog.find((row) => row.rate_plan_id === planId);
            const share = total ? (Number(quantity) / total) * 100 : 0;
            return `<tr><td>${html(plan?.name || planId)}<span class="subtext">${html(planId)}</span></td><td class="num">${count(quantity)}</td><td><span class="plan-share">${share.toFixed(1)}%</span><div class="share-track"><div class="share-fill" style="width:${share}%"></div></div></td></tr>`;
          })
          .join("")
      : '<tr><td colspan="3" class="empty-state">No active subscriptions yet.</td></tr>';
    const totalCell = panel.querySelector("tfoot .num");
    if (totalCell) totalCell.textContent = count(total);
  }

  async function initializeAccountOverview() {
    const [summary, catalog] = await Promise.all([
      loadAccountSummary(),
      apiRequest("/catalog/rate-plans"),
    ]);
    window.__iotconnectCatalog = catalog;
    const { account } = summary;
    const heading = document.querySelector(".page-head h1");
    if (heading) heading.textContent = account.account_name;
    const subheading = document.querySelector(".page-head p");
    if (subheading) subheading.textContent = `${account.account_number} · live POC account data`;
    updateMetric("Available SIMs", count(summary.available_sims));
    updateMetric("Active subscriptions", count(summary.active_subscriptions));
    updateMetric("Suspended", count(summary.suspended_subscriptions));
    updateMetric(
      "Current cycle total",
      summary.latest_bill_run ? currency(summary.latest_bill_run.output_total) : "—",
      summary.latest_bill_run
        ? `${monthLabel(summary.latest_bill_run.bill_cycle)} reconciled output`
        : "no completed bill run",
    );
    renderPlanMix(summary);

    if (page === "operator-account") {
      const config = document.querySelectorAll(".config-item");
      if (config[0]) config[0].querySelector("strong").textContent = account.external_billing_account_number;
      if (config[1]) config[1].querySelector("strong").textContent = account.billing_mode;
      if (config[2]) {
        config[2].querySelector("strong").textContent = account.private_apn_name ? "Private APN" : "Public APN";
        config[2].querySelector("small").textContent = account.private_apn_name || "account default";
      }
      if (config[3]) config[3].querySelector("strong").textContent = `${count(summary.available_sims)} SIMs`;
    }
  }

  function makeApiReferenceDiscreet() {
    if (page !== "operator-api-activity") return;
    const link = document.querySelector(".page-head a[data-api-reference]");
    if (!link) return;
    link.className = "evidence-link";
    link.textContent = "IoT Connect API (8095)";
  }

  async function initializeAccountConfiguration() {
    const account = await resolveAccount();
    applyAccountContext();
    document.getElementById("configurationAccountName").textContent = account.account_name;
    document.getElementById("configurationAccountNumber").textContent = account.account_number;
    document.getElementById("externalBillingAccount").textContent = account.external_billing_account_number;
    document.getElementById("privateApnValue").textContent = account.private_apn_name || "Public/default APN";

    document.getElementById("editCustomerName").value = account.customer_name;
    document.getElementById("editAccountName").value = account.account_name;
    document.getElementById("editExternalCustomerRef").value = account.external_customer_ref || "";
    document.getElementById("editPrivateApn").value = account.private_apn_name || "";
    document.getElementById("accountEditForm").addEventListener("submit", async (event) => {
      event.preventDefault();
      const button = event.submitter;
      button.disabled = true;
      try {
        const updated = await apiJson(
          `/admin/accounts/${account.account_id}`,
          "PATCH",
          {
            customer_name: document.getElementById("editCustomerName").value.trim(),
            account_name: document.getElementById("editAccountName").value.trim(),
            external_customer_ref: document.getElementById("editExternalCustomerRef").value.trim() || null,
            private_apn_name: document.getElementById("editPrivateApn").value.trim() || null,
            reason: document.getElementById("editAccountReason").value.trim(),
          },
          true,
        );
        state.account = updated;
        document.getElementById("configurationAccountName").textContent = updated.account_name;
        document.getElementById("privateApnValue").textContent = updated.private_apn_name || "Public/default APN";
        applyAccountContext();
        toast(`${updated.account_number} account details saved.`);
      } catch (error) {
        showFailure(error);
      } finally {
        button.disabled = false;
      }
    });

    const mode = document.getElementById("configurationBillingMode");
    const toggle = document.getElementById("configurationSummarizedToggle");
    const warning = document.getElementById("configurationModeWarning");
    const locked = document.getElementById("configurationModeLocked");
    const apply = document.getElementById("configurationApplyMode");
    const reason = document.getElementById("configurationModeReason");
    const summarized = account.billing_mode === "SUMMARIZED";
    apply.textContent = `Enable summarized billing for ${account.account_number}`;
    mode.textContent = account.billing_mode;
    mode.className = `status ${summarized ? "active" : ""}`;
    toggle.checked = summarized;
    toggle.disabled = summarized;
    locked.hidden = !summarized;
    warning.hidden = true;

    toggle.addEventListener("change", () => {
      warning.hidden = !toggle.checked;
    });
    document.getElementById("configurationCancelMode").addEventListener("click", () => {
      toggle.checked = false;
      warning.hidden = true;
    });
    apply.addEventListener("click", async () => {
      if (!reason.value.trim()) {
        toast("A reason is required for this controlled setting.", "error");
        return;
      }
      if (!window.confirm(
        `Enable summarized billing for ${account.account_name} (${account.account_number})? This cannot be reversed without a migration.`,
      )) return;
      apply.disabled = true;
      try {
        const updated = await apiJson(
          `/admin/accounts/${account.account_id}/billing-mode`,
          "POST",
          { billing_mode: "SUMMARIZED", reason: reason.value.trim() },
          true,
        );
        state.account = updated;
        mode.textContent = updated.billing_mode;
        toggle.checked = true;
        toggle.disabled = true;
        warning.hidden = true;
        locked.hidden = false;
        toast(`${updated.account_number} now uses summarized billing.`);
      } catch (error) {
        showFailure(error);
      } finally {
        apply.disabled = false;
      }
    });
  }

  async function initializeAccountNew() {
    const legacyAccounts = await apiRequest("/admin/legacy-accounts/available", {
      admin: true,
    });
    const select = document.getElementById("newExternalBillingAccount");
    select.innerHTML = legacyAccounts.length
      ? '<option value="">Choose an available external account</option>' +
        legacyAccounts
          .map(
            (row) =>
              `<option value="${html(row.legacy_account_ref)}">${html(row.account_name)} · ${html(row.legacy_account_ref)}</option>`,
          )
          .join("")
      : '<option value="">No unlinked external billing accounts are available</option>';
    document.getElementById("accountCreateForm").addEventListener("submit", async (event) => {
      event.preventDefault();
      const button = event.submitter;
      button.disabled = true;
      try {
        const account = await apiJson(
          "/admin/accounts",
          "POST",
          {
            customer_name: document.getElementById("newCustomerName").value.trim(),
            account_name: document.getElementById("newAccountName").value.trim(),
            external_billing_account_number: select.value,
            external_customer_ref: document.getElementById("newExternalCustomerRef").value.trim() || null,
            reason: document.getElementById("newAccountReason").value.trim(),
          },
          true,
        );
        toast(`${account.account_number} created. Opening account configuration.`);
        location.href = appPath(`/operator/account/configuration?account=${encodeURIComponent(account.account_id)}`);
      } catch (error) {
        showFailure(error);
      } finally {
        button.disabled = false;
      }
    });
  }

  function resourceValues(row) {
    return [
      row.iccid,
      row.imsi,
      domesticMdn(row.mdn),
      row.rate_plan_name || row.price_plan_id,
      row.status,
      row.subscription_number,
      shortDate(row.last_change),
    ];
  }

  function renderResourceRows() {
    const body = document.getElementById("resourceRows");
    if (!body) return;
    const rows = state.filteredResources;
    body.innerHTML = rows.length
      ? rows
          .map((row) => {
            const selected = state.selectedResourceIds.has(row.sim_resource_id);
            const values = resourceValues(row);
            const statusClass = row.status.toLowerCase().replaceAll("_", "-");
            return `<tr data-resource-id="${html(row.sim_resource_id)}" class="${selected ? "selected" : ""}"><td class="row-select"><input type="checkbox" aria-label="Select SIM ${html(values[0])}" ${selected ? "checked" : ""}></td><td class="mono">${html(values[0])}</td><td class="mono">${html(values[1])}</td><td>${html(values[2])}</td><td>${html(values[3] || "Selected at activation")}</td><td><span class="status ${html(statusClass)}">${html(row.status)}</span></td><td class="mono">${html(values[5])}</td><td>${html(values[6])}</td></tr>`;
          })
          .join("")
      : '<tr><td colspan="8" class="empty-state">No resources match the current filters.</td></tr>';
    body.querySelectorAll("tr[data-resource-id]").forEach((row) => {
      row.addEventListener("click", () => {
        const id = row.dataset.resourceId;
        if (state.selectedResourceIds.has(id)) state.selectedResourceIds.delete(id);
        else {
          if (state.selectedResourceIds.size >= 50) {
            toast("Interactive SIM actions are limited to 50 resources per batch.", "error");
            return;
          }
          state.selectedResourceIds.add(id);
        }
        renderResourceRows();
        updateSelectionCount();
      });
    });
    document.getElementById("resourceMatchCount").textContent = `${count(rows.length)} matching`;
    document.getElementById("resourceLoadCount").textContent = `${count(rows.length)} of ${count(state.resources.length)} loaded`;
  }

  function updateSelectionCount() {
    const node = document.getElementById("resourceSelectionCount");
    if (node) node.textContent = `${count(state.selectedResourceIds.size)} selected`;
  }

  function wildcardMatch(value, pattern) {
    if (!pattern) return true;
    if (!pattern.includes("*")) return value.includes(pattern);
    const escaped = pattern
      .split("*")
      .map((part) => part.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))
      .join(".*");
    return new RegExp(escaped, "i").test(value);
  }

  function applyResourceFilters() {
    const global = document.getElementById("resourceSearch").value.trim().toLowerCase();
    const filters = [...document.querySelectorAll(".filters .filter-box")].map((node) =>
      node.value.toLowerCase(),
    );
    state.filteredResources = state.resources.filter((row) => {
      const values = resourceValues(row).map((value) => String(value || "").toLowerCase());
      if (global && !values.some((value) => wildcardMatch(value, global))) return false;
      return filters.every((filter, index) => {
        if (!filter || filter.startsWith("all ") || filter === "any date") return true;
        return wildcardMatch(values[index], filter);
      });
    });
    state.filteredResources.sort((left, right) => {
      const leftValue = String(resourceValues(left)[state.sortColumn] || "");
      const rightValue = String(resourceValues(right)[state.sortColumn] || "");
      const result = leftValue.localeCompare(rightValue, undefined, {
        numeric: true,
        sensitivity: "base",
      });
      return state.sortDirection === "asc" ? result : -result;
    });
    renderResourceRows();
  }

  function initializeResourceSorting() {
    document.querySelectorAll("th[data-sort]").forEach((heading) => {
      heading.addEventListener("click", () => {
        const column = Number(heading.dataset.sort);
        if (state.sortColumn === column) {
          state.sortDirection = state.sortDirection === "asc" ? "desc" : "asc";
        } else {
          state.sortColumn = column;
          state.sortDirection = "asc";
        }
        document.querySelectorAll("th[data-sort]").forEach((node) => {
          node.removeAttribute("aria-sort");
        });
        heading.setAttribute(
          "aria-sort",
          state.sortDirection === "asc" ? "ascending" : "descending",
        );
        applyResourceFilters();
      });
    });
    const defaultHeading = document.querySelector(`th[data-sort="${state.sortColumn}"]`);
    if (defaultHeading) defaultHeading.setAttribute("aria-sort", "ascending");
  }

  async function initializeResources() {
    const summary = await loadAccountSummary();
    state.resources = await apiRequest(`/accounts/${state.account.account_id}/resources`);
    state.filteredResources = [...state.resources];
    const heading = document.querySelector(".page-head p");
    if (heading) heading.textContent = `${state.account.account_name} · ${state.account.account_number}`;
    const operatorNote = document.getElementById("operatorResourceNote");
    if (operatorNote) {
      operatorNote.textContent = `The operator can activate, suspend, reactivate, retire, or change the rate plan for selected ${state.account.account_name} resources. The grid itself does not silently edit state; each change opens a controlled review and creates an auditable request.`;
    }
    const summaryNumbers = document.querySelectorAll(".summary-item strong");
    if (summaryNumbers[0]) summaryNumbers[0].textContent = count(summary.active_subscriptions);
    if (summaryNumbers[1]) summaryNumbers[1].textContent = count(summary.available_sims);
    if (summaryNumbers[2]) summaryNumbers[2].textContent = count(summary.suspended_subscriptions);
    if (summaryNumbers[3]) summaryNumbers[3].textContent = count(summary.total_resources);

    const planSelect = document.querySelector('select[aria-label="Filter rate plan"]');
    const statusSelect = document.querySelector('select[aria-label="Filter status"]');
    [...new Set(state.resources.map((row) => row.rate_plan_name).filter(Boolean))]
      .sort()
      .forEach((name) => planSelect.insertAdjacentHTML("beforeend", `<option>${html(name)}</option>`));
    [...new Set(state.resources.map((row) => row.status))]
      .sort()
      .forEach((name) => statusSelect.insertAdjacentHTML("beforeend", `<option>${html(name)}</option>`));

    document.getElementById("resourceSearch").addEventListener("input", applyResourceFilters);
    document.querySelectorAll(".filters .filter-box").forEach((node) => {
      node.addEventListener(node.tagName === "SELECT" ? "change" : "input", applyResourceFilters);
    });
    initializeResourceSorting();
    document.getElementById("selectFilteredButton")?.addEventListener("click", () => {
      const eligible = state.filteredResources.filter((row) => row.status === "AVAILABLE");
      state.selectedResourceIds = new Set(
        eligible.slice(0, 50).map((row) => row.sim_resource_id),
      );
      renderResourceRows();
      updateSelectionCount();
      toast(
        eligible.length > 50
          ? "Selected the first 50 available SIMs from the filtered results."
          : `Selected ${eligible.length} available SIMs from the filtered results.`,
      );
    });
    document.getElementById("manageSelectedButton").addEventListener("click", () => {
      const selected = state.resources.filter((row) => state.selectedResourceIds.has(row.sim_resource_id));
      if (!selected.length) return toast("Select at least one SIM or subscription first.", "error");
      if (selected.every((row) => row.status === "AVAILABLE")) {
        sessionStorage.setItem(
          `iotconnect:selected:${state.account.account_id}`,
          JSON.stringify(selected.map((row) => row.sim_resource_id)),
        );
        const target = appPath(page === "operator-subscriptions" ? "/operator/actions" : "/portal/actions");
        location.href = accountHref(target);
        return;
      }
      toast("Activation is executable in this POC. Suspend, reactivate, retire, and rate-plan change remain modeled next-step contracts.");
    });
    applyResourceFilters();
    updateSelectionCount();
  }

  function actionBatchReference(accountNumber) {
    const now = new Date();
    const date = now.toISOString().replace(/[-:TZ.]/g, "").slice(0, 14);
    return `${accountNumber}-ORDER-${date}`;
  }

  function convertActionsChromeForOperator() {
    if (location.pathname !== appPath("/operator/actions")) return;
    document.querySelector(".brand small").textContent = "Enterprise IoT Operations";
    document.querySelector(".identity").innerHTML = '<span class="name">Business Operations</span><span class="chip role">Operator</span><span class="chip">POC environment</span>';
    const nav = document.querySelector(".customer-nav");
    nav.className = "operator-nav";
    nav.innerHTML = `<a class="operator-tab" href="${appPath("/operator")}">Portfolio</a><a class="operator-tab" href="${appPath("/operator/accounts")}">Accounts</a><a class="operator-tab" href="${appPath("/operator/inventory")}">SIM Inventory</a><a class="operator-tab current" href="${appPath("/operator/actions")}">SIM Actions</a><a class="operator-tab" href="${appPath("/operator/bill-cycles")}">Bill Cycles</a><a class="operator-tab" href="${appPath("/operator/catalog")}">Plan Catalog</a>`;
  }

  function renderActivationEvidence(batch) {
    const evidence = document.getElementById("customerActionEvidence");
    evidence.hidden = false;
    evidence.innerHTML = `<div class="action-head"><h2>${html(batch.batch_number)} · per-SIM outcome</h2><span>${html(batch.status)}</span></div><table><thead><tr><th>Customer order</th><th>ICCID</th><th>Assigned MDN</th><th>FlowOne</th><th>Amdocs</th><th>Message</th></tr></thead><tbody>${batch.items.map((item) => `<tr><td class="mono">${html(item.source_order_ref)}</td><td class="mono">${html(item.sim.iccid)}</td><td>${html(domesticMdn(item.mdn.mdn))}</td><td><span class="status">${html(item.network_status)}</span></td><td><span class="status">${html(item.legacy_status)}</span></td><td>${html(item.message || "—")}</td></tr>`).join("")}</tbody></table>`;
  }

  async function renderActivationHistory(isOperator) {
    const path = isOperator
      ? `/admin/activation-batches?account_id=${encodeURIComponent(state.account.account_id)}`
      : `/accounts/${state.account.account_id}/activation-batches`;
    const batches = await apiRequest(path, isOperator
      ? { admin: true }
      : { customerAccountId: state.account.account_id });
    document.getElementById("activationBatchCount").textContent = `${count(batches.length)} batches`;
    document.getElementById("activationBatchRows").innerHTML = batches.length
      ? `<table><thead><tr><th>Batch</th><th>Status</th><th class="num">Items</th><th class="num">Succeeded</th><th class="num">Failed</th><th>Completed</th><th></th></tr></thead><tbody>${batches.map((batch) => `<tr><td class="mono">${html(batch.batch_number)}</td><td><span class="status">${html(batch.status)}</span></td><td class="num">${count(batch.item_count)}</td><td class="num">${count(batch.success_count)}</td><td class="num">${count(batch.failure_count)}</td><td>${html(shortDate(batch.completed_at || batch.created_at))}</td><td><button class="evidence-link view-activation-batch" data-batch-id="${html(batch.batch_id)}">View</button></td></tr>`).join("")}</tbody></table>`
      : '<div class="empty-state">No activation batches exist for this account.</div>';
    document.querySelectorAll(".view-activation-batch").forEach((button) => {
      button.addEventListener("click", async () => {
        try {
          const batch = await apiRequest(`/activation-batches/${button.dataset.batchId}`);
          renderActivationEvidence(batch);
          document.getElementById("customerActionEvidence").scrollIntoView({ behavior: "smooth" });
        } catch (error) {
          showFailure(error);
        }
      });
    });
  }

  async function initializeActions() {
    convertActionsChromeForOperator();
    await resolveAccount();
    applyAccountContext();
    const isOperator = location.pathname === appPath("/operator/actions");
    const [resources, plans, profiles] = await Promise.all([
      apiRequest(`/accounts/${state.account.account_id}/resources`),
      apiRequest("/catalog/rate-plans"),
      apiRequest("/catalog/network-profiles"),
    ]);
    const stored = JSON.parse(
      sessionStorage.getItem(`iotconnect:selected:${state.account.account_id}`) || "[]",
    );
    const chosen = resources.filter(
      (row) => stored.includes(row.sim_resource_id) && row.status === "AVAILABLE",
    );
    document.getElementById("actionAccountContext").textContent = `${state.account.account_name} · ${state.account.account_number}`;
    document.getElementById("selectedActionCount").textContent = `${count(chosen.length)} selected`;
    document.getElementById("backToResources").href = accountHref(
      isOperator ? "/operator/subscriptions" : "/portal/subscriptions",
    );
    document.getElementById("completionBackToResources").href = accountHref(
      isOperator ? "/operator/subscriptions" : "/portal/subscriptions",
    );
    document.getElementById("selectedActionRows").innerHTML = chosen.length
      ? `<table class="selected-table"><thead><tr><th>ICCID</th><th>IMSI</th><th>Status</th></tr></thead><tbody>${chosen.map((row) => `<tr><td class="mono">${html(row.iccid)}</td><td class="mono">${html(row.imsi)}</td><td><span class="status">${html(row.status)}</span></td></tr>`).join("")}</tbody></table>`
      : '<div class="empty-state">No available SIMs were carried from the resource list. Return to Subscriptions &amp; SIMs and select account inventory first.</div>';
    const activePlans = plans.filter((row) => row.status === "ACTIVE");
    document.getElementById("customerRatePlan").innerHTML = activePlans
      .map((row) => `<option value="${html(row.rate_plan_id)}">${html(row.name)} · ${currency(row.monthly_price)}</option>`)
      .join("");
    document.getElementById("customerNetworkProfile").innerHTML = profiles
      .map((row) => `<option value="${html(row.technical_profile_id)}">${html(row.name)}</option>`)
      .join("");
    document.getElementById("customerBatchReference").value = actionBatchReference(state.account.account_number);
    if (state.account.private_apn_name) {
      document.getElementById("customerPrivateApnRow").hidden = false;
      document.getElementById("customerPrivateApnName").textContent = state.account.private_apn_name;
    }
    const createButton = document.getElementById("createCustomerBatch");
    const submitButton = document.getElementById("submitCustomerBatch");
    if (!chosen.length) createButton.disabled = true;
    let batch = null;
    createButton.addEventListener("click", async () => {
      createButton.disabled = true;
      const reference = document.getElementById("customerBatchReference").value.trim();
      if (!reference) {
        createButton.disabled = false;
        return toast("Enter a customer batch reference.", "error");
      }
      const payload = {
        items: chosen.map((row, index) => ({
          source_order_ref: `${reference.slice(0, 76)}-${String(index + 1).padStart(3, "0")}`,
          sim_resource_id: row.sim_resource_id,
          product_offering_id: "OFFER-IOT-CONNECTIVITY",
          price_plan_id: document.getElementById("customerRatePlan").value,
          technical_profile_id: document.getElementById("customerNetworkProfile").value,
          private_apn: document.getElementById("customerPrivateApn").checked
            ? state.account.private_apn_name
            : null,
        })),
      };
      try {
        batch = isOperator
          ? await apiJson(`/admin/accounts/${state.account.account_id}/activation-batches`, "POST", payload, true)
          : await apiCustomerJson(`/accounts/${state.account.account_id}/activation-batches`, "POST", payload, state.account.account_id);
        document.getElementById("actionState").textContent = batch.status;
        document.getElementById("customerActionResult").className = "action-result success";
        document.getElementById("customerActionResult").textContent = `${batch.batch_number}: ${batch.item_count} SIMs and MDNs reserved. No network action has occurred yet.`;
        submitButton.disabled = false;
      } catch (error) {
        createButton.disabled = false;
        showFailure(error);
      }
    });
    submitButton.addEventListener("click", async () => {
      if (!batch) return;
      submitButton.disabled = true;
      try {
        batch = isOperator
          ? await apiRequest(`/admin/activation-batches/${batch.batch_id}:submit`, { method: "POST", admin: true })
          : await apiCustomerJson(`/accounts/${state.account.account_id}/activation-batches/${batch.batch_id}:submit`, "POST", undefined, state.account.account_id);
        document.getElementById("actionState").textContent = batch.status;
        document.getElementById("customerActionResult").className = `action-result ${batch.failure_count ? "" : "success"}`;
        document.getElementById("customerActionResult").textContent = `${batch.success_count} succeeded; ${batch.failure_count} failed. FlowOne completed before Amdocs eligibility was evaluated.`;
        renderActivationEvidence(batch);
        const completion = document.getElementById("actionCompletion");
        completion.hidden = false;
        document.getElementById("actionCompletionTitle").textContent = batch.failure_count
          ? "Activation completed with failures"
          : "Activation completed";
        document.getElementById("actionCompletionText").textContent =
          "Each SIM was submitted independently. Failed items remain visible in batch evidence and can be retried without resubmitting successful items.";
        document.getElementById("completionSucceeded").textContent = count(batch.success_count);
        document.getElementById("completionFailed").textContent = count(batch.failure_count);
        document.getElementById("completionBatch").textContent = batch.batch_number;
        document.querySelector(".action-grid").hidden = true;
        completion.scrollIntoView({ behavior: "smooth", block: "start" });
        await renderActivationHistory(isOperator);
        sessionStorage.removeItem(`iotconnect:selected:${state.account.account_id}`);
      } catch (error) {
        submitButton.disabled = false;
        showFailure(error);
      }
    });
    document.getElementById("viewActionEvidence").addEventListener("click", () => {
      document.getElementById("customerActionEvidence").scrollIntoView({ behavior: "smooth" });
    });
    await renderActivationHistory(isOperator);
  }

  function aggregateStatement(statement) {
    const categories = {
      "Monthly access charges": 0,
      "Account-level recurring charges": 0,
      "One-time charges": 0,
      "Credits and adjustments": 0,
    };
    statement.line_items.forEach((row) => {
      const amount = Number(row.amount || 0);
      if (amount < 0) categories["Credits and adjustments"] += amount;
      else if (row.posting_scope === "SUBSCRIPTION") categories["Monthly access charges"] += amount;
      else if (row.charge_type === "ONE_TIME") categories["One-time charges"] += amount;
      else categories["Account-level recurring charges"] += amount;
    });
    return categories;
  }

  function renderLatestStatement(statement) {
    const categories = aggregateStatement(statement);
    const rows = Object.entries(categories).filter(([, amount]) => amount !== 0);
    document.getElementById("latestBillingCard").innerHTML = `
      <div class="cycle-head"><h2>${html(monthLabel(statement.bill_cycle))} invoice</h2><span><span class="status issued">${html(statement.status)}</span></span></div>
      <div class="invoice-meta">
        <div class="invoice-fact"><span>Invoice</span><strong>${html(statement.statement_number)}</strong></div>
        <div class="invoice-fact"><span>Billing period</span><strong>${html(statement.billing_period_start)}–${html(statement.billing_period_end)}</strong></div>
        <div class="invoice-fact"><span>Issued</span><strong>${html(statement.statement_date)}</strong></div>
        <div class="invoice-fact"><span>Due</span><strong>${html(statement.due_date)}</strong></div>
        <div class="invoice-fact"><span>Service lines posted</span><strong>${count(statement.legacy_service_line_count)}</strong></div>
      </div>
      <div class="invoice-actions"><p>Illustrative statement produced from reconciled legacy-billing output.</p><button id="viewInvoiceArtifact" class="pdf-button">View statement artifact</button></div>
      <table class="charge-table"><thead><tr><th>Invoice category</th><th class="num">Amount</th></tr></thead><tbody>
        ${rows.map(([label, amount]) => `<tr><td>${html(label)}</td><td class="num ${amount < 0 ? "negative" : ""}">${currency(amount)}</td></tr>`).join("")}
        <tr><td>Platform charges subtotal</td><td class="num">${currency(statement.current_charges)}</td></tr>
        <tr class="invoice-total"><td>Total invoice</td><td class="num">${currency(statement.total)}</td></tr>
      </tbody></table>
      <div class="boundary">IoT Connect supplies reconciled platform charges. Legacy Billing produces the invoice and remains responsible for tax, payment terms, accounts receivable, and collections.</div>`;
    document.getElementById("viewInvoiceArtifact").addEventListener("click", () => {
      const params = new URLSearchParams({
        account: statement.account_id,
        cycle: statement.bill_cycle,
      });
      location.href = appPath(`/artifacts/statement?${params.toString()}`);
    });
  }

  function renderBillingHistory(runs) {
    const card = document.getElementById("billingHistoryCard");
    card.innerHTML = `<div class="cycle-head"><h2>Billing history</h2><span>${count(runs.length)} completed runs</span></div><table><thead><tr><th>Cycle</th><th class="num">Source charges</th><th class="num">Output total</th><th>Status</th></tr></thead><tbody>${
      runs.length
        ? runs.map((run) => `<tr><td><span class="cycle-link">${html(monthLabel(run.bill_cycle))}</span></td><td class="num">${count(run.source_charge_count)}</td><td class="num">${currency(run.output_total)}</td><td><span class="status ${run.status === "PASSED" ? "issued" : "pending"}">${html(run.status)}</span></td></tr>`).join("")
        : '<tr><td colspan="4" class="empty-state">No completed bill runs for this account.</td></tr>'
    }</tbody></table><div class="history-note">Every run remains account-scoped and independently reconciled.</div>`;
  }

  async function initializeBilling() {
    await resolveAccount();
    applyAccountContext();
    const runs = await apiRequest(`/bill-runs?account_id=${encodeURIComponent(state.account.account_id)}`);
    renderBillingHistory(runs);
    if (!runs.length) {
      document.getElementById("latestBillingCard").innerHTML = '<div class="empty-state">No completed bill run exists. An operator can run the cycle from Bill Cycles.</div>';
      return;
    }
    const statement = await apiRequest(
      `/artifacts/accounts/${state.account.account_id}/legacy-statement/${runs[0].bill_cycle}`,
    );
    renderLatestStatement(statement);
  }

  async function initializeInventory() {
    const [summaries, available] = await Promise.all([
      apiRequest("/account-summaries"),
      apiRequest("/inventory/sims/available"),
    ]);
    const accounts = summaries.map((row) => row.account);
    const accountInput = document.getElementById("inventoryAccountSearch");
    const matches = document.getElementById("inventoryAccountMatches");
    let selectedAccount = null;
    const accountLabel = (account) =>
      `${account.account_name} · ${account.account_number} · ${account.external_billing_account_number}`;
    const renderAccountMatches = () => {
      const query = accountInput.value.trim().toLowerCase();
      const filtered = accounts
        .filter((account) => accountLabel(account).toLowerCase().includes(query))
        .slice(0, 10);
      matches.innerHTML = filtered.length
        ? filtered
            .map(
              (account) =>
                `<button type="button" class="account-match" data-account-id="${html(account.account_id)}"><b>${html(account.account_name)}</b><small>${html(account.account_number)} · ${html(account.external_billing_account_number)}</small></button>`,
            )
            .join("")
        : '<div class="account-match">No matching customer account</div>';
      matches.hidden = false;
      matches.querySelectorAll("button[data-account-id]").forEach((button) => {
        button.addEventListener("click", () => {
          selectedAccount = accounts.find(
            (account) => account.account_id === button.dataset.accountId,
          );
          accountInput.value = accountLabel(selectedAccount);
          matches.hidden = true;
        });
      });
    };
    accountInput.addEventListener("focus", renderAccountMatches);
    accountInput.addEventListener("input", () => {
      selectedAccount = null;
      renderAccountMatches();
    });
    document.addEventListener("click", (event) => {
      if (!event.target.closest(".account-search")) matches.hidden = true;
    });
    updateMetric("Operator stock", count(available.length));
    updateMetric("Assigned to customers", count(summaries.reduce((sum, row) => sum + row.available_sims, 0)));
    updateMetric("Active subscriptions", count(summaries.reduce((sum, row) => sum + row.active_subscriptions, 0)));

    document.getElementById("assignInventoryButton").addEventListener("click", async (event) => {
      const button = event.currentTarget;
      const quantity = Math.max(1, Math.min(1000, Number(document.getElementById("inventoryQuantity").value)));
      if (quantity > available.length) return toast(`Only ${available.length} unassigned SIMs are available.`, "error");
      const account = selectedAccount;
      if (!account) return toast("Search for and select a customer account first.", "error");
      button.disabled = true;
      try {
        const assigned = await apiJson(
          `/admin/accounts/${account.account_id}/sim-assignments`,
          "POST",
          { sim_resource_ids: available.slice(0, quantity).map((row) => row.sim_resource_id) },
          true,
        );
        document.getElementById("inventoryAssignmentResult").textContent = `${assigned.length} SIMs assigned`;
        toast(`${assigned.length} SIMs moved from operator stock to the selected account.`);
        setTimeout(() => location.reload(), 900);
      } catch (error) {
        showFailure(error);
      } finally {
        button.disabled = false;
      }
    });
  }

  async function renderBillRuns() {
    const runs = await apiRequest("/bill-runs");
    document.getElementById("billRunCount").textContent = `${count(runs.length)} runs`;
    document.getElementById("billRunRows").innerHTML = runs.length
      ? runs.map((run) => `<tr><td class="mono">${html(run.bill_run_number)}</td><td>${html(run.account_number)}</td><td>${html(run.bill_cycle)}</td><td>${html(run.billing_mode)}</td><td><span class="status ${run.status === "PASSED" ? "issued" : "pending"}">${html(run.status)}</span></td><td class="num">${currency(run.source_total)}</td><td class="num">${currency(run.output_total)}</td><td class="num">${currency(run.variance)}</td><td><button class="evidence-link view-billing-rows" data-run-id="${html(run.bill_run_id)}" data-run-number="${html(run.bill_run_number)}">Rows</button> · <a class="evidence-link" href="${API_BASE}/bill-runs/${encodeURIComponent(run.bill_run_id)}/file.csv">CSV</a> · <button class="evidence-link view-billing-checks" data-run-id="${html(run.bill_run_id)}" data-run-number="${html(run.bill_run_number)}">Checks</button></td></tr>`).join("")
      : '<tr><td colspan="9" class="empty-state">No bill runs yet.</td></tr>';
    document.querySelectorAll(".view-billing-rows").forEach((button) => {
      button.addEventListener("click", async () => {
        try {
          const rows = await apiRequest(`/bill-runs/${button.dataset.runId}/file`);
          document.getElementById("billingOutputTitle").textContent = `${button.dataset.runNumber} · Amdocs billing-feed rows`;
          document.getElementById("billingOutputCount").textContent = `${count(rows.length)} rows`;
          document.getElementById("billingOutputRows").innerHTML = rows.length
            ? `<table><thead><tr><th>Row</th><th>Posting scope</th><th>MDN</th><th>Charge code</th><th>Rate plan</th><th>Description</th><th class="num">Quantity</th><th class="num">Unit price</th><th class="num">Amount</th><th>GL code</th></tr></thead><tbody>${rows.map((row) => `<tr><td class="mono">${html(row.row_number)}</td><td>${html(row.posting_scope)}</td><td>${html(domesticMdn(row.mdn))}</td><td>${html(row.charge_code)}</td><td>${html(row.rate_plan_code)}</td><td>${html(row.description)}</td><td class="num">${count(row.quantity)}</td><td class="num">${currency(row.unit_price)}</td><td class="num">${currency(row.amount)}</td><td>${html(row.gl_code)}</td></tr>`).join("")}</tbody></table>`
            : '<div class="empty-state">No billing-feed rows exist for this run.</div>';
          document.getElementById("billingOutputCard").hidden = false;
          document.getElementById("billingOutputCard").scrollIntoView({ behavior: "smooth", block: "start" });
        } catch (error) {
          showFailure(error);
        }
      });
    });
    document.querySelectorAll(".view-billing-checks").forEach((button) => {
      button.addEventListener("click", async () => {
        try {
          const result = await apiRequest(`/bill-runs/${button.dataset.runId}/reconciliation`);
          const labels = {
            amounts_balance: "Source and output totals balance",
            all_sources_represented_once: "Every source charge is represented",
            no_duplicate_source_representations: "No source charge is represented twice",
            all_posting_targets_valid: "Every posting target is valid for its scope",
          };
          document.getElementById("billingReconciliationTitle").textContent = `${button.dataset.runNumber} · Revenue Assurance checks`;
          document.getElementById("billingReconciliationStatus").textContent = result.status;
          document.getElementById("billingReconciliationRows").innerHTML = `
            <div class="result-summary"><div><span>Source total</span><strong>${currency(result.source_total)}</strong></div><div><span>Output total</span><strong>${currency(result.output_total)}</strong></div><div><span>Variance</span><strong>${currency(result.variance)}</strong></div></div>
            <table><thead><tr><th>Acceptance check</th><th>Result</th></tr></thead><tbody>${Object.entries(result.acceptance_checks).map(([key, passed]) => `<tr><td>${html(labels[key] || key)}</td><td><span class="status ${passed ? "issued" : "pending"}">${passed ? "PASS" : "FAIL"}</span></td></tr>`).join("")}</tbody></table>
            <details class="customer-note"><summary>Raw API response</summary><pre>${html(JSON.stringify(result, null, 2))}</pre></details>`;
          const card = document.getElementById("billingReconciliationCard");
          card.hidden = false;
          card.scrollIntoView({ behavior: "smooth", block: "start" });
        } catch (error) {
          showFailure(error);
        }
      });
    });
  }

  async function initializeBillCycles() {
    await renderBillRuns();
    document.getElementById("runOperatorBillCycle").addEventListener("click", async (event) => {
      const button = event.currentTarget;
      const billCycle = document.getElementById("operatorBillCycle").value;
      button.disabled = true;
      try {
        const result = await apiJson("/admin/bill-cycles", "POST", { bill_cycle: billCycle }, true);
        const values = document.querySelectorAll("#billCycleSummary strong");
        values[0].textContent = count(result.accounts_evaluated);
        values[1].textContent = count(result.accounts_billed);
        values[2].textContent = count(result.accounts_skipped);
        const outcome = document.getElementById("billCycleOutcome");
        const billed = result.runs.map((run) => `${run.account_number}: ${run.bill_run_number}`).join(" · ");
        const skipped = result.skipped.map((row) => `${row.account_number}: ${row.reason}`).join(" · ");
        outcome.innerHTML = [
          billed ? `<strong>Billed</strong> ${html(billed)}` : "",
          skipped ? `<strong>Skipped</strong> ${html(skipped)}` : "",
        ].filter(Boolean).join("<br>");
        outcome.hidden = false;
        toast(`${result.bill_cycle}: ${result.accounts_billed} billed; ${result.accounts_skipped} skipped.`);
        await renderBillRuns();
      } catch (error) {
        showFailure(error);
      } finally {
        button.disabled = false;
      }
    });
  }

  async function initializeCatalog() {
    const plans = (await apiRequest("/catalog/rate-plans")).filter(
      (row) => row.product_offering_id === "OFFER-IOT-CONNECTIVITY",
    );
    document.getElementById("catalogCount").textContent = `${count(plans.length)} plans`;
    document.getElementById("catalogRows").innerHTML = plans
      .map((plan) => `<tr><td class="mono">${html(plan.rate_plan_id)}</td><td>${html(plan.rate_plan_code)}</td><td>${html(plan.name)}</td><td class="num">${currency(plan.monthly_price)}</td><td>${html(plan.gl_code)}</td><td><span class="status">${html(plan.status)}</span></td></tr>`)
      .join("");
  }

  async function initializePortfolio() {
    const [summaries, operatorInventory] = await Promise.all([
      apiRequest("/account-summaries"),
      apiRequest("/inventory/sims/available"),
    ]);
    const totalResources = summaries.reduce((sum, row) => sum + row.total_resources, 0);
    updateMetric("Customer accounts", count(summaries.length));
    updateMetric("SIMs under management", count(operatorInventory.length + totalResources));
    updateMetric("Active subscriptions", count(summaries.reduce((sum, row) => sum + row.active_subscriptions, 0)));
    updateMetric("Operator inventory", count(operatorInventory.length));
    updateMetric("Suspended", count(summaries.reduce((sum, row) => sum + row.suspended_subscriptions, 0)));
    updateMetric("Retired", count(summaries.reduce((sum, row) => sum + row.retired_subscriptions, 0)));
    const context = document.getElementById("portfolioContext");
    if (context) context.textContent = `${count(summaries.length)} prepared customer accounts · live operational data`;

    const table = document.querySelector(".account-table");
    const header = table?.querySelector("thead tr");
    if (header) header.innerHTML = '<th>Customer</th><th>Billing mode</th><th class="num">Active</th><th class="num">Available</th><th class="num">Suspended</th><th class="num">Total</th>';
    const rows = document.getElementById("portfolioAccountRows") || table?.querySelector("tbody");
    if (!rows) return;
    rows.innerHTML = summaries.length
      ? summaries.map(({ account, active_subscriptions, available_sims, suspended_subscriptions, total_resources }) => {
          const target = appPath(`/operator/account?account=${encodeURIComponent(account.account_id)}`);
          return `<tr class="live-account-row" tabindex="0" data-account-target="${html(target)}"><td><span class="account-name">${html(account.account_name)}</span><span class="subtext">${html(account.account_number)}</span></td><td><span class="status">${html(account.billing_mode)}</span></td><td class="num">${count(active_subscriptions)}</td><td class="num">${count(available_sims)}</td><td class="num">${count(suspended_subscriptions)}</td><td class="num">${count(total_resources)}</td></tr>`;
        }).join("")
      : '<tr><td colspan="6" class="empty-state">No prepared accounts are available.</td></tr>';
    rows.querySelectorAll("tr[data-account-target]").forEach((row) => {
      row.addEventListener("click", () => (location.href = row.dataset.accountTarget));
      row.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") location.href = row.dataset.accountTarget;
      });
    });
    updateMetric("Retired year to date", count(summaries.reduce((sum, row) => sum + row.retired_subscriptions, 0)));
  }

  async function initializeAccounts() {
    const summaries = await apiRequest("/account-summaries");
    document.getElementById("accountCount").textContent = `${count(summaries.length)} accounts`;
    const rows = document.getElementById("accountRows");
    rows.innerHTML = summaries.length
      ? summaries
          .map(({ account, active_subscriptions, available_sims, suspended_subscriptions, latest_bill_run }) => {
            const target = appPath(`/operator/account?account=${encodeURIComponent(account.account_id)}`);
            const latest = latest_bill_run
              ? `${monthLabel(latest_bill_run.bill_cycle)} · ${currency(latest_bill_run.output_total)}`
              : "No completed run";
            return `<tr data-account-target="${html(target)}"><td><a class="account-name" href="${html(target)}">${html(account.account_name)}</a><span class="subtext">${html(account.account_number)}</span></td><td class="mono">${html(account.external_billing_account_number)}</td><td><span class="status">${html(account.billing_mode)}</span></td><td class="num">${count(active_subscriptions)}</td><td class="num">${count(available_sims)}</td><td class="num">${count(suspended_subscriptions)}</td><td>${html(latest)}</td></tr>`;
          })
          .join("")
      : '<tr><td colspan="7" class="empty-state">No accounts are available.</td></tr>';
    rows.querySelectorAll("tr[data-account-target]").forEach((row) => {
      row.addEventListener("click", (event) => {
        if (event.target.closest("a")) return;
        location.href = row.dataset.accountTarget;
      });
    });
  }

  const initializers = {
    "portal-overview": initializeAccountOverview,
    "operator-account": initializeAccountOverview,
    "operator-account-configuration": initializeAccountConfiguration,
    "operator-account-new": initializeAccountNew,
    "portal-subscriptions": initializeResources,
    "operator-subscriptions": initializeResources,
    "portal-billing": initializeBilling,
    "operator-inventory": initializeInventory,
    "operator-bill-cycles": initializeBillCycles,
    "operator-catalog": initializeCatalog,
    "operator-portfolio": initializePortfolio,
    "operator-accounts": initializeAccounts,
    "operator-api-activity": async () => {},
    "portal-actions": initializeActions,
  };

  document.addEventListener("DOMContentLoaded", async () => {
    makeApiReferenceDiscreet();
    try {
      convertBillingChromeForOperator();
      await (initializers[page] || (async () => {}))();
    } catch (error) {
      showFailure(error);
      console.error(error);
    }
  });
})();
