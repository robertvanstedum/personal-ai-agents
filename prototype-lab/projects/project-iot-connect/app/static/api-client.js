// URL prefix (reverse-proxy deployment). The server injects
// <meta name="iotconnect-root-path"> only when IOTCONNECT_ROOT_PATH is set;
// standalone pages carry no meta and ROOT_PATH is "".
const ROOT_PATH = (document.querySelector('meta[name="iotconnect-root-path"]') || {}).content || "";
window.IOTCONNECT_ROOT_PATH = ROOT_PATH;
function appPath(path) {
  return `${ROOT_PATH}${path}`;
}
const API_BASE = `${ROOT_PATH}/api/v1`;
const ADMIN_ROLE = "BUSINESS_OPS_ADMIN";
const CUSTOMER_ROLE = "ENTERPRISE_CUSTOMER";

async function apiRequest(path, options = {}) {
  const requestId = crypto.randomUUID();
  const headers = new Headers(options.headers || {});
  headers.set("X-Request-ID", requestId);
  if (options.admin) headers.set("X-Demo-Role", ADMIN_ROLE);
  if (options.customerAccountId) {
    headers.set("X-Demo-Role", CUSTOMER_ROLE);
    headers.set("X-Demo-Account-ID", options.customerAccountId);
  }
  const response = await fetch(`${API_BASE}${path}`, { ...options, headers });
  let payload = null;
  if (response.status !== 204) {
    const contentType = response.headers.get("content-type") || "";
    if (contentType.includes("application/json")) {
      payload = await response.json();
    } else {
      const text = await response.text();
      payload = text ? { message: text } : null;
    }
  }
  if (!response.ok) {
    const error = new Error(payload?.message || `HTTP ${response.status}`);
    error.code = payload?.code || "HTTP_ERROR";
    error.requestId = payload?.request_id || requestId;
    error.details = payload?.details;
    throw error;
  }
  return payload;
}

function apiCustomerJson(path, method, body, accountId) {
  return apiRequest(path, {
    method,
    customerAccountId: accountId,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

function apiJson(path, method, body, admin = false) {
  return apiRequest(path, {
    method,
    admin,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

function apiCsv(path, rawCsv) {
  return apiRequest(path, {
    method: "POST",
    admin: true,
    headers: { "Content-Type": "text/csv" },
    body: rawCsv,
  });
}

function escapeHtml(value) {
  return String(value ?? "—")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function money(value) {
  return `$${Number(value || 0).toFixed(2)}`;
}

function renderTable(rows, columns, empty = "No records yet.") {
  if (!rows?.length) return `<div class="empty-state">${escapeHtml(empty)}</div>`;
  return `<div class="table-scroll"><table><thead><tr>${columns
    .map((column) => `<th>${escapeHtml(column.label)}</th>`)
    .join("")}</tr></thead><tbody>${rows
    .map(
      (row) => `<tr>${columns
        .map((column) => {
          const raw = row[column.key];
          const value = column.format ? column.format(raw, row) : raw;
          return `<td class="${column.className || ""}">${escapeHtml(value)}</td>`;
        })
        .join("")}</tr>`,
    )
    .join("")}</tbody></table></div>`;
}

function showToast(message, kind = "info") {
  const toast = document.getElementById("toast");
  if (!toast) return;
  toast.textContent = message;
  toast.className = `toast ${kind}`;
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => (toast.className = "toast hidden"), 4500);
}
