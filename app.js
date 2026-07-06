const state = {
  token: localStorage.getItem("ncp_token") || "",
  products: [],
  customers: [],
  invoices: [],
  credits: [],
  documents: [],
  accessUsers: [],
};
let mainChart = null;

const $ = (selector) => document.querySelector(selector);
const money = (value) => `KES ${Number(value || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const today = () => new Date().toISOString().slice(0, 10);
const monthNow = () => new Date().toISOString().slice(0, 7);

function toast(message) {
  const box = $("#toast");
  box.textContent = message;
  box.classList.add("show");
  setTimeout(() => box.classList.remove("show"), 3200);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(state.token ? { Authorization: `Bearer ${state.token}` } : {}),
      ...(options.headers || {}),
    },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || "Something went wrong.");
  return data;
}

function updateDateDisplay() {
  const now = new Date();
  const options = { day: 'numeric', month: 'long', year: 'numeric' };
  const dateStr = now.toLocaleDateString('en-GB', options);
  $("#currentDate").textContent = dateStr;
}

function showApp() {
  $("#loginView").classList.add("hidden");
  $("#appView").classList.remove("hidden");
  updateDateDisplay();
}

function showLogin() {
  $("#loginView").classList.remove("hidden");
  $("#appView").classList.add("hidden");
}

function renderTable(selector, headers = [], rows = []) {
  const table = $(selector);
  table.innerHTML = `
    <thead><tr>${(headers || []).map((h) => `<th>${h}</th>`).join("")}</tr></thead>
    <tbody>${(rows || []).length ? rows.filter(r => r).map((row) => `<tr>${(row || []).map((cell) => `<td>${cell}</td>`).join("")}</tr>`).join("") : `<tr><td colspan="${(headers || []).length}">No results found.</td></tr>`}
    </tbody>
  `;
}

function renderDocuments(docs = [], target = "#recentDocs") {
  const box = $(target);
  if (!(docs || []).length) {
    box.innerHTML = `<p class="muted">Generated PDFs will appear here.</p>`;
    return;
  }
  box.innerHTML = (docs || []).map((doc) => `
    <article class="doc">
      <div><strong>${doc.number}</strong><br><span class="muted">${doc.type} · ${doc.title}</span></div>
      <a href="${doc.pdf_path}" target="_blank" rel="noreferrer">Open PDF</a>
    </article>
  `).join("");
}

function productOptions() {
  return (state.products || [])
    .filter((product) => product.active)
    .map((product) => `<option value="${product.id}">${product.name} · ${money(product.price)} · stock ${product.stock}</option>`)
    .join("");
}

function addInvoiceItem(productId = "", quantity = 1) {
  const row = document.createElement("div");
  row.className = "item-row";
  row.innerHTML = `
    <select name="product_id" required>${productOptions()}</select>
    <input name="quantity" type="number" min="1" step="1" value="${quantity}" required />
    <button type="button" title="Remove item">×</button>
  `;
  row.querySelector("select").value = productId || state.products[0]?.id || "";
  row.querySelector("button").addEventListener("click", () => row.remove());
  $("#invoiceItems").appendChild(row);
}

function addCreditItem(productId = "", quantity = 1) {
  const row = document.createElement("div");
  row.className = "item-row";
  row.innerHTML = `
    <select name="product_id" required>${productOptions()}</select>
    <input name="quantity" type="number" min="1" step="1" value="${quantity}" required />
    <button type="button" title="Remove item">×</button>
  `;
  row.querySelector("select").value = productId || state.products[0]?.id || "";
  row.querySelector("button").addEventListener("click", () => row.remove());
  $("#creditItems").appendChild(row);
}

function animateValue(id, start, end, duration, isMoney = false) {
  const obj = $(id);
  let startTimestamp = null;
  const step = (timestamp) => {
    if (!startTimestamp) startTimestamp = timestamp;
    const progress = Math.min((timestamp - startTimestamp) / duration, 1);
    const current = Math.floor(progress * (end - start) + start);
    obj.textContent = isMoney ? money(progress * end) : current;
    if (progress < 1) window.requestAnimationFrame(step);
  };
  window.requestAnimationFrame(step);
}

function updateCharts(dashboard) {
  const ctx = $('#businessChart').getContext('2d');
  if (mainChart) mainChart.destroy();

  mainChart = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: ['Active Products', 'Stock (x10)', 'Invoices', 'Total Customers'],
      datasets: [{
        label: 'Business Metrics',
        data: [(dashboard.products?.count || 0), (dashboard.products?.stock || 0) / 10, (dashboard.invoices || 0), (dashboard.customers || 0)],
        backgroundColor: ['#0f6b54', '#d9a441', '#12302b', '#687872'],
        borderRadius: 6,
      }]
    },
    options: {
      responsive: true,
      plugins: {
        legend: { display: false }
      },
      scales: {
        y: { 
          beginAtZero: true,
          grid: { color: '#dce7e2' },
          ticks: { font: { family: 'Inter' } }
        },
        x: { ticks: { font: { family: 'Inter', weight: 'bold' } } }
      }
    }
  });
}

function renderTopCustomers(topCustomers = []) {
  renderTable("#topCustomersTable", ["Customer", "Invoices", "Total"], (topCustomers || []).map(c => [
    c.customer,
    c.invoices,
    money(c.total)
  ]));
}

function renderAccessUsers(users = []) {
  const safeUsers = (Array.isArray(users) ? users : []).filter(Boolean);
  $("#accessUsersCount").textContent = `${safeUsers.length} user${safeUsers.length === 1 ? "" : "s"}`;
  renderTable("#accessUsersTable", ["Type", "Identifier", "Added"], safeUsers.map((user) => [
    user.type || "-",
    user.identifier || "-",
    user.created_at || "-",
  ]));
}

// Helper function to attach status change listener
function attachStatusListener(sel, statusColor) {
  sel.addEventListener("change", async () => {
    const newStatus = sel.value;
    const color = statusColor[newStatus] || "#687872";
    sel.style.color = color;
    sel.style.borderColor = color;
    
    try {
      await api(`/api/invoices/${sel.dataset.id}/status`, {
        method: "POST",
        body: JSON.stringify({ status: newStatus }),
      });
      toast(`Invoice marked as ${newStatus}.`);
      // Update local state so dashboard stays consistent without full reload
      const inv = state.invoices.find(i => i.id == sel.dataset.id);
      if (inv) inv.status = newStatus;
    } catch (err) {
      toast(err.message);
      await loadAll(); // revert on error
    }
  });
}

async function loadAll() {
  const [dashboard, products, customers, invoices, credits, documents, accessUsers] = await Promise.all([
    api("/api/dashboard"),
    api("/api/products"),
    api("/api/customers"),
    api("/api/invoices"),
    api("/api/credit-notes"),
    api("/api/documents"),
    api("/api/access-users"),
  ]);

  // Ensure all state variables are arrays to prevent .map() errors
  state.products = (Array.isArray(products) ? products : []).filter(p => p);
  state.customers = (Array.isArray(customers) ? customers : []).filter(c => c);
  state.invoices = (Array.isArray(invoices) ? invoices : []).filter(i => i);
  state.credits = (Array.isArray(credits) ? credits : []).filter(cr => cr);
  state.documents = (Array.isArray(documents) ? documents : []).filter(d => d);
  state.accessUsers = (Array.isArray(accessUsers?.users) ? accessUsers.users : []).filter(u => u);

  const dbStats = dashboard || {};
  animateValue("#metricProducts", 0, dashboard.products?.count || 0, 800);
  animateValue("#metricStock", 0, dashboard.products?.stock || 0, 800);
  animateValue("#metricInvoices", 0, dashboard.invoices || 0, 800);
  animateValue("#metricCustomers", 0, state.customers.length, 800);
  animateValue("#metricRevenue", 0, dashboard.revenue || 0, 1200, true);
  
  renderDocuments(dashboard.documents || []);
  renderTopCustomers(dashboard.top_customers || []);
  renderAccessUsers(state.accessUsers);
  updateCharts(dashboard);

  renderTable("#productsTable", ["Name", "SKU", "Price", "Stock", "Status"], state.products.map((p) => [
    `<button class="ghost edit-product" data-id="${p.id}">${p.name}</button>`,
    p.sku,
    money(p.price),
    p.stock,
    p.active ? "Active" : "Hidden",
  ]));
  document.querySelectorAll(".edit-product").forEach((button) => {
    button.addEventListener("click", () => {
      const p = state.products.find((item) => item.id == button.dataset.id);
      const form = $("#productForm");
      form.id.value = p.id;
      form.name.value = p.name;
      form.sku.value = p.sku;
      form.price.value = p.price;
      form.stock.value = p.stock;
      form.active.checked = Boolean(p.active);
    });
  });

  renderTable("#customersTable", ["Name", "Phone", "Email", "Address"], state.customers.map((c) => [c.name, c.phone || "-", c.email || "-", c.address || "-"]));
  $("#customerNames").innerHTML = state.customers.map((c) => `<option value="${c.name}"></option>`).join("");

  const statusColor = { "Paid": "#0f6b54", "Unpaid": "#d9674f", "Partially Paid": "#d9a441", "Overdue": "#8b1a1a" };
  renderTable("#invoicesTable", ["Number", "Customer", "Date", "Status", "Total", "Actions"], state.invoices.map((i) => {
    const color = statusColor[i.status] || "#687872";
    return [
      i.number,
      i.customer_name,
      i.invoice_date,
      `<select class="status-select" data-id="${i.id}" style="color:${color};font-weight:700;border:2px solid ${color};padding:0.5rem 0.6rem;border-radius:6px;background:white;cursor:pointer;font-size:0.85rem;">
        <option${i.status==="Unpaid"?" selected":""}>Unpaid</option>
        <option${i.status==="Paid"?" selected":""}>Paid</option>
        <option${i.status==="Partially Paid"?" selected":""}>Partially Paid</option>
        <option${i.status==="Overdue"?" selected":""}>Overdue</option>
      </select>`,
      money(i.total),
      `<div class="table-actions">
        <a href="${i.pdf_path}" target="_blank" rel="noreferrer">Print</a>
        <button class="ghost sm edit-invoice" data-id="${i.id}">Edit</button>
      </div>`,
    ];
  }));
  
  // Attach listeners to all status selects
  document.querySelectorAll(".status-select").forEach(sel => {
    attachStatusListener(sel, statusColor);
  });
  
  document.querySelectorAll(".edit-invoice").forEach(btn => {
    btn.onclick = () => {
      const i = state.invoices.find(inv => inv.id == btn.dataset.id);
      const form = $("#invoiceForm");
      form.id.value = i.id;
      form.number.value = i.number;
      form.lpo_number.value = i.lpo_number || "";
      form.customer_name.value = i.customer_name;
      form.invoice_date.value = i.invoice_date;
      form.due_date.value = i.due_date || "";
      form.VAT.value = i.VAT || 0;
      $("#invoiceItems").innerHTML = "";
      const items = typeof i.items_json === 'string' ? JSON.parse(i.items_json) : i.items_json;
      items.forEach(item => addInvoiceItem(item.product_id, item.quantity));
      document.querySelector('.nav[data-view="invoices"]').click();
    };
  });

  $("#creditInvoice").innerHTML = state.invoices.map((i) => `<option value="${i.id}">${i.number} · ${i.customer_name} · ${money(i.total)}</option>`).join("");

  renderTable("#creditsTable", ["Number", "Customer", "Date", "Total", "Actions"], state.credits.map((c) => [
    c.number,
    c.customer_name,
    c.credit_date,
    money(c.total),
    `<div class="table-actions">
      <a href="${c.pdf_path}" target="_blank" rel="noreferrer">Print</a>
      <button class="ghost sm edit-credit" data-id="${c.id}">Edit</button>
    </div>`,
  ]));
  document.querySelectorAll(".edit-credit").forEach(btn => {
    btn.onclick = () => {
      const c = state.credits.find(cr => cr.id == btn.dataset.id);
      const form = $("#creditForm");
      form.id.value = c.id;
      form.number.value = c.number;
      form.invoice_id.value = c.invoice_id || "";
      form.reason.value = c.reason || "";
      $("#creditItems").innerHTML = "";
      const items = typeof c.items_json === 'string' ? JSON.parse(c.items_json) : c.items_json;
      items.forEach(item => addCreditItem(item.product_id, item.quantity));
      document.querySelector('.nav[data-view="credit"]').click();
    };
  });

  renderTable("#documentsTable", ["Type", "Number", "Title", "Created", "PDF"], state.documents.map((d) => [
    d.type,
    d.number,
    d.title,
    d.created_at,
    `<a href="${d.pdf_path}" target="_blank" rel="noreferrer">Open</a>`,
  ]));

  if (!$("#invoiceItems").children.length && products.length) addInvoiceItem();
  if (!$("#creditItems").children.length && products.length) addCreditItem();
}

$("#loginForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  try {
    const data = await api("/api/login", {
      method: "POST",
      body: JSON.stringify({ username: form.username.value, password: form.password.value }),
    });
    state.token = data.token;
    localStorage.setItem("ncp_token", data.token);
    showApp();
    await loadAll();
    toast("Logged in.");
  } catch (error) {
    toast(error.message);
  }
});

document.querySelectorAll(".nav").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".nav").forEach((item) => item.classList.remove("active"));
    document.querySelectorAll(".view").forEach((item) => item.classList.remove("active-view"));
    button.classList.add("active");
    $(`#${button.dataset.view}`).classList.add("active-view");
    $("#viewTitle").textContent = button.textContent;
  });
});

