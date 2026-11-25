"use strict";

// Dashboard: talks to the JSON API and draws the page.
// Safety rule used everywhere below: user text is put on the page with
// textContent (never innerHTML), so a note like "<script>..." is shown as plain text.

const money = new Intl.NumberFormat("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const compactMoney = new Intl.NumberFormat("en-IN", { notation: "compact", maximumFractionDigits: 1 });
const RUPEE = "\u20B9";
const $ = (id) => document.getElementById(id);

let currentExpenses = [];
let editingId = null;
let searchTimer = null;

// ---------------------------------------------------------------- small helpers

function pad(n) {
  return String(n).padStart(2, "0");
}

// Local date as YYYY-MM-DD. (toISOString() uses UTC and can give "yesterday" in India.)
function localDate(d) {
  return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate());
}

let budgetMonth = localDate(new Date()).slice(0, 7);          // YYYY-MM shown in the budget card

function showDate(iso) {
  return iso.slice(8, 10) + "-" + iso.slice(5, 7) + "-" + iso.slice(0, 4);
}

function monthDate(ym) {
  return new Date(Number(ym.slice(0, 4)), Number(ym.slice(5, 7)) - 1, 1);
}

function monthLabel(ym) {
  return monthDate(ym).toLocaleDateString("en-IN", { month: "long", year: "numeric" });
}

function shiftMonth(ym, delta) {
  const d = monthDate(ym);
  d.setMonth(d.getMonth() + delta);
  return localDate(d).slice(0, 7);
}

function rupees(n) {
  return RUPEE + money.format(n);
}

// kind is "ok", "err" or "warn"; an empty text hides the message.
function setMessage(id, text, kind) {
  const el = $(id);
  el.textContent = text;
  el.className = "msg " + (text ? kind : "");
}

function cell(text, className) {
  const td = document.createElement("td");
  td.textContent = text;
  if (className) td.className = className;
  return td;
}

function alertText(alert) {
  return "you are over budget for " + monthLabel(alert.month) + " (" + rupees(alert.spent) +
    " spent of " + rupees(alert.limit) + ").";
}

// One place for every request. Sends JSON, turns errors into exceptions.
async function api(method, url, body) {
  const options = { method: method, headers: {} };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(url, options);
  if (response.status === 401) {
    window.location.href = "/login";
    throw new Error("Please log in.");
  }
  if (response.status === 204) return null;
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || "Something went wrong. Please try again.");
  return data;
}

// ---------------------------------------------------------------- filters

function dateQuery() {
  const params = new URLSearchParams();
  if ($("filter-from").value) params.set("from", $("filter-from").value);
  if ($("filter-to").value) params.set("to", $("filter-to").value);
  return params;
}

function listQuery() {
  const params = dateQuery();
  if ($("filter-category").value) params.set("category", $("filter-category").value);
  if ($("filter-q").value.trim()) params.set("q", $("filter-q").value.trim());
  return params.toString();
}

// ---------------------------------------------------------------- summary and list

function renderSummary(summary) {
  $("stat-total").textContent = rupees(summary.total);
  $("stat-count").textContent = String(summary.count);

  const list = $("breakdown");
  list.replaceChildren();
  summary.by_category.forEach((item) => {
    const li = document.createElement("li");

    const name = document.createElement("span");
    name.className = "cat-name";
    name.textContent = item.category;

    const bar = document.createElement("span");
    bar.className = "bar";
    const fill = document.createElement("span");
    fill.style.width = (summary.total > 0 ? (item.total / summary.total) * 100 : 0) + "%";
    bar.appendChild(fill);

    const amount = document.createElement("span");
    amount.className = "num";
    amount.textContent = rupees(item.total);

    li.append(name, bar, amount);
    list.appendChild(li);
  });
}

function renderTable(expenses) {
  currentExpenses = expenses;
  const body = $("expense-rows");
  body.replaceChildren();
  $("empty").hidden = expenses.length > 0;
  $("expense-table").hidden = expenses.length === 0;

  let paise = 0;
  expenses.forEach((e) => {
    paise += Math.round(e.amount * 100);
    const tr = document.createElement("tr");
    tr.append(cell(showDate(e.date)), cell(e.category), cell(e.note, "note"), cell(rupees(e.amount), "num"));

    const actions = document.createElement("td");
    actions.className = "actions";
    const edit = document.createElement("button");
    edit.type = "button";
    edit.className = "btn secondary small";
    edit.textContent = "Edit";
    edit.addEventListener("click", () => openEdit(e.id));
    const del = document.createElement("button");
    del.type = "button";
    del.className = "btn danger small";
    del.textContent = "Delete";
    del.addEventListener("click", () => removeExpense(e.id));
    actions.append(edit, del);
    tr.appendChild(actions);

    body.appendChild(tr);
  });

  $("table-summary").textContent = expenses.length === 0 ? "" :
    expenses.length + (expenses.length === 1 ? " expense" : " expenses") + " shown \u00B7 " + rupees(paise / 100);
}

