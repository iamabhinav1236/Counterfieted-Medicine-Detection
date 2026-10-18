const $ = (id) => document.getElementById(id);
const views = ["overview", "verify", "registry", "qr-tools"];
let cameraStream = null;
let scanLoopActive = false;
let qrDetector = null;

function showView(name) {
  views.forEach(v => $("view-" + v).classList.toggle("hidden", v !== name));
  document.querySelectorAll(".nav-item").forEach(btn => btn.classList.toggle("active", btn.dataset.view === name));
  const titles = {overview:"Verification overview", verify:"Verify a medicine", registry:"Demo medicine registry", "qr-tools":"QR code tools"};
  $("page-title").textContent = titles[name] || "TrueDose AI";
  if (name === "registry") loadRegistry();
}

document.querySelectorAll(".nav-item").forEach(btn => btn.addEventListener("click", () => showView(btn.dataset.view)));
$("hero-verify").addEventListener("click", () => { showView("verify"); $("medicine-code-secondary").focus(); });
document.querySelectorAll("[data-code]").forEach(btn => btn.addEventListener("click", () => verifyCode(btn.dataset.code, "result-box")));
$("refresh-btn").addEventListener("click", refreshDashboard);
$("registry-refresh").addEventListener("click", loadRegistry);
$("verify-form").addEventListener("submit", e => { e.preventDefault(); verifyCode($("medicine-code").value, "result-box"); });
$("verify-form-secondary").addEventListener("submit", e => { e.preventDefault(); verifyCode($("medicine-code-secondary").value, "result-box-secondary"); });
$("generate-qr-btn").addEventListener("click", generateQr);
$("verify-selected-qr").addEventListener("click", () => verifyCode($("qr-code-select").value, "qr-scan-result"));
$("start-scan-btn").addEventListener("click", startCameraScan);
$("stop-scan-btn").addEventListener("click", stopCameraScan);
window.addEventListener("beforeunload", stopCameraScan);

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
    <div class="result-top"><div><div class="result-title">${escapeHtml(data.result)}</div><span class="status-pill ${statusClass(data.result)}">${escapeHtml(data.result)}</span></div>
    <div style="text-align:right"><div class="risk-number">${Number(data.risk_score)}<span style="font-size:11px;color:#8a999d">/100</span></div><div class="risk-label">DEMO RISK SCORE</div></div></div>
    <p class="result-reason">${escapeHtml(data.reason)}</p>
    ${m.name ? `<div class="result-fields"><div><span>Medicine</span><b>${escapeHtml(m.name)}</b></div><div><span>Manufacturer</span><b>${escapeHtml(m.manufacturer)}</b></div><div><span>Batch</span><b>${escapeHtml(m.batch)}</b></div><div><span>Recorded expiry</span><b>${escapeHtml(m.expiry)}</b></div></div>` : ""}
    <div class="result-disclaimer">${escapeHtml(data.disclaimer)}</div>`;
}
async function verifyCode(code, targetId) {
  const clean = String(code || "").trim();
  if (!clean) { toast("Enter a code first."); return; }
  const target = $(targetId);
  target.classList.remove("hidden");
  target.innerHTML = '<div class="empty-state">Checking code against the local registry…</div>';
  try {
    const response = await fetch("/api/verify", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({code:clean})});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Verification failed.");
    renderResult(data, targetId);
    $("medicine-code").value = clean.toUpperCase();
    $("medicine-code-secondary").value = clean.toUpperCase();
    await refreshDashboard();
    toast("Verification result updated.");
  } catch (err) {
    target.innerHTML = `<p class="result-reason">${escapeHtml(err.message)} Check that the Flask server is running.</p>`;
    toast("Could not complete verification.");
  }
}
async function refreshDashboard() {
  try {
    const response = await fetch("/api/stats", {cache:"no-store"});
    if (!response.ok) throw new Error("Stats unavailable");
    const data = await response.json();
    $("metric-medicines").textContent = data.medicine_records;
    $("metric-scans").textContent = data.total_scans;
    $("metric-flagged").textContent = data.flagged_scans;
    const recent = $("recent-scans");
    if (!data.recent_scans.length) recent.innerHTML = '<div class="empty-state">No scans yet. Verify a sample code to see activity here.</div>';
    else recent.innerHTML = data.recent_scans.map(s => `<div class="scan-row"><div class="scan-symbol">⌕</div><div class="scan-info"><b>${escapeHtml(s.code)}</b><small>${escapeHtml(s.scanned_at)} · Risk ${Number(s.risk_score)}/100</small></div><span class="status-pill ${statusClass(s.result)}">${escapeHtml(s.result)}</span></div>`).join("");
    setApiStatus(true);
  } catch (err) { setApiStatus(false); }
}
function setApiStatus(connected) {
  const badge = document.querySelector(".connection");
  badge.innerHTML = connected ? '<i></i> API connected' : '<i style="background:#d65e5e"></i> API disconnected';
  badge.title = connected ? "API is responding" : "Could not reach the local API";
}
async function checkApiHealth() {
  try { const response = await fetch("/api/health", {cache:"no-store"}); if (!response.ok) throw new Error(); await response.json(); setApiStatus(true); }
  catch (err) { setApiStatus(false); }
}
async function loadRegistry() {
  try {
    const response = await fetch("/api/medicines"); if (!response.ok) throw new Error();
    const rows = await response.json();
    $("registry-body").innerHTML = rows.map(m => `<tr><td><b>${escapeHtml(m.code)}</b></td><td>${escapeHtml(m.name)}</td><td>${escapeHtml(m.manufacturer)}</td><td>${escapeHtml(m.batch)}</td><td>${escapeHtml(m.expiry)}</td><td><span class="status-pill ${m.status === "clone_demo" ? "pill-warn" : "pill-good"}">${m.status === "clone_demo" ? "Clone demo scenario" : "Sample record"}</span></td></tr>`).join("");
  } catch (err) { $("registry-body").innerHTML = '<tr><td colspan="6">Could not load registry. Check the server.</td></tr>'; }
}
async function generateQr() {
  const code = $("qr-code-select").value;
  const img = $("qr-preview");
  const placeholder = $("qr-placeholder");
  const download = $("download-qr");
  try {
    const response = await fetch(`/api/qr?code=${encodeURIComponent(code)}`);
    if (!response.ok) { const data = await response.json(); throw new Error(data.error || "QR generation failed"); }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    if (img.dataset.objectUrl) URL.revokeObjectURL(img.dataset.objectUrl);
    img.dataset.objectUrl = url; img.src = url; img.classList.remove("hidden"); placeholder.classList.add("hidden");
    download.href = url; download.download = `truedose-${code.toLowerCase()}.png`; download.classList.remove("hidden");
    toast("Demo QR code generated.");
  } catch (err) { placeholder.textContent = err.message; placeholder.classList.remove("hidden"); img.classList.add("hidden"); download.classList.add("hidden"); }
}
async function startCameraScan() {
  const status = $("scanner-status");
  if (!navigator.mediaDevices?.getUserMedia) { status.textContent = "Camera access is unavailable in this browser. Use localhost in a recent Chrome or Edge browser, or enter the code manually."; return; }
  if (!("BarcodeDetector" in window)) { status.textContent = "This browser does not support built-in QR detection. Try an up-to-date Chrome or Edge browser, or use manual code entry."; return; }
  try {
    qrDetector = new BarcodeDetector({formats:["qr_code"]});
    cameraStream = await navigator.mediaDevices.getUserMedia({video:{facingMode:{ideal:"environment"}}, audio:false});
    const video = $("qr-video"); video.srcObject = cameraStream; video.classList.remove("hidden"); $("scanner-placeholder").classList.add("hidden");
    await video.play(); scanLoopActive = true; $("start-scan-btn").disabled = true; $("stop-scan-btn").disabled = false;
    status.textContent = "Camera is running. Point it at a generated TrueDose QR code.";
    scanFrame();
  } catch (err) { status.textContent = `Could not start camera: ${err.message}. Check browser camera permissions.`; stopCameraScan(); }
}
async function scanFrame() {
  if (!scanLoopActive) return;
  const video = $("qr-video");
  if (video.readyState >= 2) {
    try {
      const codes = await qrDetector.detect(video);
      if (codes.length && codes[0].rawValue) {
        const raw = codes[0].rawValue.trim();
        stopCameraScan();
        $("scanner-status").textContent = `QR read: ${raw}. Checking registry…`;
        await verifyCode(raw, "qr-scan-result");
        return;
      }
    } catch (err) { /* Continue scanning while the camera frame is not ready. */ }
  }
  if (scanLoopActive) requestAnimationFrame(scanFrame);
}
function stopCameraScan() {
  scanLoopActive = false;
  if (cameraStream) { cameraStream.getTracks().forEach(track => track.stop()); cameraStream = null; }
  const video = $("qr-video");
  if (video) { video.pause(); video.srcObject = null; video.classList.add("hidden"); }
  const placeholder = $("scanner-placeholder"); if (placeholder) placeholder.classList.remove("hidden");
  if ($("start-scan-btn")) $("start-scan-btn").disabled = false;
  if ($("stop-scan-btn")) $("stop-scan-btn").disabled = true;
}
let toastTimeout;
function toast(message) {
  const t = $("toast"); t.textContent = message; t.classList.remove("hidden");
  clearTimeout(toastTimeout); toastTimeout = setTimeout(() => t.classList.add("hidden"), 2400);
}
checkApiHealth();
refreshDashboard();