$("#refreshBtn").addEventListener("click", () => loadAll().then(() => toast("Fresh data loaded.")));
$("#addInvoiceItem").addEventListener("click", () => addInvoiceItem());
$("#addCreditItem").addEventListener("click", () => addCreditItem());

$("#quickInvoiceBtn").addEventListener("click", () => {
  document.querySelector('.nav[data-view="invoices"]').click();
});

$("#logoutBtn").addEventListener("click", async () => {
  try {
    await api("/api/logout", { method: "POST" });
  } catch (err) {
    console.warn("Logout failed", err);
  }
  state.token = "";
  localStorage.removeItem("ncp_token");
  showLogin();
});

$("#productForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector('button[type="submit"]');
  try {
    button.disabled = true;
    await api("/api/products", {
      method: "POST",
      body: JSON.stringify({
        id: form.id.value || null,
        name: form.name.value,
        sku: form.sku.value,
        price: form.price.value,
        stock: form.stock.value,
        active: form.active.checked,
      }),
    });
    form.reset();
    form.active.checked = true;
    await loadAll();
    toast("Product saved.");
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = false;
  }
});

$("#customerForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector('button[type="submit"]');
  try {
    button.disabled = true;
    await api("/api/customers", {
      method: "POST",
      body: JSON.stringify({ name: form.name.value, phone: form.phone.value, email: form.email.value, address: form.address.value }),
    });
    form.reset();
    await loadAll();
    toast("Customer saved.");
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = false;
  }
});