async function refreshList() {
  const query = listQuery();
  $("export-link").href = "/api/expenses.csv" + (query ? "?" + query : "");
  try {
    const [list, summary] = await Promise.all([
      api("GET", "/api/expenses?" + query),
      api("GET", "/api/summary?" + dateQuery().toString()),
    ]);
    renderSummary(summary);
    renderTable(list);
    setMessage("page-message", "", "ok");
  } catch (error) {
    setMessage("page-message", error.message, "err");
  }
}

// ---------------------------------------------------------------- budget

function renderBudget(b) {
  const hasBudget = b.limit !== null;
  const progress = $("budget-progress");
  const text = $("budget-text");

  $("budget-remove").hidden = !hasBudget;
  $("budget-limit").placeholder = hasBudget ? "Now " + b.limit.toFixed(2) : "e.g. 4000";

  const percent = hasBudget ? b.percent : 0;
  $("budget-fill").style.width = Math.min(percent, 100) + "%";
  progress.setAttribute("aria-valuenow", String(Math.round(Math.min(percent, 100))));
  progress.className = "progress " + (!hasBudget ? "none" : b.exceeded ? "over" : percent >= 80 ? "warn" : "ok");

  if (!hasBudget) {
    text.className = "budget-text";
    text.textContent = "No budget set for " + monthLabel(b.month) + ". Spent so far: " + rupees(b.spent) + ".";
  } else if (b.exceeded) {
    text.className = "budget-text over";
    text.textContent = "Over budget by " + rupees(-b.remaining) + " (" + rupees(b.spent) + " spent of " + rupees(b.limit) + ").";
  } else {
    text.className = "budget-text" + (percent >= 80 ? " warn" : "");
    text.textContent = rupees(b.spent) + " of " + rupees(b.limit) + " spent (" + percent + "%) \u00B7 " +
      rupees(b.remaining) + " left" + (percent >= 80 ? ". Almost at your limit." : ".");
  }
}

async function loadBudget() {
  $("budget-month").textContent = monthLabel(budgetMonth);
  try {
    renderBudget(await api("GET", "/api/budget/" + budgetMonth));
  } catch (error) {
    setMessage("budget-message", error.message, "err");
  }
}

async function saveBudget(event) {
  event.preventDefault();
  setMessage("budget-message", "", "ok");
  try {
    await api("PUT", "/api/budget/" + budgetMonth, { limit: $("budget-limit").value });
    $("budget-limit").value = "";
    await loadBudget();
    setMessage("budget-message", "Budget saved for " + monthLabel(budgetMonth) + ".", "ok");
  } catch (error) {
    setMessage("budget-message", error.message, "err");
  }
}

async function removeBudget() {
  if (!window.confirm("Remove the budget for " + monthLabel(budgetMonth) + "?")) return;
  try {
    await api("DELETE", "/api/budget/" + budgetMonth);
    $("budget-limit").value = "";
    await loadBudget();
    setMessage("budget-message", "Budget removed.", "ok");
  } catch (error) {
    setMessage("budget-message", error.message, "err");
  }
}

function moveBudgetMonth(delta) {
  budgetMonth = shiftMonth(budgetMonth, delta);
  $("budget-limit").value = "";
  setMessage("budget-message", "", "ok");
  loadBudget();
}

// ---------------------------------------------------------------- 6-month trend

function renderTrend(months) {
  const box = $("trend");
  box.replaceChildren();
  const max = Math.max(0, ...months.map((m) => m.total));
  const parts = [];

  months.forEach((m, i) => {
    const short = monthDate(m.month).toLocaleDateString("en-IN", { month: "short" });
    parts.push(short + " " + rupees(m.total));

    const col = document.createElement("div");
    col.className = "trend-col" + (i === months.length - 1 ? " current" : "");

    const value = document.createElement("span");
    value.className = "trend-value";
    value.textContent = m.total > 0 ? RUPEE + compactMoney.format(m.total) : "";

    const area = document.createElement("span");
    area.className = "trend-area";
    const bar = document.createElement("span");
    bar.className = "trend-bar" + (m.total > 0 ? "" : " zero");
    bar.style.height = (max > 0 ? (m.total / max) * 100 : 0) + "%";
    area.appendChild(bar);

    const label = document.createElement("span");
    label.className = "trend-label";
    label.textContent = short;

    col.append(value, area, label);
    box.appendChild(col);
  });
  box.setAttribute("aria-label", "Spending per month: " + parts.join(", "));
}

async function loadTrend() {
  try {
    const data = await api("GET", "/api/summary/monthly?months=6&to=" + localDate(new Date()).slice(0, 7));
    renderTrend(data.months);
  } catch (error) {
    setMessage("page-message", error.message, "err");
  }
}

function refreshAll() {
  return Promise.all([refreshList(), loadBudget(), loadTrend()]);
}

