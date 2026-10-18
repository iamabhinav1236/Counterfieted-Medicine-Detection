const $ = (id) => document.getElementById(id);
const views = ["overview", "verify", "registry"];

function showView(name) {
  views.forEach(v => $("view-" + v).classList.toggle("hidden", v !== name));
  document.querySelectorAll(".nav-item").forEach(btn => btn.classList.toggle("active", btn.dataset.view === name));
  const titles = {overview:"Verification overview", verify:"Verify a medicine", registry:"Demo medicine registry"};
  $("page-title").textContent = titles[name] || "TrueDose AI";
  if (name === "registry") loadRegistry();
}

document.querySelectorAll(".nav-item").forEach(btn => btn.addEventListener("click", () => showView(btn.dataset.view)));
$("hero-verify").addEventListener("click", () => { showView("verify"); $("medicine-code-secondary").focus(); });
document.querySelectorAll("[data-code]").forEach(btn => btn.addEventListener("click", () => verifyCode(btn.dataset.code, "result-box")));
$("refresh-btn").addEventListener("click", refreshDashboard);
$("registry-refresh").addEventListener("click", loadRegistry);

$("verify-form").addEventListener("submit", e => {
  e.preventDefault();
  verifyCode($("medicine-code").value, "result-box");
});
$("verify-form-secondary").addEventListener("submit", e => {
  e.preventDefault();
  verifyCode($("medicine-code-secondary").value, "result-box-secondary");
});

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}
function statusClass(result) {
  if (result === "REGISTERED RECORD") return "pill-good";
  if (result === "EXPIRED BATCH" || result === "REVIEW REPEATED SCANS") return "pill-warn";
  if (result === "SUSPICIOUS" || result === "UNKNOWN CODE") return "pill-bad";
  return "pill-neutral";
}
function renderResult(data, targetId) {
  const m = data.medicine || {};
  const target = $(targetId);
  target.classList.remove("hidden");
  target.innerHTML = `
    <div class="result-top">
      <div><div class="result-title">${escapeHtml(data.result)}</div><span class="status-pill ${statusClass(data.result)}">${escapeHtml(data.result)}</span></div>
      <div style="text-align:right"><div class="risk-number">${Number(data.risk_score)}<span style="font-size:11px;color:#8a999d">/100</span></div><div class="risk-label">DEMO RISK SCORE</div></div>
    </div>
    <p class="result-reason">${escapeHtml(data.reason)}</p>
    ${m.name ? `<div class="result-fields">
      <div><span>Medicine</span><b>${escapeHtml(m.name)}</b></div>
      <div><span>Manufacturer</span><b>${escapeHtml(m.manufacturer)}</b></div>
      <div><span>Batch</span><b>${escapeHtml(m.batch)}</b></div>
      <div><span>Recorded expiry</span><b>${escapeHtml(m.expiry)}</b></div>
    </div>` : ""}
    <div class="result-disclaimer">${escapeHtml(data.disclaimer)}</div>`;
}
async function verifyCode(code, targetId) {
  const clean = String(code || "").trim();
  if (!clean) { toast("Enter a code first."); return; }
  const target = $(targetId);
  target.classList.remove("hidden");
  target.innerHTML = '<div class="empty-state">Checking code against the local registry…</div>';
  try {
    const response = await fetch("/api/verify", {
      method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify({code:clean})
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Verification failed.");
    renderResult(data, targetId);
    $("medicine-code").value = clean.toUpperCase();
    $("medicine-code-secondary").value = clean.toUpperCase();
    await async function checkApiHealth() {
  const badge = document.querySelector(".connection");
  try {
    const response = await fetch("/api/health", {cache: "no-store"});
    if (!response.ok) throw new Error("Health check failed");
    const data = await response.json();
    badge.innerHTML = '<i></i> API connected';
    badge.title = data.status ? `Server status: ${data.status}` : "API is responding";
  } catch (err) {
    badge.innerHTML = '<i style="background:#d65e5e"></i> API disconnected';
    badge.title = "Could not reach the local API";
  }
}
checkApiHealth();
refreshDashboard();
    toast("Verification result updated.");
  } catch (err) {
    target.innerHTML = `<p class="result-reason">${escapeHtml(err.message)} Check that the Flask server is running.</p>`;
    toast("Could not complete verification.");
  }
}
async function refreshDashboard() {
  try {
    const response = await fetch("/api/stats");
    const data = await response.json();
    $("metric-medicines").textContent = data.medicine_records;
    $("metric-scans").textContent = data.total_scans;
    $("metric-flagged").textContent = data.flagged_scans;
    const recent = $("recent-scans");
    if (!data.recent_scans.length) {
      recent.innerHTML = '<div class="empty-state">No scans yet. Verify a sample code to see activity here.</div>';
    } else {
      recent.innerHTML = data.recent_scans.map(s => `
        <div class="scan-row">
          <div class="scan-symbol">⌕</div>
          <div class="scan-info"><b>${escapeHtml(s.code)}</b><small>${escapeHtml(s.scanned_at)} · Risk ${Number(s.risk_score)}/100</small></div>
          <span class="status-pill ${statusClass(s.result)}">${escapeHtml(s.result)}</span>
        </div>`).join("");
    }
  } catch (err) {
    document.querySelector(".connection").innerHTML = '<i style="background:#d65e5e"></i> API disconnected';
  }
}
async function loadRegistry() {
  try {
    const response = await fetch("/api/medicines");
    const rows = await response.json();
    $("registry-body").innerHTML = rows.map(m => `<tr>
      <td><b>${escapeHtml(m.code)}</b></td><td>${escapeHtml(m.name)}</td><td>${escapeHtml(m.manufacturer)}</td>
      <td>${escapeHtml(m.batch)}</td><td>${escapeHtml(m.expiry)}</td>
      <td><span class="status-pill ${m.status === "clone_demo" ? "pill-warn" : "pill-good"}">${m.status === "clone_demo" ? "Clone demo scenario" : "Sample record"}</span></td>
    </tr>`).join("");
  } catch (err) {
    $("registry-body").innerHTML = '<tr><td colspan="6">Could not load registry. Check the server.</td></tr>';
  }
}
let toastTimeout;
function toast(message) {
  const t = $("toast"); t.textContent = message; t.classList.remove("hidden");
  clearTimeout(toastTimeout); toastTimeout = setTimeout(() => t.classList.add("hidden"), 2400);
}
async function checkApiHealth() {
  const badge = document.querySelector(".connection");
  try {
    const response = await fetch("/api/health", {cache: "no-store"});
    if (!response.ok) throw new Error("Health check failed");
    const data = await response.json();
    badge.innerHTML = '<i></i> API connected';
    badge.title = data.status ? `Server status: ${data.status}` : "API is responding";
  } catch (err) {
    badge.innerHTML = '<i style="background:#d65e5e"></i> API disconnected';
    badge.title = "Could not reach the local API";
  }
}
checkApiHealth();
refreshDashboard();