$("#invoiceForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector('button[type="submit"]');
  
  try {
    const items = [...$("#invoiceItems").querySelectorAll(".item-row")].map((row) => ({
      product_id: row.querySelector('[name="product_id"]').value,
      quantity: row.querySelector('[name="quantity"]').value,
    }));

    if (items.length === 0) {
      toast("Please add at least one item to the invoice.");
      return;
    }

    button.disabled = true;
    button.textContent = "Generating...";

    const result = await api("/api/invoices", {
      method: "POST",
      body: JSON.stringify({
        id: form.id.value || null,
        number: form.number.value,
        lpo_number: form.lpo_number.value,
        customer_name: form.customer_name.value,
        invoice_date: form.invoice_date.value || today(),
        due_date: form.due_date.value,
        VAT: form.VAT.value,
        items,
      }),
    });
    window.open(result.pdf_path, "_blank");
    $("#invoiceItems").innerHTML = "";
    form.reset();
    form.invoice_date.value = today();
    addInvoiceItem();
    await loadAll();
    toast(`Invoice ${result.number} generated.`);
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "Create printable invoice";
  }
});

$("#creditForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector('button[type="submit"]');
  
  try {
    button.disabled = true;
    button.textContent = "Generating...";

    const items = [...$("#creditItems").querySelectorAll(".item-row")].map((row) => ({
      product_id: row.querySelector('[name="product_id"]').value,
      quantity: row.querySelector('[name="quantity"]').value,
    }));

    if (items.length === 0) {
      toast("Please add at least one item to the credit note.");
      return;
    }

    const result = await api("/api/credit-notes", {
      method: "POST",
      body: JSON.stringify({ 
        id: form.id.value || null,
        invoice_id: form.invoice_id.value, 
        reason: form.reason.value, 
        restock: form.restock.checked,
        number: form.number.value,
        items: items,
      }),
    });
    window.open(result.pdf_path, "_blank");
    form.reset();
    form.restock.checked = true;
    $("#creditItems").innerHTML = "";
    addCreditItem();
    await loadAll();
    toast(`Credit note ${result.number} generated.`);
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "Create credit note";
  }
});