// ---------------------------------------------------------------- expense actions

async function addExpense(event) {
  event.preventDefault();
  const form = event.target;
  setMessage("add-message", "", "ok");
  try {
    const saved = await api("POST", "/api/expenses", {
      date: form.date.value,
      category: form.category.value,
      amount: form.amount.value,      // sent as text so no rounding happens in the browser
      note: form.note.value,
    });
    form.amount.value = "";
    form.note.value = "";
    form.amount.focus();
    await refreshAll();
    if (saved.budget_alert) setMessage("add-message", "Expense added. Warning: " + alertText(saved.budget_alert), "warn");
    else setMessage("add-message", "Expense added.", "ok");
  } catch (error) {
    setMessage("add-message", error.message, "err");
  }
}

function openEdit(id) {
  const expense = currentExpenses.find((e) => e.id === id);
  if (!expense) return;
  editingId = id;
  const form = $("edit-form");
  form.date.value = expense.date;
  form.category.value = expense.category;
  form.amount.value = expense.amount.toFixed(2);
  form.note.value = expense.note;
  setMessage("edit-message", "", "ok");
  $("edit-dialog").showModal();
}

async function saveEdit(event) {
  event.preventDefault();
  const form = event.target;
  try {
    const saved = await api("PUT", "/api/expenses/" + editingId, {
      date: form.date.value,
      category: form.category.value,
      amount: form.amount.value,
      note: form.note.value,
    });
    $("edit-dialog").close();
    await refreshAll();
    if (saved.budget_alert) setMessage("page-message", "Expense updated. Warning: " + alertText(saved.budget_alert), "warn");
    else setMessage("page-message", "Expense updated.", "ok");
  } catch (error) {
    setMessage("edit-message", error.message, "err");
  }
}

async function removeExpense(id) {
  const expense = currentExpenses.find((e) => e.id === id);
  if (!expense) return;
  const question = "Delete this expense (" + rupees(expense.amount) + ", " + expense.category + ")?";
  if (!window.confirm(question)) return;
  try {
    await api("DELETE", "/api/expenses/" + id);
    await refreshAll();
    setMessage("page-message", "Expense deleted.", "ok");
  } catch (error) {
    setMessage("page-message", error.message, "err");
  }
}

// ---------------------------------------------------------------- account actions

async function logout() {
  try {
    await api("POST", "/api/logout");
  } finally {
    window.location.href = "/login";
  }
}

function openPasswordDialog() {
  $("password-form").reset();
  setMessage("password-message", "", "ok");
  $("password-dialog").showModal();
}

async function changePassword(event) {
  event.preventDefault();
  const current = $("pw-current").value;
  const next = $("pw-new").value;
  if (next !== $("pw-confirm").value) {
    setMessage("password-message", "The new passwords do not match.", "err");
    return;
  }
  try {
    await api("POST", "/api/change-password", { current_password: current, new_password: next });
    $("password-dialog").close();
    setMessage("page-message", "Password changed. Your other devices were signed out.", "ok");
  } catch (error) {
    setMessage("password-message", error.message, "err");
  }
}

// ---------------------------------------------------------------- filter buttons

function setThisMonth() {
  const now = new Date();
  $("filter-from").value = localDate(new Date(now.getFullYear(), now.getMonth(), 1));
  $("filter-to").value = localDate(new Date(now.getFullYear(), now.getMonth() + 1, 0));
  refreshList();
}

function clearFilters() {
  $("filter-from").value = "";
  $("filter-to").value = "";
  $("filter-category").value = "";
  $("filter-q").value = "";
  refreshList();
}

// ---------------------------------------------------------------- start

$("add-date").value = localDate(new Date());
$("add-form").addEventListener("submit", addExpense);
$("edit-form").addEventListener("submit", saveEdit);
$("edit-cancel").addEventListener("click", () => $("edit-dialog").close());
$("password-form").addEventListener("submit", changePassword);
$("password-cancel").addEventListener("click", () => $("password-dialog").close());
$("change-password").addEventListener("click", openPasswordDialog);
$("logout").addEventListener("click", logout);
$("btn-month").addEventListener("click", setThisMonth);
$("btn-clear").addEventListener("click", clearFilters);
$("budget-form").addEventListener("submit", saveBudget);
$("budget-remove").addEventListener("click", removeBudget);
$("budget-prev").addEventListener("click", () => moveBudgetMonth(-1));
$("budget-next").addEventListener("click", () => moveBudgetMonth(1));
$("filter-form").addEventListener("submit", (e) => { e.preventDefault(); refreshList(); });
["filter-from", "filter-to", "filter-category"].forEach((id) => $(id).addEventListener("change", refreshList));
$("filter-q").addEventListener("input", () => {            // wait until typing pauses
  clearTimeout(searchTimer);
  searchTimer = setTimeout(refreshList, 250);
});
refreshAll();