$("#statementForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector('button[type="submit"]');
  
  try {
    button.disabled = true;
    button.textContent = "Generating...";
    const result = await api("/api/statements", { 
      method: "POST", 
      body: JSON.stringify({ customer_name: form.customer_name.value }) 
    });
    window.open(result.pdf_path, "_blank");
    await loadAll();
    toast(`Statement ${result.number} generated.`);
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "Generate statement";
  }
});

$("#monthlyForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector('button[type="submit"]');
  button.disabled = true;
  button.textContent = "Generating...";
  try {
    const result = await api("/api/reports/monthly", { 
      method: "POST", 
      body: JSON.stringify({ month: form.month.value || monthNow() }) 
    });
    window.open(result.pdf_path, "_blank");
    await loadAll();
    toast(`Monthly report ${result.number} generated.`);
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "Generate monthly report";
  }
});

$("#annualForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector('button[type="submit"]');
  button.disabled = true;
  button.textContent = "Generating...";
  try {
    const result = await api("/api/reports/annual", { 
      method: "POST", 
      body: JSON.stringify({ year: form.year.value }) 
    });
    window.open(result.pdf_path, "_blank");
    await loadAll();
    toast(`Annual report ${result.number} generated.`);
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "Generate annual comparison";
  }
});

$("#invoiceForm").invoice_date.value = today();
$("#monthlyForm").month.value = monthNow();

if (state.token) {
  showApp();
  loadAll().catch(() => {
    localStorage.removeItem("ncp_token");
    state.token = "";
    showLogin();
  });
}
